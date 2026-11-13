"""Interactively configure the local Quidway Manager login without storing a plaintext password."""
from __future__ import annotations

import base64
import getpass
import hashlib
import os
import tempfile
import re
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SETTINGS = Path(os.environ.get("QM_SETTINGS_FILE", ROOT / "local-settings.ps1")).expanduser()
ITERATIONS = 600_000


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    print("Configuração do login local do Quidway Manager")
    username = input("Usuário (3 a 64 letras, números, ponto, hífen ou sublinhado): ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
        print("Nome de usuário inválido.")
        return 2

    password = getpass.getpass("Senha (12 a 1024 caracteres): ")
    if not 12 <= len(password) <= 1024:
        print("A senha precisa ter de 12 a 1024 caracteres.")
        return 2
    repeated = getpass.getpass("Digite a senha novamente: ")
    if not secrets.compare_digest(password.encode("utf-8"), repeated.encode("utf-8")):
        print("As senhas não coincidem.")
        return 2

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS, dklen=32)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    encoded = f"pbkdf2_sha256${ITERATIONS}${b64(salt)}${b64(digest)}"
    secret_key = secrets.token_urlsafe(48)

    existing = SETTINGS.read_text(encoding="utf-8-sig") if SETTINGS.exists() else "# Configurações locais do Quidway Manager\n"
    lines = [
        line for line in existing.splitlines()
        if not re.match(r"\s*\$env:QM_(AUTH_USERNAME|AUTH_PASSWORD_HASH|SECRET_KEY)\s*=", line, re.I)
    ]
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend((
        "",
        "# Login local do Quidway Manager; senha armazenada somente como hash PBKDF2.",
        f"$env:QM_AUTH_USERNAME = {ps_quote(username)}",
        f"$env:QM_AUTH_PASSWORD_HASH = {ps_quote(encoded)}",
        f"$env:QM_SECRET_KEY = {ps_quote(secret_key)}",
        "",
    ))
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".qm-login-", dir=SETTINGS.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        os.replace(temporary, SETTINGS)
        if os.name == "posix":
            SETTINGS.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Login configurado para o usuário {username!r} em local-settings.ps1. A senha não foi gravada em texto puro.")
    print("Reinicie o Quidway Manager: python start.py (Windows: .\\start.ps1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
