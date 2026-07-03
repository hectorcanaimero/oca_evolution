from unittest.mock import patch

from odoo.tests.common import TransactionCase

from odoo.addons.evolution_whatsapp.models.evolution_api import EvolutionAPIError


class TestEvolutionWebhook(TransactionCase):
    def setUp(self):
        super().setUp()
        self.instance = self.env["evolution.instance"].create({
            "name": "test_webhook",
            "token": "fake-token",
            "evolution_api_url": "https://evogo.example.test",
            "evolution_global_apikey": "fake-global-key",
            "evolution_webhook_base_url": "https://abc123.ngrok-free.app",
        })

    def test_sync_remote_id_backfills_from_fetch_all_instances(self):
        with patch(
            "odoo.addons.evolution_whatsapp.models.evolution_api.EvolutionAPI.fetch_all_instances",
            return_value={"data": [{
                "id": "dc977042-f951-478c-882e-49e2d541a896",
                "name": "test_webhook",
                "token": "fake-token",
            }]},
        ):
            result = self.instance._sync_remote_id()
        self.assertTrue(result)
        self.assertEqual(self.instance.remote_id, "dc977042-f951-478c-882e-49e2d541a896")

    def test_sync_remote_id_noop_when_already_set(self):
        self.instance.remote_id = "already-set-id"
        with patch(
            "odoo.addons.evolution_whatsapp.models.evolution_api.EvolutionAPI.fetch_all_instances",
        ) as mock_fetch:
            result = self.instance._sync_remote_id()
        self.assertTrue(result)
        mock_fetch.assert_not_called()

    def test_register_webhook_raises_on_evolution_error(self):
        self.instance.remote_id = "already-set-id"
        with patch(
            "odoo.addons.evolution_whatsapp.models.evolution_api.EvolutionAPI.connect_instance",
            side_effect=EvolutionAPIError("not authorized"),
        ):
            with self.assertRaises(EvolutionAPIError):
                self.instance.action_register_webhook()
        self.assertIn("not authorized", self.instance.last_error or "")

    def test_create_in_evolution_survives_webhook_registration_failure(self):
        with patch(
            "odoo.addons.evolution_whatsapp.models.evolution_api.EvolutionAPI.create_instance",
            return_value={"data": {"id": "dc977042-f951-478c-882e-49e2d541a896"}},
        ), patch(
            "odoo.addons.evolution_whatsapp.models.evolution_api.EvolutionAPI.get_qr",
            return_value={},
        ), patch(
            "odoo.addons.evolution_whatsapp.models.evolution_api.EvolutionAPI.connect_instance",
            side_effect=EvolutionAPIError("not authorized"),
        ):
            result = self.instance.action_create_in_evolution()
        self.assertTrue(result)
        self.assertEqual(self.instance.state, "qrcode")
