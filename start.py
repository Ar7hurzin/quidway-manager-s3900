#!/usr/bin/env python3
"""Start Quidway Manager on Windows, Linux or in a container."""
import json
import os
import re
import signal
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
SETTINGS = Path(os.environ.get('QM_SETTINGS_FILE', ROOT / 'local-settings.ps1')).expanduser()
AUTH = {'QM_AUTH_USERNAME', 'QM_AUTH_PASSWORD_HASH', 'QM_SECRET_KEY'}
ALLOWED = AUTH | {'QM_MODE', 'QM_SERIAL_PORT', 'QM_SERIAL_BAUD', 'QM_WEB_PORT', 'QM_ENABLE_CONFIG_WRITES', 'QM_ENABLE_CLI', 'QM_HTTPS_ONLY', 'QM_CONNECT_TIMEOUT', 'QM_SWITCH_HOST', 'QM_SWITCH_PORT', 'QM_SWITCH_USER', 'QM_KNOWN_HOSTS', 'QM_SSH_KEY_FILE'}

def load_settings():
    values = {}
    if SETTINGS.exists():
        if os.name == 'posix':
            SETTINGS.chmod(0o600)
        for line in SETTINGS.read_text(encoding='utf-8-sig').splitlines():
            m = re.fullmatch(r"\s*\$env:(QM_[A-Z_]+)\s*=\s*'((?:[^']|'')*)'\s*", line)
            if m and m[1] in ALLOWED:
                values[m[1]] = m[2].replace("''", "'")
    return values

def main():
    os.chdir(ROOT)
    os.umask(0o077)
    if len(sys.argv) > 1:
        if sys.argv[1] == 'configure':
            from configure_login import main as configure
            return configure()
        print('Uso: python start.py [configure]', file=sys.stderr)
        return 2
    for key, value in load_settings().items():
        os.environ.setdefault(key, value)
    os.environ.setdefault('QM_MODE', 'demo')
    os.environ.setdefault('QM_ENABLE_CONFIG_WRITES', 'false')
    os.environ.setdefault('QM_ENABLE_CLI', 'false')
    os.environ.setdefault('QM_WEB_PORT', '8787')
    if not all(os.getenv(k) for k in AUTH):
        if os.getenv('QM_CONTAINER') == '1' or not sys.stdin.isatty():
            print('Configure o login primeiro: python start.py configure (Docker: docker compose run --rm app configure).', file=sys.stderr)
            return 2
        from configure_login import main as configure
        if configure() != 0:
            return 2
        os.environ.update({k: v for k, v in load_settings().items() if k in AUTH})
    if os.environ['QM_MODE'] not in {'demo','serial','real'}:
        print('QM_MODE deve ser demo, serial ou real.', file=sys.stderr); return 2
    try:
        port = int(os.environ['QM_WEB_PORT'])
        if not 1 <= port <= 65535: raise ValueError
    except ValueError:
        print('QM_WEB_PORT inválida.', file=sys.stderr); return 2
    url = f'http://127.0.0.1:{port}'
    try:
        with urlopen(url+'/api/health', timeout=1) as resp:
            info = json.load(resp)
        if info.get('service') == 'Quidway Manager' and info.get('auth_configured'):
            print('Quidway Manager já está em execução: '+url, flush=True); return 0
        print('A porta já está ocupada por outro serviço ou por um Manager antigo. Encerre-o antes de iniciar.', file=sys.stderr); return 2
    except (URLError, OSError, ValueError, AttributeError):
        pass
    from app import app, _auth_configured
    if not _auth_configured():
        print('Credenciais locais inválidas. Execute python start.py configure.', file=sys.stderr); return 2
    from waitress import serve
    host = '0.0.0.0' if os.getenv('QM_CONTAINER') == '1' else '127.0.0.1'
    print('Quidway Manager: '+url+' | modo: '+os.environ['QM_MODE'], flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        serve(app, host=host, port=port, threads=8)
    except OSError as exc:
        print('Não foi possível abrir a porta '+str(port)+': '+str(exc), file=sys.stderr); return 2
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
