"""Physical dashboard regression tests with captured-format VRP responses."""
import unittest
from unittest.mock import patch, Mock
from collections import deque
from physical_dashboard import (PhysicalDashboard, parse_identity, parse_port,
                                parse_brief, parse_vlan_detail, parse_mac_table,
                                reconcile_vlan_members)
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

    def test_multiline_vlan_members_damaged_serial_and_individual_port(self):
        raw = """VLAN ID: 12
 Description: teste0001
 Tagged   Ports: none
 Untagged Ports:
             Ethernet1/�/1         Ethernet1/0/2         Ethernet1/0/3 """
        detail = parse_vlan_detail(raw, 12)
        self.assertEqual(detail["name"], "teste0001")
        self.assertEqual(detail["tagged_numbers"], [])
        self.assertEqual(detail["untagged_numbers"], [2, 3])
        self.assertTrue(detail["membership_partial"])
        self.assertEqual(detail["members"], "Untagged: 2, 3 (leitura parcial)")

        # The missing port must only be added after separate CLI confirmation.
        individual = parse_port(
            "Ethernet1/0/1 current state : UP\n"
            "Mdi type: autoJ Port link-type: access\nPVID: 12",
            1,
        )
        self.assertEqual(individual["mode"], "access")
        self.assertEqual(individual["pvid"], 12)
        data = {"vlans": [detail], "ports": [individual]}
        reconcile_vlan_members(data)
        self.assertEqual(data["vlans"][0]["members"],
                         "Untagged: 1, 2, 3 (leitura parcial)")
        self.assertEqual(data["vlans"][0]["membership_from_ports"], [1])

    def test_vlan_does_not_invent_members_from_corrupt_name(self):
        detail = parse_vlan_detail(
            "VLAN ID: 12\nTagged Ports: none\nUntagged Ports: "
            "Ethernet1/�/1", 12
        )
        self.assertIsNone(detail["members"])
        self.assertTrue(detail["membership_partial"])
        self.assertEqual(detail["untagged_numbers"], [])
        empty = parse_vlan_detail(
            "VLAN ID: 12\nTagged Ports: none\nUntagged Ports: none", 12
        )
        self.assertEqual(empty["members"], "Nenhuma")
        self.assertFalse(empty["membership_partial"])

    def test_mac_table(self):
        result = parse_mac_table("00e0-fc12-3456 12 Learned Ethernet1/0/1 AGING\n00e0-fc12-3456 11 Dynamic Ethernet1/0/2\n")
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["entries"]), 2)
        self.assertIsNone(parse_mac_table("Unrecognized command"))
        self.assertEqual(parse_mac_table("Total MAC addresses: 0")["count"], 0)

    def test_read_only_cache_and_partial_ports(self):
        d = PhysicalDashboard()
        settings = SerialSettings("/dev/ttyUSB0", 9600, 3)
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        responses = deque([
            b"<Quidway>", VERSION.encode(), VLAN.encode(), DETAIL.encode(),
            b"<Quidway>", VLAN.encode(),
            b"<Quidway>", PORT.encode(),
            b"<Quidway>", b"Ethernet1/0/2 current state : DOWN\nPVID: 1",
        ])
        with patch("physical_dashboard.open_connection", return_value=conn), \
             patch("physical_dashboard._read_until_prompt",
                   side_effect=lambda *a: responses.popleft()):
            first = d.collect(settings)
            self.assertEqual(len(first["snapshot"]["ports"]), 28)
            self.assertEqual(len(first["snapshot"]["vlans"]), 3)
            self.assertIsNone(first["snapshot"]["ports"][0]["pvid"])
            self.assertFalse(first["stale"])
            writes = conn.write.call_count
            self.assertEqual(d.collect(settings)["snapshot"], first["snapshot"])
            self.assertEqual(conn.write.call_count, writes)
            d.collect(settings, force=True)  # stage 0: VLAN inventory
            second = d.collect(settings, force=True)  # stage 1: port 1
            self.assertEqual(second["snapshot"]["ports"][0]["pvid"], 12)
            self.assertEqual(second["snapshot"]["ports"][0]["state"], "up")
            self.assertIsNone(second["snapshot"]["ports"][3]["state"])
            third = d.collect(settings, force=True)  # stage 2: port 2
            self.assertEqual(third["snapshot"]["ports"][1]["state"], "down")
            self.assertEqual(third["snapshot"]["ports"][0]["pvid"], 12)
            for call in conn.write.call_args_list:
                self.assertTrue(call.args[0] == b"\r" or call.args[0].startswith(b"display "))
        d.invalidate()
        self.assertTrue(d.cached()["stale"])
        with patch("physical_dashboard.open_connection",
                   side_effect=OSError("cable disconnected")):
            result = d.collect(settings, force=True)
        self.assertTrue(result["stale"])
        self.assertIn("cable disconnected", result["message"])
        self.assertEqual(result["snapshot"]["ports"][0]["pvid"], 12)

    def test_vlan_inventory_failure_does_not_erase_old_values(self):
        d = PhysicalDashboard()
        settings = SerialSettings("/dev/ttyUSB0", 9600, 3)
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        responses = deque([
            b"<Quidway>", VERSION.encode(), VLAN.encode(), DETAIL.encode(),
            b"<Quidway>", b"partial, invalid response",
        ])
        with patch("physical_dashboard.open_connection", return_value=conn), \
             patch("physical_dashboard._read_until_prompt",
                   side_effect=lambda *a: responses.popleft()):
            d.collect(settings)
            d.collect(settings, force=True)
        self.assertEqual([v["id"] for v in d.cached()["snapshot"]["vlans"]], [1, 11, 12])
        self.assertIn("preservados", " ".join(d.cached()["snapshot"]["notes"]))

    def test_incomplete_inventory_does_not_hide_individually_confirmed_vlan(self):
        d = PhysicalDashboard()
        settings = SerialSettings("/dev/ttyUSB0", 9600, 3)
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        only_default = b'Now, the following VLAN exist(s):\n  1(default)\n<Quidway>'
        responses = deque([
            b"<Quidway>", VERSION.encode(), only_default, DETAIL.encode(),
            b"<Quidway>", only_default,
        ])
        with patch("physical_dashboard.open_connection", return_value=conn), \
             patch("physical_dashboard._read_until_prompt",
                   side_effect=lambda *a: responses.popleft()):
            first = d.collect(settings)
            self.assertEqual([v["id"] for v in first["snapshot"]["vlans"]], [1, 12])
            vlan = first["snapshot"]["vlans"][1]
            self.assertTrue(vlan["verified_individually"])
            self.assertEqual(vlan["name"], "teste_notebook")
            self.assertIn("display vlan 12", vlan["note"])
            second = d.collect(settings, force=True)  # incomplete inventory again
            self.assertEqual([v["id"] for v in second["snapshot"]["vlans"]], [1, 12])
            self.assertIn("inventário", second["snapshot"]["vlans"][1]["note"].lower())
            writes = [call.args[0] for call in conn.write.call_args_list]
            self.assertIn(b'display vlan 12\r', writes)
            self.assertTrue(all(x == b'\r' or x.startswith(b"display ") for x in writes))

    def test_unconfirmed_vlan_is_never_invented(self):
        d = PhysicalDashboard()
        settings = SerialSettings("/dev/ttyUSB0", 9600, 3)
        conn = Mock()
        conn.__enter__ = Mock(return_value=conn)
        conn.__exit__ = Mock(return_value=False)
        only_default = b'Now, the following VLAN exist(s):\n  1(default)\n<Quidway>'
        responses = deque([b"<Quidway>", VERSION.encode(), only_default, b"Error: Unrecognized VLAN"])
        with patch("physical_dashboard.open_connection", return_value=conn), \
             patch("physical_dashboard._read_until_prompt",
                   side_effect=lambda *a: responses.popleft()):
            result = d.collect(settings)
        self.assertEqual([v["id"] for v in result["snapshot"]["vlans"]], [1])
        self.assertIn("não confirmada", " ".join(result["snapshot"]["notes"]))
