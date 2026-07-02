from odoo.tests.common import TransactionCase


class TestCrmSync(TransactionCase):
    def setUp(self):
        super().setUp()
        self.instance = self.env["evolution.instance"].create({
            "name": "test_crm",
            "crm_integration_enabled": True,
            "crm_reopen_days": 30,
        })

    def _incoming(self, recipient="5491155556666", body="hola"):
        return self.env["evolution.message"].create({
            "instance_id": self.instance.id,
            "direction": "incoming",
            "recipient": recipient,
            "body": body,
        })

    def test_creates_lead_on_first_incoming_message(self):
        message = self._incoming()
        self.assertTrue(message.lead_id)
        self.assertEqual(message.lead_id.phone, "5491155556666")

    def test_reuses_open_lead_for_same_number(self):
        first = self._incoming(body="hola")
        second = self._incoming(body="dale")
        self.assertEqual(first.lead_id, second.lead_id)

    def test_ignores_outgoing_messages(self):
        message = self.env["evolution.message"].create({
            "instance_id": self.instance.id,
            "direction": "outgoing",
            "recipient": "5491155556666",
            "body": "hola",
        })
        self.assertFalse(message.lead_id)

    def test_disabled_integration_does_not_create_lead(self):
        self.instance.crm_integration_enabled = False
        message = self._incoming()
        self.assertFalse(message.lead_id)

    def test_no_reopen_creates_new_lead_each_time(self):
        self.instance.crm_reopen_days = 0
        first = self._incoming()
        second = self._incoming()
        self.assertNotEqual(first.lead_id, second.lead_id)
