"""Real loopback server smoke test, using isolated settings and a free port."""
import base64, hashlib, json, os, secrets, socket, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
class LauncherTest(unittest.TestCase):
    def test_launch_duplicate_and_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'local-settings.ps1'; salt=secrets.token_bytes(16)
            b64=lambda x: base64.urlsafe_b64encode(x).decode().rstrip('=')
            digest=hashlib.pbkdf2_hmac('sha256',b'launcher-test-only-123456',salt,600000)
            p.write_text("$env:QM_AUTH_USERNAME = 'launcher-test'\n$env:QM_AUTH_PASSWORD_HASH = 'pbkdf2_sha256$600000$"+b64(salt)+'$'+b64(digest)+"'\n$env:QM_SECRET_KEY = '"+secrets.token_urlsafe(48)+"'\n",encoding='utf-8')
            with socket.socket() as s:
                s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
            env={k:v for k,v in os.environ.items() if not k.startswith('QM_')}; env.update(QM_SETTINGS_FILE=str(p),QM_WEB_PORT=str(port),QM_MODE='demo')
            cmd=[sys.executable,str(ROOT/'start.py')]
            proc=subprocess.Popen(cmd,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                base=f'http://127.0.0.1:{port}'
                for _ in range(60):
                    try:
                        with urlopen(base+'/api/health',timeout=1) as response: health=json.load(response)
                        break
                    except OSError: time.sleep(.15)
                else: self.fail('Server did not start')
                self.assertTrue(health['auth_configured']); self.assertEqual(health['mode'],'demo')
                with urlopen(base+'/login',timeout=3) as response: self.assertIn(b'show-password',response.read())
                duplicate=subprocess.run(cmd,env=env,capture_output=True,text=True,encoding='utf-8',timeout=10)
                self.assertEqual(duplicate.returncode,0,duplicate.stderr)
            finally:
                proc.terminate()
                try: proc.wait(timeout=8)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
