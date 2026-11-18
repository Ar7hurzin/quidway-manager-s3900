"""Testes de verificacao de identidade antes de configurar."""

import unittest
from unittest.mock import Mock, patch

import serial_config

from serial_readonly import SerialSettings
from switch_config import build_plan, ConfigurationError


class SwitchIdentityGuardTest(unittest.TestCase):

    def verificar_rejeicao(self, resposta):

        conexao = Mock()
        comandos = []

        def consulta_falsa(
            connection,
            command,
            settings,
            allow_confirmation=False
        ):
            comandos.append(command)

            if command == "display version":
                return resposta, False

            self.fail(
                "Comando inesperado antes da validacao: "
                + command
            )

        settings = SerialSettings(
            "/dev/ttyUSB0",
            9600,
            3
        )

        plano = build_plan({
            "action": "save"
        })

        with patch.object(
            serial_config,
            "_open",
            return_value=conexao
        ), patch.object(
            serial_config,
            "_read_response",
            return_value=(b"<Quidway>", False)
        ), patch.object(
            serial_config,
            "_exchange",
            side_effect=consulta_falsa
        ):

            with self.assertRaises(ConfigurationError):
                serial_config.execute_plan(
                    settings,
                    plano
                )

        self.assertEqual(
            comandos,
            ["display version"]
        )

        conexao.close.assert_called_once()

    def test_troca_s3900_para_s3000(self):

        resposta = """
Huawei Versatile Routing Platform Software
VRP Software, Version 3.10, Release 0039
Quidway S3026C with 1 MIPS Processor
"""

        self.verificar_rejeicao(resposta)

    def test_firmware_s3900_incompativel(self):

        resposta = """
Huawei Versatile Routing Platform Software
VRP Software, Version 3.10(CN), RELEASE 0099
Quidway 3900-SI 28-Port
"""

        self.verificar_rejeicao(resposta)


if __name__ == "__main__":
    unittest.main()
