"""Physical dashboard regression tests with captured-format VRP responses."""
import unittest
from unittest.mock import patch, Mock
from collections import deque
from physical_dashboard import (PhysicalDashboard, parse_identity, parse_port,
                                parse_brief, parse_vlan_detail)
from serial_config import _parse_vlan_inventory
from serial_readonly import SerialSettings, SerialReadOnlyError

VERSION = """VRP (R) Software, Version 3.10(CN), RELEASE 0002
Quidway 3900-SI 28-Port uptime is 0 week,0 day,0 hour,32 minutes
Hardware Version is SI_REV.C
CPLD Version is CPLD 001
Bootrom Version is 213"""
VLAN = "Now, the following VLAN exist(s):\n  1(default), 11-12,\u25ab\n<Quidway>"
PORT = """Ethernet1/0/1 current state : UP
PVID: 12
Port link-type: access
 Tagged VLAN ID : none
 Untagged VLAN ID : 12
Last 300 seconds input: 0 packets/sec 0 bytes/sec
Last 300 seconds output: 1 packets/sec 100 bytes/sec"""
DETAIL = """VLAN ID: 12
VLIN Type: static
Description: teste_notebook
Tagged Ports: none
Untagged Ports: Ethernet1/0/1"""

class PhysicalDashboardTest(unittest.TestCase):
    def test_inventory_ranges(self):
        self.assertEqual(_parse_vlan_inventory(VLAN), {1,11,12})
        self.assertEqual(_parse_vlan_inventory("Following VLAN exist(s):\n1(default), 2-4, 4094,\n<Quidway>"), {1,2,3,4,4094})

    def test_identity_and_details(self):
        self.assertEqual(parse_identity(VERSION)["hardware"], "SI_REV.C")
        with self.assertRaises(SerialReadOnlyError):
            parse_identity("other switch")
        p = parse_port(PORT, 1)
        self.assertEqual((p["state"],p["pvid"],p["mode"],p["tx_bps"]), ("up",12,"access",800))
        self.assertEqual(p["untagged"],"12")
        self.assertIsNone(parse_port("PVID: 12\nPort link-type: access",1)["state"])
        self.assertIsNone(parse_port("Error: rejected",1))
        self.assertEqual(parse_vlan_detail(DETAIL,12)["name"],"teste_notebook")
        self.assertIsNone(parse_vlan_detail(DETAIL,11))
        self.assertEqual(parse_brief("Ethernet1/0/1 UP 100M\nEthernet1/0/2 DOWN\n")[2]["state"],"down")

    def test_read_only_cache_and_partial_ports(self):
        d = PhysicalDashboard()
        settings = SerialSettings("/dev/ttyUSB0",9600,3)
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        responses = deque([
            b"<Quidway>",
            VERSION.encode(), VLAN.encode(),
            b"Error: Unrecognized command",
            PORT.encode(), b"Ethernet1/0/2 current state : DOWN\nPVID: 1",
            b"Ethernet1/0/3 current state : DOWN\nPVID: 1",
            b"VLAN ID: 1\nTagged Ports: none\nUntagged Ports: none",
            b"VLAN ID: 11\nDescription: LAB\nTagged Ports: none\nUntagged Ports: none",
        ])
        with patch("physical_dashboard.open_connection",return_value=conn), \
             patch("physical_dashboard._read_until_prompt",side_effect=lambda *a: responses.popleft()):
            r=d.collect(settings)
            self.assertEqual(len(r["snapshot"]["ports"]),28)
            self.assertEqual(len(r["snapshot"]["vlans"]),3)
            self.assertEqual(r["snapshot"]["ports"][0]["pvid"],12)
            self.assertIsNone(r["snapshot"]["ports"][3]["state"])
            self.assertFalse(r["stale"])
            writes=conn.write.call_count
            self.assertEqual(d.collect(settings)["snapshot"],r["snapshot"])
            self.assertEqual(conn.write.call_count,writes)
            for call in conn.write.call_args_list:
                self.assertTrue(call.args[0] == b"\r" or call.args[0].startswith(b"display "))
        d.invalidate()
        self.assertTrue(d.cached()["stale"])
        with patch("physical_dashboard.open_connection",side_effect=OSError("cable disconnected")):
            with self.assertRaises(SerialReadOnlyError):
                d.collect(settings)
        self.assertTrue(d.cached()["stale"])
        self.assertEqual(d.cached()["snapshot"]["ports"][0]["pvid"],12)
