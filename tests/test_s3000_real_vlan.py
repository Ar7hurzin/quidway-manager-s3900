"""Testes de VLAN com respostas reais do Huawei S3026C."""

import unittest
from pathlib import Path

from serial_config import _parse_vlan_inventory
from physical_dashboard import parse_vlan_detail


FIXTURES = Path(__file__).parent / "fixtures"


class S3000RealVlanTest(unittest.TestCase):

    def test_inventario_real(self):
        texto = (
            FIXTURES / "s3000_display_vlan.txt"
        ).read_text(encoding="utf-8")

        vlans = _parse_vlan_inventory(texto)

        self.assertEqual(vlans, {1})

    def test_membros_reais_vlan_1(self):
        texto = (
            FIXTURES / "s3000_vlan1.txt"
        ).read_text(encoding="utf-8")

        vlan = parse_vlan_detail(
            texto,
            1,
            perfil="s3000"
        )

        self.assertIsNotNone(vlan)
        self.assertEqual(vlan["id"], 1)
        self.assertEqual(vlan["name"], "VLAN 0001")
        self.assertEqual(vlan["tagged_numbers"], [])
        self.assertEqual(
            vlan["untagged_numbers"],
            list(range(1, 25))
        )
        self.assertFalse(vlan["membership_partial"])

    def test_rejeita_vlan_diferente(self):
        texto = (
            FIXTURES / "s3000_vlan1.txt"
        ).read_text(encoding="utf-8")

        resultado = parse_vlan_detail(
            texto,
            12,
            perfil="s3000"
        )

        self.assertIsNone(resultado)


if __name__ == "__main__":
    unittest.main()
