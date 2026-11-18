"""Testes de integracao do monitoramento multiswitch."""

import time
import unittest

from collections import deque
from unittest.mock import Mock, patch

from physical_dashboard import PhysicalDashboard
from serial_readonly import SerialSettings, SerialReadOnlyError


VERSION_S3000 = """
Huawei Versatile Routing Platform Software
VRP Software, Version 3.10, Release 0039
Quidway S3026C uptime is 0 week, 0 day, 1 hour
"""

VERSION_S3900 = """
VRP (R) Software, Version 3.10(CN), RELEASE 0002
Quidway 3900-SI 28-Port uptime is 0 week, 0 day, 1 hour
"""

VLAN = """
Now, the following VLAN exist(s):
1(default)
<Quidway>
"""

VLAN_DETAIL = """
VLAN ID: 12
Description: TESTE
Tagged Ports: none
Untagged Ports: Ethernet0/1
"""

PORT_S3000 = """
Ethernet0/1 current state : UP
PVID: 1
Port link-type: access
"""


class MultiswitchTest(unittest.TestCase):

    def setUp(self):
        self.dashboard = PhysicalDashboard()
        self.settings = SerialSettings("/dev/ttyUSB0", 9600, 3)

        self.connection = Mock()
        self.connection.__enter__ = Mock(
            return_value=self.connection
        )
        self.connection.__exit__ = Mock(return_value=False)

    def simular_serial(self, respostas):
        fila = deque(
            resposta.encode()
            for resposta in respostas
        )

        def ler(*args, **kwargs):
            return fila.popleft()

        return patch(
            "physical_dashboard._read_until_prompt",
            side_effect=ler
        )

    def test_identificacao_s3000(self):

        respostas = [
            "<Quidway>",
            VERSION_S3000,
            VLAN,
            VLAN_DETAIL,
            "<Quidway>",
            VLAN,
            "<Quidway>",
            PORT_S3000,
        ]

        with patch(
            "physical_dashboard.open_connection",
            return_value=self.connection
        ), self.simular_serial(respostas):

            resultado = self.dashboard.collect(
                self.settings,
                force=True
            )

            snapshot = resultado["snapshot"]

            self.assertEqual(snapshot["profile"], "s3000")
            self.assertEqual(snapshot["expected_ports"], 24)
            self.assertEqual(len(snapshot["ports"]), 24)

            self.assertEqual(
                snapshot["ports"][0]["interface"],
                "Ethernet0/1"
            )

            self.dashboard.collect(self.settings, force=True)
            resultado = self.dashboard.collect(
                self.settings,
                force=True
            )

            self.assertEqual(
                resultado["snapshot"]["ports"][0]["state"],
                "up"
            )

        comandos = [
            chamada.args[0]
            for chamada in self.connection.write.call_args_list
        ]

        self.assertIn(
            b"display interface Ethernet0/1\r",
            comandos
        )

    def test_troca_s3900_para_s3000(self):

        respostas = [
            "<Quidway>",
            VERSION_S3900,
            VLAN,
            VLAN_DETAIL,

            "<Quidway>",
            VERSION_S3000,

            "<Quidway>",
            VERSION_S3000,
            VLAN,
            VLAN_DETAIL,
        ]

        with patch(
            "physical_dashboard.open_connection",
            return_value=self.connection
        ), self.simular_serial(respostas):

            primeiro = self.dashboard.collect(
                self.settings,
                force=True
            )

            self.assertEqual(
                primeiro["snapshot"]["expected_ports"],
                28
            )

            # Simula a passagem de mais de 60 segundos
            self.dashboard.ultima_verificacao_identidade = (
                time.monotonic() - 61
            )

            with self.assertRaises(SerialReadOnlyError):
                self.dashboard.collect(
                    self.settings,
                    force=True
                )

            # O cache antigo nao pode continuar disponivel
            self.assertIsNone(
                self.dashboard.cached()["snapshot"]
            )

            # Reconhece o novo equipamento
            novo = self.dashboard.collect(
                self.settings,
                force=True
            )

            self.assertEqual(
                novo["snapshot"]["profile"],
                "s3000"
            )

            self.assertEqual(
                novo["snapshot"]["expected_ports"],
                24
            )

            self.assertEqual(
                len(novo["snapshot"]["ports"]),
                24
            )


if __name__ == "__main__":
    unittest.main()
