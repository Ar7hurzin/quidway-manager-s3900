"""Revocable, bounded sessions for the single-process local server.

Activity/presence is deliberately separate: an idle browser stays authorized
until its original expiry, but a logged-out cookie can never register itself.
Restarting the server clears authorization and requires signing in again.
"""
from __future__ import annotations

import hmac
import secrets
import threading
import time


class SessionRegistry:
    def __init__(self, max_sessions=4096):
        self._entries = {}
        self._lock = threading.Lock()
        self.max_sessions = max_sessions

    def issue(self, username, credential_version, lifetime_seconds):
        now = time.time()
        with self._lock:
            self._prune(now)
            if len(self._entries) >= self.max_sessions:
                raise ValueError('Limite de sessões atingido. Aguarde ou encerre uma sessão.')
            identifier = secrets.token_urlsafe(32)
            self._entries[identifier] = (username, credential_version, now + lifetime_seconds)
            return identifier

    def valid(self, identifier, username, credential_version):
        if not isinstance(identifier, str):
            return False
        with self._lock:
            self._prune(time.time())
            entry = self._entries.get(identifier)
            return bool(entry and entry[0] == username and
                        hmac.compare_digest(entry[1], credential_version))

    def revoke(self, identifier):
        if isinstance(identifier, str):
            with self._lock:
                self._entries.pop(identifier, None)

    def _prune(self, now):
        for identifier, (_, _, expires) in list(self._entries.items()):
            if expires <= now:
                del self._entries[identifier]
