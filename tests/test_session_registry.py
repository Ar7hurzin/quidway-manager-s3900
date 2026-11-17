"""Revocation is authorization, not the five-minute presence counter."""
import unittest
from unittest.mock import patch

from session_registry import SessionRegistry


class SessionRegistryTest(unittest.TestCase):
    def test_expiration_credential_rotation_and_unknown_id(self):
        registry = SessionRegistry()
        with patch('session_registry.time.time', return_value=100):
            token = registry.issue('reader', 'credential-v1', 60)
            self.assertTrue(registry.valid(token, 'reader', 'credential-v1'))
            self.assertFalse(registry.valid(token, 'other', 'credential-v1'))
            self.assertFalse(registry.valid(token, 'reader', 'credential-v2'))
            self.assertFalse(registry.valid([], 'reader', 'credential-v1'))
        with patch('session_registry.time.time', return_value=160):
            self.assertFalse(registry.valid(token, 'reader', 'credential-v1'))

    def test_capacity_prunes_and_revoke_is_idempotent(self):
        registry = SessionRegistry(max_sessions=1)
        with patch('session_registry.time.time', return_value=100):
            token = registry.issue('reader', 'v1', 60)
            with self.assertRaises(ValueError): registry.issue('reader', 'v1', 60)
            registry.revoke(token)
            registry.revoke(token)
            token = registry.issue('reader', 'v1', 60)
        with patch('session_registry.time.time', return_value=161):
            self.assertIsInstance(registry.issue('reader', 'v1', 60), str)


if __name__ == '__main__': unittest.main()
