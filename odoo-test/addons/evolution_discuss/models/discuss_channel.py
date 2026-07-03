from odoo import api, fields, models
from odoo.tools import html2plaintext


class DiscussChannel(models.Model):
    """Bridge WhatsApp <-> Discuss: cada número que escribe vive en su propio
    canal, visible para los agentes sin salir de Odoo (ver evolution_go_discuss_inbox
    memory). v1: solo texto, sin routing/SLA — para eso conviene Chatwoot, no esto."""

    _inherit = "discuss.channel"

    evolution_instance_id = fields.Many2one("evolution.instance", index=True, copy=False)
    evolution_phone_number = fields.Char(index=True, copy=False)
    evolution_partner_id = fields.Many2one("res.partner", copy=False)

    _sql_constraints = [
        (
            "evolution_conversation_unique",
            "unique(evolution_instance_id, evolution_phone_number)",
            "Ya existe un canal de WhatsApp para este número en esta instancia.",
        ),
    ]

    @api.model
    def _evolution_get_or_create(self, instance, phone_number, push_name=None):
        channel = self.search([
            ("evolution_instance_id", "=", instance.id),
            ("evolution_phone_number", "=", phone_number),
        ], limit=1)
        if channel:
            return channel

        Partner = self.env["res.partner"].sudo()
        partner = (
            Partner.search([("phone", "=", phone_number)], limit=1)
            or Partner.search([("mobile", "=", phone_number)], limit=1)
        )
        if not partner:
            partner = Partner.create({
                "name": push_name or phone_number,
                "phone": phone_number,
                "company_id": instance.company_id.id,
            })

        agents = self.env.ref("evolution_whatsapp.group_evolution_user").users
        channel = self.create({
            "name": "WhatsApp: %s" % (push_name or phone_number),
            "channel_type": "channel",
            "evolution_instance_id": instance.id,
            "evolution_phone_number": phone_number,
            "evolution_partner_id": partner.id,
        })
        # add_members() en vez de channel_partner_ids: [(6,0,ids)] en el create:
        # ese command dispara un batching interno de discuss_channel_member que en
        # esta versión de Odoo 18 corrompe el INSERT (partner_id termina como
        # ARRAY[...] en vez de escalar) — confirmado en logs reales, no teórico.
        if agents:
            channel.add_members(agents.mapped("partner_id").ids, post_joined_message=False)
        return channel

    def message_post(self, **kwargs):
        message = super().message_post(**kwargs)
        if (
            not self.evolution_instance_id
            or self.env.context.get("evolution_skip_send")
            or kwargs.get("message_type", "comment") != "comment"
        ):
            return message

        text = html2plaintext(kwargs.get("body") or "").strip()
        if not text:
            return message  # v1: sin adjuntos, un mensaje sin texto no se reenvía

        evo_message = self.env["evolution.message"].sudo().create({
            "instance_id": self.evolution_instance_id.id,
            "direction": "outgoing",
            "recipient": self.evolution_phone_number,
            "message_type": "text",
            "body": text,
        })
        try:
            evo_message._send_text()
        except Exception as exc:  # noqa: BLE001 - reportamos el fallo en el propio canal
            self.with_context(evolution_skip_send=True).message_post(
                body="⚠️ No se pudo enviar por WhatsApp: %s" % exc,
                subtype_xmlid="mail.mt_note",
            )
        return message
