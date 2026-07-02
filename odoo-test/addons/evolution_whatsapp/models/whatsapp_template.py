import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WhatsAppTemplate(models.Model):
    """Plantilla reutilizable de WhatsApp, análoga a mail.template / sms.template.

    Usa mail.render.mixin para soportar QWeb inline (``{{ object.partner_id.name }}``)
    y resolver dotted-field paths para el destinatario.
    """

    _name = "whatsapp.template"
    _description = "Plantilla de WhatsApp"
    _inherit = ["mail.render.mixin"]
    _order = "name"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    model_id = fields.Many2one(
        "ir.model",
        string="Modelo aplicable",
        required=True,
        ondelete="cascade",
        domain=[("transient", "=", False)],
        help="Modelo de Odoo sobre el que aplica esta plantilla "
             "(ej: sale.order, crm.lead, account.move).",
    )
    model = fields.Char(
        related="model_id.model",
        string="Model Name",
        store=True,
        index=True,
        readonly=True,
    )
    render_model = fields.Char(
        related="model_id.model",
        store=False,
    )
    instance_id = fields.Many2one(
        "evolution.instance",
        string="Instancia",
        required=True,
        ondelete="restrict",
        help="Instancia desde la cual se enviará el mensaje.",
    )
    body = fields.Text(
        string="Cuerpo del mensaje",
        translate=True,
        required=True,
        help="Texto con placeholders QWeb. Ej: "
             "Hola {{ object.partner_id.name }}, tu pedido "
             "{{ object.name }} ya fue confirmado.",
    )
    phone_field = fields.Char(
        string="Campo de teléfono",
        help="Nombre del campo (admite dotted notation) que contiene el "
             "número del destinatario. Ej: 'mobile', 'partner_id.mobile', "
             "'partner_id.phone'. Si está vacío se intenta auto-detectar.",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "whatsapp_template_attachment_rel",
        "template_id",
        "attachment_id",
        string="Adjuntos",
        help="Archivos a enviar junto al mensaje (uno por mensaje).",
    )
    message_type = fields.Selection(
        [
            ("text", "Solo texto"),
            ("image", "Imagen"),
            ("video", "Video"),
            ("document", "Documento (PDF, etc)"),
            ("audio", "Audio"),
        ],
        default="text",
        required=True,
        string="Tipo",
        help="Si es distinto de 'texto' el cuerpo se envía como caption del "
             "primer adjunto.",
    )
    report_template_id = fields.Many2one(
        "ir.actions.report",
        string="Reporte a adjuntar",
        domain="[('model','=',model)]",
        help="Si se setea, el reporte se genera y adjunta automáticamente "
             "para cada registro al enviar.",
    )
    use_default_recipient = fields.Boolean(
        string="Auto-detectar teléfono",
        default=True,
        help="Si no se setea 'Campo de teléfono', usa mobile/phone del "
             "partner_id (o del propio record si es res.partner).",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
        help="Empresa (tenant CitaSpot) dueña de esta plantilla.",
    )

    @api.constrains("phone_field", "model_id")
    def _check_phone_field(self):
        for tpl in self:
            if not tpl.phone_field:
                continue
            model = self.env.get(tpl.model_id.model)
            if model is None:
                continue
            try:
                tpl._resolve_field_path(model, tpl.phone_field)
            except KeyError as exc:
                raise UserError(
                    _("Campo de teléfono inválido: %s") % exc
                ) from exc

    def _resolve_field_path(self, model, path):
        """Valida y devuelve el último campo del dotted path."""
        current_model = model
        parts = path.split(".")
        for i, part in enumerate(parts):
            field = current_model._fields.get(part)
            if not field:
                raise KeyError(
                    "%s no existe en %s" % (part, current_model._name)
                )
            if i < len(parts) - 1:
                if field.type not in ("many2one", "one2one"):
                    raise KeyError(
                        "%s no es relacional, no se puede seguir con '%s'"
                        % (part, parts[i + 1])
                    )
                current_model = self.env[field.comodel_name]
        return field

    def _get_phone_for_record(self, record):
        """Devuelve el número de teléfono normalizable para un registro."""
        self.ensure_one()
        if self.phone_field:
            obj = record
            for part in self.phone_field.split("."):
                if not obj:
                    return False
                obj = getattr(obj, part, False)
            return obj or False
        if not self.use_default_recipient:
            return False
        # Auto-detect: record.mobile/phone o partner_id.mobile/phone
        for fname in ("mobile", "phone"):
            val = getattr(record, fname, False)
            if val:
                return val
        partner = getattr(record, "partner_id", False)
        if partner:
            return partner.mobile or partner.phone or False
        return False

    def _render_body_for_records(self, res_ids):
        """Devuelve {record_id: rendered_body_str}."""
        self.ensure_one()
        if not res_ids:
            return {}
        return self._render_field("body", res_ids, compute_lang=True)

    def _generate_report_attachments(self, records):
        """Si hay report_template_id, genera el PDF para cada registro."""
        self.ensure_one()
        if not self.report_template_id:
            return {}
        result = {}
        Report = self.env["ir.actions.report"]
        for record in records:
            data, _mime = Report._render_qweb_pdf(
                self.report_template_id.report_name, [record.id]
            )
            attachment = self.env["ir.attachment"].create({
                "name": "%s.pdf" % (record.display_name or record._name),
                "datas": base64.b64encode(data),
                "res_model": record._name,
                "res_id": record.id,
                "mimetype": "application/pdf",
            })
            result[record.id] = attachment
        return result

    def action_open_composer(self):
        """Abre el composer con esta plantilla pre-seleccionada."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Enviar por WhatsApp"),
            "res_model": "whatsapp.composer",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_template_id": self.id,
                "default_res_model": self.model,
            },
        }
