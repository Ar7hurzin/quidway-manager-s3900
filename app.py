"""Quidway Manager: local monitoring and serial-console management for Quidway S3900."""
from __future__ import annotations

import os
import atexit
import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from io import BytesIO
from functools import wraps
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, render_template_string, request, send_file, session, url_for
from waitress import serve

from serial_readonly import SerialSettings, SerialReadOnlyError, collect_serial_diagnostics
from serial_config import execute_plan, export_current_configuration
from serial_cli import SerialCLIError, cli_session
from ssh_readonly import SwitchSettings, SSHReadOnlyError, collect_diagnostics
from switch_config import ConfigurationError, build_plan, consume_preview, issue_preview
from virtual_switch import snapshot as virtual_snapshot
from physical_dashboard import PhysicalDashboard

physical_dashboard = PhysicalDashboard()

atexit.register(cli_session.shutdown)

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")
app.config.update(
    JSON_SORT_KEYS=False,
    MAX_CONTENT_LENGTH=32 * 1024,
    SECRET_KEY=os.getenv("QM_SECRET_KEY") or secrets.token_urlsafe(48),
    SESSION_COOKIE_NAME="qm_virtual_session" if os.getenv("QM_MODE") == "virtual" else "session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("QM_HTTPS_ONLY", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)
AUTH_USERNAME = os.getenv("QM_AUTH_USERNAME", "").strip()
AUTH_PASSWORD_HASH = os.getenv("QM_AUTH_PASSWORD_HASH", "").strip()
ACTIVE_SESSION_TTL_SECONDS = 5 * 60
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
_active_sessions: dict[str, tuple[str, float]] = {}
_active_sessions_lock = threading.Lock()
_serial_request_lock = threading.Lock()
_login_attempts = {}
_login_lock = threading.Lock()

def _login_rate_allowed():
    now = time.monotonic()
    key = request.remote_addr or "local"
    with _login_lock:
        for k, (_, until) in list(_login_attempts.items()):
            if until <= now: _login_attempts.pop(k, None)
        count, until = _login_attempts.get(key, (0, now + 60))
        if count >= 8 or (key not in _login_attempts and len(_login_attempts) >= 1024):
            return False
        _login_attempts[key] = (count + 1, until)
        return True

def _serial_exclusive(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        if mode() not in {"serial", "virtual"}: return fn(*args, **kwargs)
        if not _serial_request_lock.acquire(blocking=False):
            return jsonify({"ok": False, "error": "Outra operação serial está em andamento. Aguarde e tente novamente."}), 409
        try: return fn(*args, **kwargs)
        finally: _serial_request_lock.release()
    return run


LOGIN_PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="csrf-token" content="{{ csrf_token }}"><title>Entrar · Quidway Manager</title><link rel="stylesheet" href="/static/styles.css?v=20261113-light-1"></head><body class="login-body"><main class="login-shell"><section class="login-hero"><div><div class="brand-mark">Q</div><h2>Gerencie sua infraestrutura de rede</h2><p>Quidway Manager · Huawei Quidway S3900<br>Monitoramento, diagnóstico e configuração em um único console.</p></div><small>CONSOLE DE GERENCIAMENTO PRIVADO</small></section><section class="login-card"><p class="card-overline">ACESSO PRIVADO</p><h1>Entrar</h1><p class="login-note">Use a conta local configurada neste computador.</p>{% if error %}<p class="login-error" role="alert">{{ error }}</p>{% endif %}<form method="post" action="/login"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><label>Usuário<input name="username" autocomplete="username" required autofocus maxlength="64"></label><label>Senha<div class="password-field"><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="1024"><button id="show-password" type="button" class="button button-secondary" aria-pressed="false">Mostrar</button></div></label><button class="button button-primary" type="submit">Entrar</button></form><small>A sessão termina depois de 8 horas.</small></section></main><script src="/static/login.js?v=1" defer></script></body></html>"""
SETUP_REQUIRED_PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Configurar acesso · Quidway Manager</title><link rel="stylesheet" href="/static/styles.css?v=20261113-light-1"></head><body class="login-body"><main class="card setup-card"><h1>Configure o login antes de iniciar</h1><p>O Quidway Manager está bloqueado até criar uma conta local. Execute o comando correspondente ao seu ambiente e reinicie o servidor.</p><p>Linux: <code>.venv/bin/python configure_login.py</code></p><p>Windows: <code>.\\configure-login.ps1</code></p><p>Docker: <code>docker compose run --rm app configure</code></p></main></body></html>"""


def _render_manager():
    template = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    return render_template_string(template, csrf_token=token)


def _valid_password_hash(encoded: str) -> bool:
    try:
        algorithm, rounds_text, salt_text, expected_text = encoded.split("$", 3)
        rounds = int(rounds_text)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= rounds <= 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(expected_text + "=" * (-len(expected_text) % 4))
        return len(salt) >= 16 and len(expected) == 32
    except (ValueError, TypeError):
        return False


def _check_password(password: str) -> bool:
    if not _valid_password_hash(AUTH_PASSWORD_HASH):
        return False
    _, rounds_text, salt_text, expected_text = AUTH_PASSWORD_HASH.split("$", 3)
    rounds = int(rounds_text)
    salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
    expected = base64.urlsafe_b64decode(expected_text + "=" * (-len(expected_text) % 4))
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=32)
    return hmac.compare_digest(actual, expected)


def _auth_configured() -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", AUTH_USERNAME) and _valid_password_hash(AUTH_PASSWORD_HASH) and os.getenv("QM_SECRET_KEY"))


def _safe_token_equal(supplied: object, expected: object) -> bool:
    return (isinstance(supplied, str) and isinstance(expected, str) and bool(expected)
            and supplied.isascii() and expected.isascii() and hmac.compare_digest(supplied, expected))


def _touch_active_session() -> None:
    session_id = session.get("qm_session_id")
    username = session.get("qm_user")
    if not isinstance(session_id, str) or username != AUTH_USERNAME:
        return
    now = time.time()
    with _active_sessions_lock:
        expired = [key for key, (_, last_seen) in _active_sessions.items() if now - last_seen > ACTIVE_SESSION_TTL_SECONDS]
        for key in expired:
            _active_sessions.pop(key, None)
        _active_sessions[session_id] = (username, now)


@app.before_request
def require_login_and_csrf():
    if request.endpoint == "health" or request.endpoint == "static":
        return None
    if not _auth_configured():
        return SETUP_REQUIRED_PAGE, 503

    if request.path == "/login":
        if request.method == "POST":
            supplied = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not _safe_token_equal(supplied, expected):
                return "Token de formulário inválido. Atualize a página e tente novamente.", 400
        return None

    if session.get("qm_user") == AUTH_USERNAME:
        logged_in_at = session.get("qm_login_time")
        if not isinstance(logged_in_at, (int, float)) or time.time() - logged_in_at > SESSION_MAX_AGE_SECONDS:
            session_id = session.get("qm_session_id")
            if isinstance(session_id, str):
                with _active_sessions_lock:
                    _active_sessions.pop(session_id, None)
            session.clear()
    if session.get("qm_user") != AUTH_USERNAME:
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Sessão encerrada. Entre novamente."}), 401
        return redirect(url_for("login"))

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not _safe_token_equal(supplied, expected):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Token de segurança inválido. Atualize a página e tente novamente."}), 400
            return "Token de segurança inválido. Atualize a página e tente novamente.", 400
    _touch_active_session()
    return None


@app.after_request
def prevent_caching(response):
    # Avoid retaining switch diagnostics or device metadata in shared browser caches.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response

DEMO_DATA: dict[str, Any] = {
    "device": {"name": "S3900 · demonstração", "model": "Quidway S3900 (exemplo)", "version": "Não coletada", "uptime": "Não coletado"},
    "summary": {"ports_up": "18 / 24", "vlans": "4", "alerts": "2", "last_backup": "Não realizado"},
    "ports": [
        {"number": n, "state": "up" if n not in {3, 6, 10, 13, 17, 20} else "down", "label": "Ativa" if n not in {3, 6, 10, 13, 17, 20} else "Sem link"}
        for n in range(1, 25)
    ],
    "vlans": [
        {"id": 1, "name": "default", "members": "23–24", "note": "Exemplo"},
        {"id": 10, "name": "ADMIN", "members": "23 (tagged), 1–4", "note": "Exemplo"},
        {"id": 20, "name": "USUARIOS", "members": "23 (tagged), 5–16", "note": "Exemplo"},
        {"id": 99, "name": "GESTAO", "members": "24 (tagged), 17–18", "note": "Exemplo"},
    ],
    "events": [
        {"time": "09:42", "title": "Backup ilustrativo", "kind": "info"},
        {"time": "09:18", "title": "Porta 07 mudou", "kind": "warning"},
        {"time": "08:55", "title": "Sessão SSH iniciada", "kind": "info"},
        {"time": "08:10", "title": "Alerta de link", "kind": "warning"},
    ],
}


def mode() -> str:
    return os.getenv("QM_MODE", "demo").strip().lower()


def switch_settings() -> SwitchSettings:
    return SwitchSettings.from_environment()


def serial_settings() -> SerialSettings:
    if mode() == "virtual":
        return SerialSettings("S3900-Virtual", 9600, 3, virtual=True)
    return SerialSettings.from_environment()


def config_writes_enabled() -> bool:
    if mode() == "virtual": return True
    return os.getenv("QM_ENABLE_CONFIG_WRITES", "false").strip().lower() in {"1", "true", "yes", "on"}


@app.get("/")
def index():
    return _render_manager()


@app.get("/monitoramento")
def monitoring_page():
    return _render_manager()


@app.get("/sistema")
def system_page():
    return _render_manager()


@app.get("/configuracao")
def configuration_page():
    return _render_manager()


@app.get("/cli")
def cli_page():
    return _render_manager()


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("qm_user") == AUTH_USERNAME:
        return redirect(url_for("index"))
    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    if request.method == "POST":
        if not _login_rate_allowed():
            response = app.make_response((render_template_string(LOGIN_PAGE, csrf_token=csrf_token, error="Muitas tentativas. Aguarde um minuto e tente novamente."), 429))
            response.headers["Retry-After"] = "60"
            return response
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        username_matches = username.isascii() and hmac.compare_digest(username, AUTH_USERNAME)
        password_matches = len(password) <= 1024 and _check_password(password)
        if username_matches and password_matches:
            with _login_lock:
                _login_attempts.pop(request.remote_addr or "local", None)
            session.clear()
            session.permanent = True
            session["qm_user"] = AUTH_USERNAME
            session["qm_session_id"] = secrets.token_urlsafe(24)
            session["qm_login_time"] = time.time()
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("index"))
        return render_template_string(LOGIN_PAGE, csrf_token=csrf_token, error="Usuário ou senha incorretos."), 401
    return render_template_string(LOGIN_PAGE, csrf_token=csrf_token, error="")


@app.post("/logout")
def logout():
    session_id = session.get("qm_session_id")
    if isinstance(session_id, str):
        with _active_sessions_lock:
            _active_sessions.pop(session_id, None)
    session.clear()
    return redirect(url_for("login"))


def _cli_request_allowed() -> bool:
    if not request.is_json:
        return False
    origin = request.headers.get("Origin")
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc == request.host


@app.get("/api/cli/status")
def cli_status():
    return jsonify({"mode": mode(), "available": mode() in {"serial", "virtual"} and cli_enabled(), "connected": cli_session.connected, "message": "CLI virtual habilitada; sem hardware." if mode() == "virtual" else "CLI habilitada." if cli_enabled() else "CLI bloqueada. Defina QM_ENABLE_CLI=true somente em ambiente autorizado."})


def cli_enabled():
    if mode() == "virtual": return True
    return os.getenv("QM_ENABLE_CLI", "false").lower() == "true"


@app.post("/api/cli/open")
@_serial_exclusive
def cli_open():
    if not cli_enabled():
        return jsonify({"ok": False, "error": "CLI bloqueada. Defina QM_ENABLE_CLI=true somente após validar o ambiente."}), 403
    if not _cli_request_allowed():
        return jsonify({"ok": False, "error": "Requisição bloqueada: use a interface local do Quidway Manager."}), 403
    if mode() not in {"serial", "virtual"}:
        return jsonify({"ok": False, "error": "O terminal CLI integrado exige QM_MODE=serial e o cabo console conectado a este computador."}), 409
    try:
        session = cli_session.open(serial_settings())
        return jsonify({"ok": True, **session})
    except (SerialCLIError, SerialReadOnlyError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.post("/api/cli/send")
@_serial_exclusive
def cli_send():
    if not cli_enabled():
        return jsonify({"ok": False, "error": "CLI bloqueada."}), 403
    if not _cli_request_allowed():
        return jsonify({"ok": False, "error": "Requisição bloqueada: origem inválida."}), 403
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict): return jsonify({"ok": False, "error": "JSON inválido."}), 400
        result = cli_session.send(payload.get("token"), payload.get("command"))
        return jsonify({"ok": True, **result})
    except SerialCLIError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/cli/close")
@_serial_exclusive
def cli_close():
    if not _cli_request_allowed():
        return jsonify({"ok": False, "error": "Requisição bloqueada: origem inválida."}), 403
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict): return jsonify({"ok": False, "error": "JSON inválido."}), 400
        cli_session.close(payload.get("token"))
        if mode() == "serial": physical_dashboard.invalidate()
        return jsonify({"ok": True, "message": "Sessão virtual encerrada." if mode() == "virtual" else "Sessão serial encerrada; porta COM liberada."})
    except SerialCLIError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/status")
def status():
    current_mode = mode()
    if current_mode == "virtual":
        return jsonify({"mode": "virtual", "label": "S3900 Virtual", "ready": True, "message": "SIMULAÇÃO: alterações persistem apenas no switch virtual. Nenhum equipamento físico é acessado."})
    if current_mode == "serial":
        try:
            cfg = serial_settings()
        except SerialReadOnlyError as exc:
            return jsonify({"mode": "serial", "label": "Console serial", "ready": False, "message": str(exc)})
        return jsonify({
            "mode": "serial", "label": "Console serial", "ready": bool(cfg.port),
            "message": f"Console serial em {cfg.port} a {cfg.baud_rate} baud. Diagnósticos são somente leitura; a CLI exige autorização explícita. Feche outros programas que usam a porta antes de conectar.",
        })
    if current_mode != "real":
        return jsonify({"mode": "demo", "label": "Demonstração", "ready": True, "message": "Dados inventados para demonstrar a interface. Nada foi lido do switch."})
    try:
        cfg = switch_settings()
    except SSHReadOnlyError as exc:
        return jsonify({"mode": "real", "label": "Leitura SSH", "ready": False, "message": str(exc)})
    known_hosts_ok = bool(cfg.known_hosts_file and Path(cfg.known_hosts_file).expanduser().is_file())
    key_ok = bool(cfg.key_filename and Path(cfg.key_filename).expanduser().is_file())
    has_one_credential = bool(cfg.password) != bool(cfg.key_filename)
    ready = bool(cfg.host and cfg.username and has_one_credential and known_hosts_ok and (not cfg.key_filename or key_ok))
    return jsonify({
        "mode": "real", "label": "Leitura SSH", "ready": ready,
        "message": "Pronto para consulta somente leitura." if ready else "Configuração incompleta: informe host e usuário, exatamente uma credencial, e um arquivo known_hosts existente nas variáveis QM_*.",
    })


@app.get("/api/virtual/state")
def virtual_state():
    if mode() != "virtual":
        return jsonify({"error": "Disponível somente no modo virtual."}), 409
    try:
        return jsonify(virtual_snapshot())
    except (OSError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 503


@app.get("/api/demo")
def demo():
    if mode() in {"real", "serial", "virtual"}:
        return jsonify({"error": "Os dados de demonstração estão desativados no modo real."}), 409
    return jsonify({**DEMO_DATA, "mode": "demo", "collected_at": datetime.now(timezone.utc).isoformat()})


@app.post("/api/refresh")
@_serial_exclusive
def refresh():
    current_mode = mode()
    if current_mode == "virtual":
        try:
            outputs = [] if cli_session.connected else collect_serial_diagnostics(serial_settings())
            return jsonify({"mode": "virtual", "ok": True, "message": "Estado do S3900 Virtual atualizado. Nenhum switch físico consultado.", "collected_at": datetime.now(timezone.utc).isoformat(), "outputs": outputs, "snapshot": virtual_snapshot()})
        except (SerialReadOnlyError, OSError, ValueError) as exc:
            return jsonify({"mode": "virtual", "ok": False, "message": str(exc), "outputs": []}), 503
    if current_mode not in {"real", "serial"}:
        return jsonify({
            "mode": "demo", "ok": True,
            "message": "Demonstração atualizada. Os valores continuam sendo fictícios.",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "outputs": [],
        })
    try:
        if current_mode == "serial":
            if cli_session.connected:
                return jsonify({"mode": "serial", "ok": False, "message": "Encerre a sessão CLI antes de abrir uma nova consulta serial.", "collected_at": datetime.now(timezone.utc).isoformat(), "outputs": []}), 409
            cfg = serial_settings()
            payload = request.get_json(silent=True)
            automatic = isinstance(payload, dict) and payload.get("automatic") is True
            reading = physical_dashboard.collect(cfg, force=not automatic)
            return jsonify({
                "mode": "serial", "ok": True,
                "message": "Leitura física concluída. Portas e detalhes de VLAN são coletados em lotes.",
                **reading,
            })
        cfg = switch_settings()
        outputs = collect_diagnostics(cfg)
        return jsonify({
            "mode": "real", "ok": True,
            "message": "Consultas concluídas. Saídas brutas; parser e compatibilidade deste firmware ainda precisam ser validados.",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "outputs": outputs,
        })
    except SSHReadOnlyError as exc:
        return jsonify({
            "mode": "real", "ok": False,
            "message": str(exc),
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "outputs": [],
        }), 503
    except SerialReadOnlyError as exc:
        return jsonify({
            "mode": "serial", "ok": False,
            "message": str(exc),
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "outputs": [],
        }), 503


@app.get("/api/dashboard")
def dashboard_cache():
    if mode() != "serial":
        return jsonify({"error": "Cache disponível somente no modo serial físico."}), 409
    result = physical_dashboard.cached()
    return jsonify({"mode": "serial", "ok": True, "cli_busy": cli_session.connected, **result})


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "Quidway Manager", "mode": mode(), "auth_required": True, "auth_configured": _auth_configured()})


@app.get("/api/connections")
def connections():
    now = time.time()
    with _active_sessions_lock:
        expired = [key for key, (_, last_seen) in _active_sessions.items() if now - last_seen > ACTIVE_SESSION_TTL_SECONDS]
        for key in expired:
            _active_sessions.pop(key, None)
        sessions_by_user: dict[str, list[float]] = {}
        for username, last_seen in _active_sessions.values():
            sessions_by_user.setdefault(username, []).append(last_seen)
    users = [
        {
            "username": username,
            "sessions": len(timestamps),
            "last_activity": datetime.fromtimestamp(max(timestamps), timezone.utc).isoformat(),
        }
        for username, timestamps in sorted(sessions_by_user.items())
    ]
    return jsonify({
        "manager": {"active_sessions": sum(user["sessions"] for user in users), "active_users": len(users), "users": users},
        "switch": {
            "status": "not_collected",
            "devices": None,
            "message": "A leitura de MAC/endereço de porta ainda não foi validada neste firmware. Nenhuma contagem de dispositivos foi inferida.",
        },
        "active_window_seconds": ACTIVE_SESSION_TTL_SECONDS,
    })


@app.get("/api/config/status")
def configuration_status():
    serial_mode = mode() in {"serial", "virtual"}
    enabled = serial_mode and config_writes_enabled()
    return jsonify({
        "available": serial_mode,
        "writes_enabled": enabled,
        "message": (
            "Configuração virtual habilitada: nenhuma alteração física." if mode() == "virtual" else
            "A configuração por console está habilitada com prévia por operação."
            if enabled else
            "Prévia e validação disponíveis. O envio de mudanças está bloqueado por QM_ENABLE_CONFIG_WRITES=false."
            if serial_mode else
            "A configuração só está disponível pelo console serial local neste perfil."
        ),
        "features": ["VLANs", "Portas access/trunk/hybrid", "Descrição e estado de portas", "Nome do equipamento", "IP da interface VLAN", "Rotas estáticas IPv4", "Backup de configuração", "Salvar configuração"],
    })


@app.post("/api/config/preview")
def configuration_preview():
    if mode() not in {"serial", "virtual"}:
        return jsonify({"error": "A configuração requer QM_MODE=serial e o cabo console local."}), 409
    try:
        payload = request.get_json(silent=True)
        plan = build_plan(payload)
        if mode() == "virtual":
            from serial_config import _verify_vlan_preconditions
            from serial_readonly import open_connection
            with open_connection(serial_settings()) as connection:
                _verify_vlan_preconditions(connection, serial_settings(), plan)
        token = issue_preview(plan, owner=session.get("qm_session_id"))
        return jsonify({
            "ok": True, "token": token, "action": plan.action,
            "summary": plan.summary, "commands": list(plan.commands),
            "warnings": (["SIMULAÇÃO: somente o estado virtual será alterado."] if mode() == "virtual" else list(plan.warnings)),
            "expires_in_seconds": 300,
        })
    except ConfigurationError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/config/apply")
@_serial_exclusive
def configuration_apply():
    if mode() not in {"serial", "virtual"}:
        return jsonify({"ok": False, "error": "A configuração requer o modo serial local."}), 409
    if not config_writes_enabled():
        return jsonify({"ok": False, "error": "Envio de mudanças está bloqueado. Habilite QM_ENABLE_CONFIG_WRITES somente após revisar a prévia e os comandos para o firmware instalado."}), 403
    if cli_session.connected:
        return jsonify({"ok": False, "error": "Encerre a sessão CLI antes de aplicar uma configuração pela área guiada."}), 409
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict): return jsonify({"ok": False, "error": "JSON inválido."}), 400
        plan = consume_preview(payload.get("token"), owner=session.get("qm_session_id"))
        if mode() == "serial": physical_dashboard.invalidate(plan)
        outputs = execute_plan(serial_settings(), plan)
        return jsonify({
            "ok": True, "summary": plan.summary,
            "message": "Operação aplicada ao S3900 Virtual." if mode() == "virtual" else "Operação enviada. Revise os retornos do switch; as mudanças em execução ainda podem precisar ser salvas.",
            "outputs": outputs,
            **({"snapshot": virtual_snapshot()} if mode() == "virtual" else {}),
        })
    except (ConfigurationError, SerialReadOnlyError, OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/config/backup")
@_serial_exclusive
def configuration_backup():
    if mode() not in {"serial", "virtual"}:
        return jsonify({"ok": False, "error": "O backup requer o modo serial local."}), 409
    if cli_session.connected:
        return jsonify({"ok": False, "error": "Encerre a sessão CLI antes de exportar o backup serial."}), 409
    try:
        content, filename = export_current_configuration(serial_settings())
        return send_file(
            BytesIO(content), mimetype="text/plain; charset=utf-8",
            as_attachment=True, download_name=filename,
            max_age=0,
        )
    except (ConfigurationError, SerialReadOnlyError, OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


if __name__ == "__main__":
    # Native is loopback-only. Container publishes host loopback via Compose.
    port = int(os.getenv("QM_WEB_PORT", "8787"))
    host = "0.0.0.0" if os.getenv("QM_CONTAINER") == "1" else "127.0.0.1"
    serve(app, host=host, port=port, threads=8)
