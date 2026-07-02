from odoo import models


class EvolutionInstance(models.Model):
    _inherit = "evolution.instance"

    def _notify_inbound_message(self, phone, body, push_name):
        channel = self.env["discuss.channel"]._evolution_get_or_create(self, phone, push_name)
        channel.with_context(evolution_skip_send=True).message_post(
            body=body,
            author_id=channel.evolution_partner_id.id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
