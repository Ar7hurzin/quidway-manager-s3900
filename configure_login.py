"""Interactively configure SSO (OpenID Connect) login for Quidway Manager.

No user password is requested or stored: people sign in at the identity
provider with two-factor authentication. This file only records which
provider and which e-mails are trusted, plus a random session-signing key.
"""
from __future__ import annotations

import getpass
import os
import re
import secrets
import tempfile
from pathlib import Path

from oidc_auth import DEFAULT_MFA_AMR, OIDCSettings, _csv

ROOT = Path(__file__).resolve().parent
SETTINGS = Path(os.environ.get("QM_SETTINGS_FILE", ROOT / "local-settings.ps1")).expanduser()
# Old password-based keys are removed so no password hash remains on disk.
MANAGED = re.compile(r"\s*\$env:QM_(AUTH_USERNAME|AUTH_PASSWORD_HASH|SECRET_KEY|OIDC_[A-Z_]+)\s*=", re.I)


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def main() -> int:
    print("Configuração do login SSO (OpenID Connect) do Quidway Manager")
    print("Nenhuma senha de usuário é pedida nem gravada: o login e o 2FA acontecem no provedor.\n")
    port = os.getenv("QM_WEB_PORT", "8787").strip() or "8787"
    issuer = ask("URL do emissor (issuer), ex. https://login.microsoftonline.com/<tenant>/v2.0")
    client_id = ask("Client ID do aplicativo registrado no provedor")
    client_secret = getpass.getpass("Client secret (Enter para cliente público com PKCE): ").strip()
    emails = ask("E-mails autorizados, separados por vírgula")
    redirect_uri = ask("Redirect URI cadastrada no provedor", f"http://127.0.0.1:{port}/auth/callback")
    require_mfa = ask("Exigir comprovação de 2FA no token? (S/n)", "S").lower() not in {"n", "nao", "não", "no"}

    settings = OIDCSettings(
        issuer=issuer, client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri,
        allowed_emails=tuple(e.lower() for e in _csv(emails)), require_mfa=require_mfa,
    )
    problems = settings.problems()
    if problems:
        print("\nConfiguração não gravada:")
        for problem in problems:
            print(" - " + problem)
        return 2
    if not require_mfa:
        print("\nATENÇÃO: o app NÃO vai conferir o 2FA no token. Garanta que o provedor exige 2FA "
              "para todas as contas autorizadas (ex. política do Google Workspace).")

    existing = SETTINGS.read_text(encoding="utf-8-sig") if SETTINGS.exists() else "# Configurações locais do Quidway Manager\n"
    lines = [line for line in existing.splitlines() if not MANAGED.match(line)]
    lines = [line for line in lines
             if not re.match(r"\s*# (Login local do Quidway Manager|Login SSO \(OpenID Connect\)|Valores 'amr' aceitos)", line)]
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend((
        "",
        "# Login SSO (OpenID Connect). Nenhuma senha de usuário é armazenada.",
        f"$env:QM_OIDC_ISSUER = {ps_quote(settings.issuer)}",
        f"$env:QM_OIDC_CLIENT_ID = {ps_quote(settings.client_id)}",
        *( [f"$env:QM_OIDC_CLIENT_SECRET = {ps_quote(settings.client_secret)}"] if settings.client_secret else [] ),
        f"$env:QM_OIDC_REDIRECT_URI = {ps_quote(settings.redirect_uri)}",
        f"$env:QM_OIDC_ALLOWED_EMAILS = {ps_quote(','.join(settings.allowed_emails))}",
        f"$env:QM_OIDC_REQUIRE_MFA = {ps_quote('true' if require_mfa else 'false')}",
        f"# Valores 'amr' aceitos como 2FA (padrão: {DEFAULT_MFA_AMR}). Keycloak/Authentik podem usar QM_OIDC_ACCEPTED_ACR.",
        f"$env:QM_SECRET_KEY = {ps_quote(secrets.token_urlsafe(48))}",
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
    print(f"\nSSO configurado em {SETTINGS.name} para {len(settings.allowed_emails)} conta(s).")
    print(f"Cadastre no provedor a redirect URI: {settings.redirect_uri}")
    print("Escopos usados: openid email profile. Reinicie o Quidway Manager: python start.py (Windows: .\\start.ps1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
