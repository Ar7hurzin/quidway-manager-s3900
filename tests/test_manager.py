"""Functional regression checks; no network or physical switch required."""
import base64
import hashlib
import os
import secrets
import unittest
from unittest.mock import patch

PASSWORD = 'local-test-only-123456'
SALT = secrets.token_bytes(16)
def b64(value): return base64.urlsafe_b64encode(value).decode().rstrip('=')
os.environ.update(QM_AUTH_USERNAME='test-admin', QM_AUTH_PASSWORD_HASH='pbkdf2_sha256$600000$'+b64(SALT)+'$'+b64(hashlib.pbkdf2_hmac('sha256', PASSWORD.encode(), SALT, 600000)), QM_SECRET_KEY=secrets.token_urlsafe(48), QM_MODE='demo', QM_ENABLE_CONFIG_WRITES='false', QM_ENABLE_CLI='false')
import app as manager

class ManagerTest(unittest.TestCase):
    def setUp(self):
        manager._login_attempts.clear()
        self.client = manager.app.test_client()
    def login(self):
        self.client.get('/login')
        with self.client.session_transaction() as sess: token = sess['csrf_token']
        self.assertEqual(self.client.post('/login', data={'username':'test-admin','password':PASSWORD,'csrf_token':token}).status_code, 302)
        with self.client.session_transaction() as sess: return sess['csrf_token']
    def test_public_health(self):
        result=self.client.get('/api/health').get_json()
        self.assertTrue(result['auth_configured']); self.assertEqual(result['mode'],'demo')
    def test_protected_pages_and_api(self):
        self.assertEqual(self.client.get('/').status_code,302)
        self.assertEqual(self.client.get('/api/status').status_code,401)
    def test_login_and_pages(self):
        login=self.client.get('/login'); self.assertIn(b'show-password',login.data)
        self.login()
        for page in ('/','/sistema','/monitoramento','/configuracao','/cli','/api/status','/api/demo','/api/config/status','/api/cli/status','/api/connections'):
            with self.subTest(page=page): self.assertEqual(self.client.get(page).status_code,200)
        self.assertEqual(self.client.get('/static/styles.css').status_code,200)
        self.assertEqual(self.client.get('/static/login.js').status_code,200)
    def test_login_csrf_and_logout(self):
        self.assertEqual(self.client.post('/login',data={'username':'test-admin','password':PASSWORD}).status_code,400)
        token=self.login()
        self.assertEqual(self.client.post('/logout').status_code,400)
        self.assertEqual(self.client.post('/logout',data={'csrf_token':token}).status_code,302)
        self.assertEqual(self.client.get('/api/demo').status_code,401)
    def test_invalid_credentials_and_rate_limit(self):
        self.client.get('/login')
        with self.client.session_transaction() as sess: token=sess['csrf_token']
        for _ in range(8): self.assertEqual(self.client.post('/login',data={'username':'test-admin','password':'wrong','csrf_token':token}).status_code,401)
        self.assertEqual(self.client.post('/login',data={'username':'test-admin','password':PASSWORD,'csrf_token':token}).status_code,429)
    def test_demo_and_write_guards(self):
        token=self.login(); headers={'X-CSRF-Token':token}
        self.assertEqual(self.client.post('/api/refresh',json={},headers=headers).status_code,200)
        self.assertEqual(self.client.post('/api/config/apply',json={},headers=headers).status_code,409)
        with patch.dict(os.environ,{'QM_MODE':'serial'}):
            self.assertEqual(self.client.post('/api/config/apply',json={},headers=headers).status_code,403)
            self.assertEqual(self.client.post('/api/cli/open',json={},headers=headers).status_code,403)
            self.assertFalse(self.client.get('/api/cli/status').get_json()['available'])
    def test_serial_exclusion(self):
        token=self.login(); headers={'X-CSRF-Token':token}
        with patch.dict(os.environ,{'QM_MODE':'serial'}):
            with manager._serial_request_lock:
                self.assertEqual(self.client.post('/api/refresh',json={},headers=headers).status_code,409)
    def test_session_expiry(self):
        self.login()
        with self.client.session_transaction() as sess: sess['qm_login_time']=0
        self.assertEqual(self.client.get('/api/status').status_code,401)
    def test_secure_headers(self):
        response=self.client.get('/login')
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(response.headers['X-Frame-Options'],'DENY')

if __name__ == '__main__': unittest.main()
