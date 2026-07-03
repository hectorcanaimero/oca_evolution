from unittest.mock import patch

from odoo.tests.common import TransactionCase


class TestWhatsappAutomation(TransactionCase):
    def setUp(self):
        super().setUp()
        self.instance = self.env["evolution.instance"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if self.instance:
            self.instance.write({"state": "connected", "token": self.instance.token or "fake-token"})
        else:
            self.instance = self.env["evolution.instance"].create({
                "name": "test_automation",
                "state": "connected",
                "token": "fake-token",
            })
        self.partner = self.env["res.partner"].create({
            "name": "Cliente Test",
            "mobile": "5491155556666",
        })
        model_id = self.env["ir.model"]._get_id("res.partner")
        self.template = self.env["whatsapp.template"].create({
            "name": "Aviso",
            "model_id": model_id,
            "instance_id": self.instance.id,
            "body": "Hola {{ object.name }}",
        })
        self.action = self.env["ir.actions.server"].create({
            "name": "Enviar WhatsApp de prueba",
            "model_id": model_id,
            "state": "whatsapp_message",
            "whatsapp_template_id": self.template.id,
        })

    def test_run_action_sends_whatsapp_via_composer(self):
        with patch(
            "odoo.addons.evolution_whatsapp.models.evolution_api.EvolutionAPI.send_text",
            return_value={"messageId": "abc123"},
        ) as mock_send:
            self.action._run_action_whatsapp_message_multi(
                eval_context={"record": self.partner, "records": self.partner}
            )
        mock_send.assert_called_once()
        message = self.env["evolution.message"].search(
            [("recipient", "=", "5491155556666")], limit=1
        )
        self.assertTrue(message)
        self.assertIn("Cliente Test", message.body)

    def test_run_action_without_template_is_noop(self):
        self.action.whatsapp_template_id = False
        result = self.action._run_action_whatsapp_message_multi(
            eval_context={"record": self.partner, "records": self.partner}
        )
        self.assertFalse(result)
        self.assertFalse(
            self.env["evolution.message"].search(
                [("recipient", "=", "5491155556666")]
            )
        )
