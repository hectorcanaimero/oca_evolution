import secrets

from odoo import _, api, fields, models


class EvolutionMessage(models.Model):
    _name = "evolution.message"
    _description = "Mensaje de WhatsApp enviado/recibido vía Evolution Go"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(
        string="Resumen",
        compute="_compute_name",
        store=True,
    )
    instance_id = fields.Many2one(
        "evolution.instance",
        string="Instancia",
        required=True,
        ondelete="cascade",
        index=True,
    )
    direction = fields.Selection(
        [("outgoing", "Saliente"), ("incoming", "Entrante")],
        default="outgoing",
        required=True,
        tracking=True,
    )
    recipient = fields.Char(
        string="Número destino",
        required=True,
        index=True,
        tracking=True,
        help="Número en formato internacional sin '+'. Ej: 5491155556666",
    )
    message_type = fields.Selection(
        [
            ("text", "Texto"),
            ("image", "Imagen"),
            ("video", "Video"),
            ("document", "Documento"),
            ("audio", "Audio"),
            ("location", "Ubicación"),
            ("contact", "Contacto"),
        ],
        default="text",
        required=True,
    )
    body = fields.Text(string="Texto / Caption")
    media_url = fields.Char(string="URL del archivo")
    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Adjunto",
        ondelete="set null",
    )
    mimetype = fields.Char()
    filename = fields.Char()
    state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("sending", "Enviando"),
            ("sent", "Enviado"),
            ("delivered", "Entregado"),
            ("read", "Leído"),
            ("failed", "Fallido"),
        ],
        default="draft",
        tracking=True,
        index=True,
    )
    external_id = fields.Char(
        string="ID Evolution",
        index=True,
        copy=False,
        help="messageId devuelto por Evolution Go.",
    )
    error_message = fields.Text(copy=False)
    raw_response = fields.Text(copy=False, groups="evolution_whatsapp.group_evolution_manager")
    sent_at = fields.Datetime()
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
        help="Empresa (tenant CitaSpot) dueña del mensaje.",
    )

    @api.depends("recipient", "message_type", "body")
    def _compute_name(self):
        for rec in self:
            preview = (rec.body or "").strip().splitlines()[:1]
            preview = preview[0] if preview else dict(self._fields["message_type"].selection).get(rec.message_type, "")
            preview = preview[:60]
            rec.name = "%s → %s" % (rec.recipient or "?", preview)

    def action_resend(self):
        for rec in self:
            if rec.direction != "outgoing":
                continue
            if rec.message_type == "text":
                rec._send_text()
            else:
                rec._send_media()
        return True

    def _send_text(self):
        self.ensure_one()
        self.state = "sending"
        try:
            response = self.instance_id._instance_api().send_text(
                number=self.recipient,
                text=self.body or "",
            )
        except Exception as exc:
            self.write({"state": "failed", "error_message": str(exc)})
            raise
        msg_id = (response or {}).get("messageId") or (
            (response or {}).get("data", {}).get("Info", {}).get("ID")
        )
        self.write({
            "state": "sent",
            "external_id": msg_id,
            "raw_response": str(response),
            "sent_at": fields.Datetime.now(),
            "error_message": False,
        })
        return response

    def _send_media(self):
        self.ensure_one()
        self.state = "sending"
        api = self.instance_id._instance_api()
        url = self.media_url
        if not url and self.attachment_id:
            url = self._public_url_for_attachment(self.attachment_id)
            self.media_url = url
        if not url:
            raise ValueError(_("No hay archivo ni URL para enviar."))
        kwargs = {
            "number": self.recipient,
            "media_type": self.message_type,
            "caption": self.body or None,
            "filename": self.filename or None,
            "mimetype": self.mimetype or (self.attachment_id.mimetype if self.attachment_id else None),
            "url": url,
        }
        try:
            response = api.send_media(**kwargs)
        except Exception as exc:
            self.write({"state": "failed", "error_message": str(exc)})
            raise
        msg_id = (response or {}).get("messageId") or (
            (response or {}).get("data", {}).get("Info", {}).get("ID")
        )
        self.write({
            "state": "sent",
            "external_id": msg_id,
            "raw_response": str(response),
            "sent_at": fields.Datetime.now(),
            "error_message": False,
        })
        return response

    def _public_url_for_attachment(self, attachment):
        """Genera URL descargable pública para el attachment con access_token."""
        if not attachment.access_token:
            attachment.sudo().access_token = secrets.token_urlsafe(32)
        ICP = self.env["ir.config_parameter"].sudo()
        base = (
            self.instance_id.evolution_webhook_base_url
            or ICP.get_param("web.base.url")
            or ""
        ).rstrip("/")
        if not base:
            raise ValueError(_(
                "Falta configurar 'web.base.url' para generar URLs públicas."
            ))
        return "%s/web/content/%s?access_token=%s&download=true" % (
            base, attachment.id, attachment.access_token,
        )
