"""Quidway Manager: local, read-only planning MVP for Quidway S3900."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, send_from_directory
from waitress import serve

from ssh_readonly import SwitchSettings, SSHReadOnlyError, collect_diagnostics

ROOT = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")
app.config.update(JSON_SORT_KEYS=False, MAX_CONTENT_LENGTH=32 * 1024)


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


@app.get("/")
def index():
    return send_from_directory(ROOT / "static", "index.html")


@app.get("/api/status")
def status():
    current_mode = mode()
    if current_mode != "real":
        return jsonify({"mode": "demo", "label": "Demonstração", "ready": True, "message": "Dados inventados para demonstrar a interface. Nada foi lido do switch."})
    cfg = switch_settings()
    known_hosts_ok = bool(cfg.known_hosts_file and Path(cfg.known_hosts_file).expanduser().is_file())
    key_ok = bool(cfg.key_filename and Path(cfg.key_filename).expanduser().is_file())
    has_one_credential = bool(cfg.password) != bool(cfg.key_filename)
    ready = bool(cfg.host and cfg.username and has_one_credential and known_hosts_ok and (not cfg.key_filename or key_ok))
    return jsonify({
        "mode": "real", "label": "Leitura SSH", "ready": ready,
        "message": "Pronto para consulta somente leitura." if ready else "Configuração incompleta: informe host e usuário, exatamente uma credencial, e um arquivo known_hosts existente nas variáveis QM_*.",
    })


@app.get("/api/demo")
def demo():
    if mode() == "real":
        return jsonify({"error": "Os dados de demonstração estão desativados no modo real."}), 409
    return jsonify({**DEMO_DATA, "mode": "demo", "collected_at": datetime.now(timezone.utc).isoformat()})


@app.post("/api/refresh")
def refresh():
    if mode() != "real":
        return jsonify({
            "mode": "demo", "ok": True,
            "message": "Demonstração atualizada. Os valores continuam sendo fictícios.",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "outputs": [],
        })
    try:
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


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "Quidway Manager", "mode": mode()})


if __name__ == "__main__":
    # Loopback only. Use Tailscale Serve for private remote access; do not bind to 0.0.0.0.
    port = int(os.getenv("QM_WEB_PORT", "8787"))
    serve(app, host="127.0.0.1", port=port, threads=8)
