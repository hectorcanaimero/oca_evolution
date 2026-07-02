import re
from datetime import timedelta

from odoo import _, api, fields, models


def _normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if digits else ""


class EvolutionMessage(models.Model):
    _inherit = "evolution.message"

    lead_id = fields.Many2one(
        "crm.lead",
        string="Oportunidad",
        copy=False,
        index=True,
        help="Lead de CRM vinculado a este mensaje (matcheado o creado automáticamente).",
    )

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        for message in messages:
            message._sync_crm_lead()
        return messages

    def _sync_crm_lead(self):
        """Vincula el mensaje entrante con un lead de CRM, creando uno nuevo
        si no matchea ninguno con actividad dentro de crm_reopen_days.

        ponytail: matching de teléfono es heurístico (últimos 10 dígitos),
        no E.164 completo — subir a phone_validation si aparecen falsos
        positivos entre países con números locales del mismo largo.
        """
        self.ensure_one()
        instance = self.instance_id
        if self.direction != "incoming" or not instance.crm_integration_enabled or self.lead_id:
            return
        normalized = _normalize_phone(self.recipient)
        if not normalized:
            return
        tail = normalized[-8:]

        Lead = self.env["crm.lead"].sudo()
        candidates = Lead.search([
            "|", ("phone", "like", tail), ("mobile", "like", tail),
            ("company_id", "in", [instance.company_id.id, False]),
        ])
        cutoff = fields.Datetime.now() - timedelta(days=instance.crm_reopen_days or 0)
        lead = next(
            (l for l in candidates
             if l.write_date >= cutoff
             and normalized in (_normalize_phone(l.phone), _normalize_phone(l.mobile))),
            None,
        )
        if not lead:
            Partner = self.env["res.partner"].sudo()
            partners = Partner.search([
                "|", ("phone", "like", tail), ("mobile", "like", tail),
            ])
            partner = next(
                (p for p in partners
                 if normalized in (_normalize_phone(p.phone), _normalize_phone(p.mobile))),
                None,
            )
            lead = Lead.create({
                "name": _("WhatsApp: %s") % (self.body[:50] if self.body else self.recipient),
                "phone": self.recipient,
                "partner_id": partner.id if partner else False,
                "team_id": instance.crm_team_id.id or False,
                "user_id": instance.crm_user_id.id or False,
                "company_id": instance.company_id.id,
            })
        self.lead_id = lead.id
        lead.message_post(body=self.body or self.name)
