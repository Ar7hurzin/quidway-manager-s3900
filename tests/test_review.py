"""Regression tests for input validation and switch-response handling."""
import unittest
from collections import deque
from unittest.mock import patch

from switch_config import ConfigurationError, build_plan, issue_preview, consume_preview
from switch_text import command_rejected
from ssh_readonly import _read_until_prompt

class ReviewTest(unittest.TestCase):
    def test_malformed_action_and_state(self):
        for data in ({'action': []}, {'action': 'set_port_state', 'port': 1, 'state': []}):
            with self.subTest(data=data), self.assertRaises(ConfigurationError):
                build_plan(data)

    def test_reject_non_integer_port_and_vlan(self):
        for data in ({'action': 'create_vlan', 'vlan_id': 3.5}, {'action': 'set_port_state', 'port': 1.5, 'state': 'up'}, {'action': 'create_vlan', 'vlan_id': True}):
            with self.subTest(data=data), self.assertRaises(ConfigurationError):
                build_plan(data)
        self.assertIn('vlan 10', build_plan({'action': 'create_vlan', 'vlan_id': 10}).commands)

    def test_reject_network_and_broadcast_address(self):
        for ip in ('192.168.1.0', '192.168.1.255'):
            with self.assertRaises(ConfigurationError):
                build_plan({'action':'set_management_ip','vlan_id':1,'ip':ip,'prefix':24})

    def test_preview_owner_and_single_use(self):
        plan=build_plan({'action':'save'})
        token=issue_preview(plan, owner='session-a')
        with self.assertRaises(ConfigurationError): consume_preview(token, owner='session-b')
        self.assertEqual(consume_preview(token, owner='session-a'),plan)
        with self.assertRaises(ConfigurationError): consume_preview(token, owner='session-a')

    def test_preview_expiry(self):
        token=issue_preview(build_plan({'action':'save'}), lifetime_seconds=0, owner='session-a')
        with self.assertRaises(ConfigurationError): consume_preview(token, owner='session-a')

    def test_vrp_error_detection(self):
        self.assertTrue(command_rejected('display vlan\n  % Unrecognized command\n<Quidway>'))
        self.assertTrue(command_rejected('Error: access denied'))
        self.assertFalse(command_rejected('description Error: label'))

    def test_ssh_pagination(self):
        class FakeChannel:
            closed=False
            def __init__(self): self.chunks=deque([b'first page\r\n---- More ----', b'\r\nsecond page\r\n<Quidway>']); self.sent=[]
            def recv_ready(self): return bool(self.chunks)
            def recv(self, size): return self.chunks.popleft()
            def exit_status_ready(self): return not self.chunks
            def sendall(self, data): self.sent.append(data)
        channel=FakeChannel()
        self.assertIn(b'second page',_read_until_prompt(channel,1))
        self.assertEqual(channel.sent,[' '])
