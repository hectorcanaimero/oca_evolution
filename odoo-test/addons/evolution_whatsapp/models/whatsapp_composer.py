import base64
import json
import logging
import mimetypes
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .evolution_api import _normalize_number

_logger = logging.getLogger(__name__)


class WhatsAppComposer(models.TransientModel):
    """Composer genérico de WhatsApp, análogo a mail.compose.message / sms.composer.

    Funciona en dos modos:

    - ``single``: un destinatario, se invoca desde la vista form de un registro
      (sale.order, crm.lead, etc) y postea el resultado al chatter de ese registro.
    - ``mass``: varios destinatarios, se invoca desde la vista list con varios
      registros seleccionados, o usado standalone (sin res_model) para envío
      manual a una lista de números.
    """

    _name = "whatsapp.composer"
    _description = "Composer de mensajes WhatsApp"

    instance_id = fields.Many2one(
        "evolution.instance",
        string="Instancia",
        required=True,
        domain="[('state','=','connected')]",
        default=lambda self: self._default_instance(),
    )

    # Contexto del registro origen
    res_model = fields.Char(string="Modelo")
    res_ids = fields.Text(string="IDs (JSON)")
    composition_mode = fields.Selection(
        [("single", "Único destinatario"), ("mass", "Masivo")],
        compute="_compute_composition_mode",
        store=True,
    )
    number_of_records = fields.Integer(
        compute="_compute_composition_mode",
        store=True,
    )

    # Plantilla
    template_id = fields.Many2one(
        "whatsapp.template",
        string="Plantilla",
        domain="[('model','=',res_model)]",
    )

    # Destinatario / contenido
    use_template_recipient = fields.Boolean(
        string="Usar teléfono del registro",
        default=True,
        help="Si está activo, el número se obtiene del registro origen "
             "(via la plantilla o auto-detección).",
    )
    manual_recipients = fields.Char(
        string="Destinatarios manuales",
        help="Uno o varios números separados por coma. Usá esto si querés "
             "enviar a alguien que no es el contacto del registro.",
    )

    message_type = fields.Selection(
        [
            ("text", "Texto"),
            ("image", "Imagen"),
            ("video", "Video"),
            ("document", "Documento (PDF, etc)"),
            ("audio", "Audio"),
        ],
        default="text",
        required=True,
    )
    body = fields.Text(string="Mensaje / Caption")
    source = fields.Selection(
        [("attachment", "Subir archivo"), ("url", "URL pública"),
         ("template", "Adjuntos de la plantilla")],
        default="attachment",
        string="Fuente",
    )
    attachment = fields.Binary(string="Archivo")
    attachment_filename = fields.Char(string="Nombre archivo")
    media_url = fields.Char(string="URL del archivo")
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "whatsapp_composer_attachment_rel",
        "composer_id",
        "attachment_id",
        string="Adjuntos",
    )
    delay_ms = fields.Integer(
        string="Demora (ms)",
        default=0,
        help="Demora entre cada mensaje en envíos masivos.",
    )
    log_in_chatter = fields.Boolean(
        string="Loggear en chatter",
        default=True,
        help="Postear el envío en el chatter del registro origen.",
    )

    # Resultado
    result_count_sent = fields.Integer(readonly=True)
    result_count_failed = fields.Integer(readonly=True)
    result_log = fields.Text(readonly=True)

    # ---------------- Defaults & computed ----------------

    @api.model
    def _default_instance(self):
        return self.env["evolution.instance"].search(
            [("state", "=", "connected")], limit=1
        )

    @api.depends("res_ids")
    def _compute_composition_mode(self):
        for rec in self:
            ids = rec._get_res_ids()
            count = len(ids)
            rec.number_of_records = count
            rec.composition_mode = "mass" if count > 1 else "single"

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        # Cuando se llama desde un binding de server action, viene
        # active_model / active_ids en el contexto
        ctx = self.env.context
        if "default_res_model" not in defaults and ctx.get("active_model"):
            defaults["res_model"] = ctx["active_model"]
        if "default_res_ids" not in defaults and ctx.get("active_ids"):
            defaults["res_ids"] = json.dumps(list(ctx["active_ids"]))
        return defaults

    # ---------------- Onchange ----------------

    @api.onchange("template_id")
    def _onchange_template_id(self):
        if not self.template_id:
            return
        self.instance_id = self.template_id.instance_id
        self.message_type = self.template_id.message_type
        if self.template_id.attachment_ids:
            self.source = "template"
            self.attachment_ids = self.template_id.attachment_ids
        # Renderear body para el primer registro como preview
        ids = self._get_res_ids()
        if ids:
            rendered = self.template_id._render_body_for_records(ids[:1])
            self.body = rendered.get(ids[0], self.template_id.body)
        else:
            self.body = self.template_id.body

    @api.onchange("res_model")
    def _onchange_res_model(self):
        # Limpiar template si no aplica
        if self.template_id and self.template_id.model != self.res_model:
            self.template_id = False

    # ---------------- Helpers ----------------

    def _get_res_ids(self):
        self.ensure_one()
        if not self.res_ids:
            return []
        try:
            ids = json.loads(self.res_ids)
            return [int(x) for x in ids if x]
        except (ValueError, TypeError):
            return []

    def _get_records(self):
        self.ensure_one()
        ids = self._get_res_ids()
        if not self.res_model or not ids:
            return self.env["res.partner"].browse()
        return self.env[self.res_model].browse(ids).exists()

    def _resolve_recipients(self):
        """Devuelve [(record_or_None, phone_normalized), ...]."""
        self.ensure_one()
        default_cc = (self.instance_id.evolution_default_country_code or "").strip()

        def normalize(raw):
            if not raw:
                return False
            norm = _normalize_number(raw)
            if default_cc and not norm.startswith(default_cc) and len(norm) <= 10:
                norm = default_cc + norm
            return norm

        result = []
        if self.manual_recipients:
            for raw in self.manual_recipients.replace(";", ",").split(","):
                num = normalize(raw.strip())
                if num:
                    result.append((None, num))

        if self.use_template_recipient:
            for rec in self._get_records():
                phone = (
                    self.template_id._get_phone_for_record(rec)
                    if self.template_id
                    else self._auto_detect_phone(rec)
                )
                num = normalize(phone)
                if num:
                    result.append((rec, num))

        if not result:
            raise UserError(
                _("No se pudieron resolver destinatarios. Verificá los "
                  "números o el campo de teléfono del modelo.")
            )
        return result

    def _auto_detect_phone(self, record):
        for fname in ("mobile", "phone"):
            val = getattr(record, fname, False)
            if val:
                return val
        partner = getattr(record, "partner_id", False)
        if partner:
            return partner.mobile or partner.phone or False
        return False

    def _render_body(self, record):
        self.ensure_one()
        if not record or not self.template_id:
            return self.body or ""
        # Render con la plantilla para este registro específico
        try:
            rendered = self.template_id._render_field("body", [record.id])
            return rendered.get(record.id, self.body or "")
        except Exception as exc:
            _logger.warning("Render template falló: %s", exc)
            return self.body or ""

    def _public_url_for_attachment(self, attachment):
        """Devuelve URL descargable pública para un ir.attachment.

        Se asegura de que tenga access_token para que el link sea usable
        sin autenticación (Evolution Go descarga el archivo desde ahí).
        """
        if not attachment.access_token:
            attachment.sudo().access_token = secrets.token_urlsafe(32)
        ICP = self.env["ir.config_parameter"].sudo()
        base = (
            self.instance_id.evolution_webhook_base_url
            or ICP.get_param("web.base.url")
            or ""
        ).rstrip("/")
        if not base:
            raise UserError(_(
                "Falta configurar 'web.base.url' (o la 'URL pública de Odoo' de la instancia) "
                "para generar URLs públicas de los adjuntos."
            ))
        return "%s/web/content/%s?access_token=%s&download=true" % (
            base, attachment.id, attachment.access_token,
        )

    def _create_persistent_attachment(self):
        """Persiste el binary subido en el composer como ir.attachment.

        Queda ligado a 'evolution.message' pero con res_id=0 hasta que el
        mensaje se cree y sepamos su ID (se actualiza después en action_send).
        """
        self.ensure_one()
        if not self.attachment:
            return None
        mimetype = (
            mimetypes.guess_type(self.attachment_filename or "")[0]
            or "application/octet-stream"
        )
        return self.env["ir.attachment"].sudo().create({
            "name": self.attachment_filename or "media",
            "datas": self.attachment,
            "mimetype": mimetype,
            "res_model": "evolution.message",
            "res_id": 0,
            "type": "binary",
        })

    def _resolve_attachments(self, record):
        """Devuelve lista de dicts con la info necesaria para el envío.

        Cada dict tiene: {url, attachment_id, filename, mimetype}.
        La URL siempre se manda a Evolution Go — nunca base64 en body.
        """
        self.ensure_one()
        if self.message_type == "text":
            return []
        if self.source == "url":
            if not self.media_url:
                raise UserError(_("Ingresá una URL para el archivo."))
            return [{
                "url": self.media_url,
                "attachment_id": None,
                "filename": None,
                "mimetype": None,
            }]
        attachments = []
        if self.source == "template":
            atts = self.template_id.attachment_ids if self.template_id else self.env["ir.attachment"]
            if self.template_id and self.template_id.report_template_id and record:
                report_attachments = self.template_id._generate_report_attachments(record)
                atts |= report_attachments.get(record.id, self.env["ir.attachment"])
            for att in atts:
                attachments.append({
                    "url": self._public_url_for_attachment(att),
                    "attachment_id": att.id,
                    "filename": att.name,
                    "mimetype": att.mimetype or "application/octet-stream",
                })
        elif self.source == "attachment" and self.attachment:
            att = self._create_persistent_attachment()
            if att:
                attachments.append({
                    "url": self._public_url_for_attachment(att),
                    "attachment_id": att.id,
                    "filename": att.name,
                    "mimetype": att.mimetype or "application/octet-stream",
                })
        return attachments

    # ---------------- Entry points ----------------

    @api.model
    def action_open_for_records(self, records):
        """Llamado por la server action binding."""
        if not records:
            raise UserError(_("No hay registros seleccionados."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Enviar por WhatsApp"),
            "res_model": "whatsapp.composer",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": records._name,
                "default_res_ids": json.dumps(records.ids),
            },
        }

    @api.model
    def action_open_standalone(self):
        """Abre el composer en modo manual (sin registro de origen)."""
        return {
            "type": "ir.actions.act_window",
            "name": _("Enviar mensaje WhatsApp"),
            "res_model": "whatsapp.composer",
            "view_mode": "form",
            "target": "new",
            "context": {"default_use_template_recipient": False},
        }

    # ---------------- Action ----------------

    def action_send(self):
        self.ensure_one()
        if self.instance_id.state != "connected":
            raise UserError(
                _("La instancia '%s' no está conectada.") % self.instance_id.name
            )
        if self.message_type == "text" and not (self.body or "").strip() and not self.template_id:
            raise ValidationError(_("Escribí un mensaje o elegí una plantilla."))

        api = self.instance_id._instance_api()
        recipients = self._resolve_recipients()

        sent, failed, log_lines = 0, 0, []
        Message = self.env["evolution.message"]

        for record, number in recipients:
            body = self._render_body(record)
            attachments = self._resolve_attachments(record)
            last_attachment = None
            try:
                if self.message_type == "text" or not attachments:
                    response = api.send_text(
                        number=number,
                        text=body,
                        delay=self.delay_ms or None,
                    )
                else:
                    response = None
                    for idx, att in enumerate(attachments):
                        response = api.send_media(
                            number=number,
                            media_type=self.message_type,
                            url=att["url"],
                            mimetype=att.get("mimetype"),
                            caption=body if idx == 0 else None,
                            filename=att.get("filename"),
                            delay=self.delay_ms or None,
                        )
                        last_attachment = att

                sent += 1
                log_lines.append("✓ %s" % number)
                msg = Message.create(
                    self._log_vals(record, number, body, "sent", response, last_attachment)
                )
                self._link_attachment_to_message(last_attachment, msg)
                if record and self.log_in_chatter:
                    self._post_to_chatter(record, number, body, success=True)
            except Exception as exc:
                failed += 1
                log_lines.append("✗ %s — %s" % (number, exc))
                _logger.exception("Error enviando a %s", number)
                msg = Message.create(
                    self._log_vals(
                        record, number, body, "failed", None, last_attachment, error=str(exc)
                    )
                )
                self._link_attachment_to_message(last_attachment, msg)
                if record and self.log_in_chatter:
                    self._post_to_chatter(record, number, body, success=False, error=str(exc))

        self.write({
            "result_count_sent": sent,
            "result_count_failed": failed,
            "result_log": "\n".join(log_lines),
        })

        if sent and not failed and self.composition_mode == "single":
            return {"type": "ir.actions.act_window_close"}

        return {
            "type": "ir.actions.act_window",
            "res_model": "whatsapp.composer",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _log_vals(self, record, number, body, state, response, attachment_info=None, error=None):
        vals = {
            "instance_id": self.instance_id.id,
            "direction": "outgoing",
            "recipient": number,
            "message_type": self.message_type,
            "body": body or False,
            "state": state,
            "error_message": error or False,
            "raw_response": str(response) if response else False,
            "sent_at": fields.Datetime.now() if state == "sent" else False,
        }
        if response:
            msg_id = (
                response.get("messageId")
                or (response.get("data") or {}).get("Info", {}).get("ID")
            )
            vals["external_id"] = msg_id
        if attachment_info:
            vals["media_url"] = attachment_info.get("url") or False
            vals["attachment_id"] = attachment_info.get("attachment_id") or False
            vals["filename"] = attachment_info.get("filename") or False
            vals["mimetype"] = attachment_info.get("mimetype") or False
        return vals

    def _link_attachment_to_message(self, attachment_info, message):
        """Une el ir.attachment recién creado con el evolution.message final.

        Solo aplica cuando el attachment es huérfano (res_id=0), típicamente
        cuando el user subió el archivo desde el composer (source=attachment).
        Los adjuntos de plantilla ya tienen su propio res_model/res_id.
        """
        if not attachment_info or not attachment_info.get("attachment_id") or not message:
            return
        att = self.env["ir.attachment"].sudo().browse(attachment_info["attachment_id"])
        if att.exists() and att.res_model == "evolution.message" and not att.res_id:
            att.write({"res_id": message.id})

    def _post_to_chatter(self, record, number, body, success=True, error=None):
        """Loguea el envío en el chatter del registro origen."""
        if not hasattr(record, "message_post"):
            return
        status_html = (
            '<span class="badge text-bg-success">Enviado</span>'
            if success
            else '<span class="badge text-bg-danger">Falló</span>'
        )
        type_label = dict(self._fields["message_type"].selection).get(self.message_type, "")
        body_html = (
            "<p><i class=\"fa fa-whatsapp\"/> <b>WhatsApp %(status)s</b> "
            "a <b>%(num)s</b> · %(type_)s</p>"
            "<blockquote>%(body)s</blockquote>"
        ) % {
            "status": status_html,
            "num": number,
            "type_": type_label,
            "body": (body or "").replace("\n", "<br/>"),
        }
        if error:
            body_html += '<p class="text-danger"><small>Error: %s</small></p>' % error
        try:
            record.message_post(
                body=body_html,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        except Exception as exc:
            _logger.warning("No se pudo postear al chatter: %s", exc)
