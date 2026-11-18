"""Testes de seguranca da configuracao multiswitch."""

import os
import unittest
from unittest.mock import patch

import tests.test_manager as helpers


class MultiswitchSecurityTest(unittest.TestCase):

    def setUp(self):
        teste = helpers.ManagerTest("test_public_health")
        teste.setUp()

        self.client = teste.client
        self.headers = {"X-CSRF-Token": teste.login()}

    def verificar_bloqueio(self, snapshot, stale=False):
        cache = {"snapshot": snapshot, "stale": stale}

        ambiente = {
            "QM_MODE": "serial",
            "QM_ENABLE_CONFIG_WRITES": "true",
            "QM_ENABLE_CLI": "false",
        }

        with patch.dict(os.environ, ambiente), patch.object(
            helpers.manager.physical_dashboard,
            "cached",
            return_value=cache
        ):
            resposta = self.client.get("/api/config/status")
            dados = resposta.get_json()

            self.assertFalse(dados["available"])
            self.assertFalse(dados["writes_enabled"])

            operacoes = [
                ("/api/config/preview", {"action": "save"}),
                ("/api/config/apply", {"token": "teste"}),
                ("/api/config/backup", {}),
            ]

            for rota, payload in operacoes:
                with self.subTest(rota=rota):
                    resposta = self.client.post(
                        rota,
                        json=payload,
                        headers=self.headers
                    )
                    self.assertEqual(resposta.status_code, 409)

    def test_s3000_bloqueado(self):
        self.verificar_bloqueio({
            "model": "S3026C",
            "profile": "s3000"
        })

    def test_desconhecido_bloqueado(self):
        self.verificar_bloqueio(None)

    def test_s3900_desatualizado_bloqueado(self):
        self.verificar_bloqueio({
            "model": "3900-SI",
            "profile": "s3900"
        }, stale=True)

    def test_s3900_identificado(self):
        cache = {
            "snapshot": {
                "model": "3900-SI",
                "profile": "s3900"
            },
            "stale": False
        }

        with patch.dict(os.environ, {
            "QM_MODE": "serial",
            "QM_ENABLE_CONFIG_WRITES": "false",
        }), patch.object(
            helpers.manager.physical_dashboard,
            "cached",
            return_value=cache
        ):
            resposta = self.client.get("/api/config/status")
            dados = resposta.get_json()

            self.assertTrue(dados["available"])
            self.assertFalse(dados["writes_enabled"])


if __name__ == "__main__":
    unittest.main()
