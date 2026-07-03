import base64
import logging
import secrets
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .evolution_api import EvolutionAPI, EvolutionAPIError

_logger = logging.getLogger(__name__)


class EvolutionInstance(models.Model):
    _name = "evolution.instance"
    _description = "Instancia de WhatsApp en Evolution Go"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(
        string="Nombre",
        required=True,
        copy=False,
        tracking=True,
        help="Identificador único de la instancia en Evolution Go.",
    )
    token = fields.Char(
        string="Token",
        copy=False,
        readonly=True,
        index=True,
        help="Token único de la instancia. Se usa como apikey en operaciones "
             "específicas de esta instancia y para identificarla en el webhook.",
    )
    remote_id = fields.Char(
        string="ID remoto",
        copy=False,
        readonly=True,
        help="UUID devuelto por Evolution Go al crear la instancia. Requerido para borrarla.",
    )
    state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("qrcode", "Esperando QR"),
            ("connected", "Conectado"),
            ("disconnected", "Desconectado"),
            ("error", "Error"),
        ],
        default="draft",
        tracking=True,
        copy=False,
    )
    qr_code = fields.Binary(
        string="Código QR",
        attachment=False,
        copy=False,
        help="Imagen PNG del QR para emparejar con WhatsApp.",
    )
    qr_code_text = fields.Char(
        string="Pairing Code",
        copy=False,
        help="Código de emparejamiento alfanumérico alternativo al QR.",
    )
    qr_fetched_at = fields.Datetime(copy=False)
    phone_number = fields.Char(
        string="Número conectado",
        copy=False,
        readonly=True,
        tracking=True,
    )
    proxy_address = fields.Char(string="Proxy host")
    proxy_port = fields.Char(string="Proxy puerto")
    proxy_username = fields.Char(string="Proxy usuario")
    proxy_password = fields.Char(string="Proxy password")
    evolution_api_url = fields.Char(string="URL Evolution Go")
    evolution_global_apikey = fields.Char(string="API Key global")
    evolution_webhook_base_url = fields.Char(
        string="URL pública de Odoo",
        help="Si se deja vacío se usa la URL base de Odoo (web.base.url).",
    )
    evolution_default_country_code = fields.Char(
        string="Código de país por defecto", default="54"
    )
    webhook_url = fields.Char(
        string="Webhook URL",
        compute="_compute_webhook_url",
        store=False,
    )
    message_ids = fields.One2many(
        "evolution.message",
        "instance_id",
        string="Mensajes",
    )
    message_count = fields.Integer(
        compute="_compute_message_count",
    )
    last_event_at = fields.Datetime(copy=False, readonly=True)
    last_error = fields.Text(copy=False, readonly=True)
    test_recipient = fields.Char(
        string="Número de prueba",
        help="Número en formato internacional sin '+'. Ej: 5491155556666",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
        help="Empresa (tenant CitaSpot) dueña de esta instancia de WhatsApp.",
    )

    _sql_constraints = [
        (
            "company_unique",
            "unique(company_id)",
            "Ya existe una instancia de Evolution para esta empresa. Solo se permite una por empresa.",
        ),
    ]

    @api.constrains("name")
    def _check_name(self):
        for rec in self:
            if not rec.name:
                continue
            if not all(c.isalnum() or c in "-_" for c in rec.name):
                raise ValidationError(
                    _("El nombre solo puede contener letras, números, '-' y '_'.")
                )

    def _compute_message_count(self):
        groups = self.env["evolution.message"]._read_group(
            domain=[("instance_id", "in", self.ids)],
            groupby=["instance_id"],
            aggregates=["__count"],
        )
        counts = {instance.id: count for instance, count in groups}
        for rec in self:
            rec.message_count = counts.get(rec.id, 0)

    def _compute_webhook_url(self):
        ICP = self.env["ir.config_parameter"].sudo()
        for rec in self:
            base = (rec.evolution_webhook_base_url or ICP.get_param("web.base.url") or "").rstrip("/")
            if base and rec.token:
                rec.webhook_url = "%s/evolution/webhook/%s" % (base, rec.token)
            else:
                rec.webhook_url = False

    # ---------------- API helpers ----------------

    def _get_base_url(self):
        self.ensure_one()
        if not self.evolution_api_url:
            raise UserError(
                _("Configurá la URL de Evolution Go en la pestaña 'Conexión' de esta instancia.")
            )
        return self.evolution_api_url

    def _global_api(self):
        self.ensure_one()
        return EvolutionAPI(
            base_url=self.evolution_api_url,
            apikey=self.evolution_global_apikey,
        )

    def _instance_api(self):
        self.ensure_one()
        if not self.token:
            raise UserError(
                _("La instancia '%s' aún no tiene token. Creala primero en Evolution.")
                % self.name
            )
        return EvolutionAPI(
            base_url=self._get_base_url(),
            apikey=self.token,
            instance_name=self.name,
        )

    def _proxy_payload(self):
        self.ensure_one()
        if not self.proxy_address:
            return None
        return {
            "address": self.proxy_address or "",
            "port": self.proxy_port or "",
            "username": self.proxy_username or "",
            "password": self.proxy_password or "",
        }

    def _sync_remote_id(self):
        """Si remote_id está vacío, lo recupera listando instancias en
        Evolution Go y matcheando por name o token. No falla si no hay
        match — devuelve False y el llamador decide si es fatal."""
        self.ensure_one()
        if self.remote_id:
            return True
        try:
            response = self._global_api().fetch_all_instances()
        except EvolutionAPIError as exc:
            _logger.warning(
                "No se pudo listar instancias para sync remote_id de %s: %s",
                self.name, exc,
            )
            return False
        for item in (response or {}).get("data") or []:
            if item.get("name") == self.name or (self.token and item.get("token") == self.token):
                if item.get("id"):
                    self.write({"remote_id": item["id"]})
                    return True
        return False

    # ---------------- Actions ----------------

    def action_create_in_evolution(self):
        """Crea la instancia en el servidor Evolution Go."""
        for rec in self:
            if not rec.token:
                rec.token = secrets.token_urlsafe(24)
            try:
                api_global = rec._global_api()
                response = api_global.create_instance(
                    name=rec.name,
                    token=rec.token,
                    proxy=rec._proxy_payload(),
                )
            except EvolutionAPIError as exc:
                rec.write({"state": "error", "last_error": str(exc)})
                raise
            data = (response or {}).get("data") or response or {}
            rec.write({
                "state": "qrcode",
                "last_error": False,
                "last_event_at": fields.Datetime.now(),
                "remote_id": data.get("id") or data.get("Id") or data.get("ID") or rec.remote_id,
            })
            qr_value = (
                data.get("qrcode")
                or data.get("Qrcode")
                or data.get("qr")
                or data.get("QR")
            )
            code_value = data.get("Code") or data.get("code") or data.get("pairingCode")
            if qr_value:
                rec._store_qr(qr_value)
            if code_value and not rec.qr_code_text:
                rec.qr_code_text = code_value
            if not rec.qr_code:
                try:
                    qr_response = rec._instance_api().get_qr()
                except EvolutionAPIError as exc:
                    _logger.warning(
                        "No se pudo traer QR tras crear %s: %s", rec.name, exc
                    )
                else:
                    qr_data = (qr_response or {}).get("data") or qr_response or {}
                    qr_extra = (
                        qr_data.get("qrcode")
                        or qr_data.get("Qrcode")
                        or qr_data.get("qr")
                        or qr_data.get("base64")
                    )
                    if qr_extra:
                        rec._store_qr(qr_extra)
                    code_extra = (
                        qr_data.get("code")
                        or qr_data.get("pairingCode")
                    )
                    if code_extra:
                        rec.qr_code_text = code_extra
            rec.message_post(body=_("Instancia creada en Evolution Go."))
            try:
                rec.action_register_webhook()
            except (EvolutionAPIError, UserError) as exc:
                _logger.warning(
                    "No se pudo registrar el webhook automáticamente para %s: %s",
                    rec.name, exc,
                )
                rec.message_post(body=_(
                    "⚠️ No se pudo registrar el webhook automáticamente (%s). "
                    "Hacelo manual desde la pestaña Webhook."
                ) % exc)
        return True

    def action_refresh_qr(self):
        for rec in self:
            try:
                response = rec._instance_api().get_qr()
            except EvolutionAPIError as exc:
                rec.write({"state": "error", "last_error": str(exc)})
                raise
            data = (response or {}).get("data") or response or {}
            qr_value = (
                data.get("Qrcode")
                or data.get("qrcode")
                or data.get("qr")
                or data.get("base64")
            )
            code = (
                data.get("Code")
                or data.get("code")
                or data.get("pairingCode")
            )
            if not qr_value:
                rec.message_post(body=_("Evolution no devolvió un QR; ¿ya conectado?"))
            else:
                rec._store_qr(qr_value)
                rec.qr_code_text = code or False
                rec.qr_fetched_at = fields.Datetime.now()
                rec.state = "qrcode"
        return self._open_form()

    def action_refresh_state(self):
        for rec in self:
            try:
                response = rec._instance_api().get_status()
            except EvolutionAPIError as exc:
                rec.write({"state": "error", "last_error": str(exc)})
                raise
            data = (response or {}).get("data") or {}
            connected = data.get("connected") or data.get("Connected")
            phone = data.get("phoneNumber") or data.get("PhoneNumber") or data.get("jid")
            if connected:
                rec.write({
                    "state": "connected",
                    "phone_number": phone or rec.phone_number,
                    "qr_code": False,
                    "qr_code_text": False,
                    "last_event_at": fields.Datetime.now(),
                    "last_error": False,
                })
            elif phone and not connected:
                rec.write({
                    "state": "disconnected",
                    "last_event_at": fields.Datetime.now(),
                })
        return True

    def action_logout(self):
        for rec in self:
            try:
                rec._instance_api().logout()
            except EvolutionAPIError as exc:
                rec.write({"last_error": str(exc)})
                raise
            rec.write({
                "state": "disconnected",
                "qr_code": False,
                "qr_code_text": False,
                "phone_number": False,
            })
            rec.message_post(body=_("Sesión cerrada en Evolution Go."))
        return True

    def action_delete_in_evolution(self):
        for rec in self:
            if not rec.remote_id:
                raise UserError(_("La instancia no tiene ID remoto; no se creó en Evolution o ya fue borrada."))
            try:
                rec._instance_api().delete_instance(rec.remote_id)
            except EvolutionAPIError as exc:
                rec.write({"last_error": str(exc)})
                raise
            rec.write({
                "state": "draft",
                "token": False,
                "remote_id": False,
                "qr_code": False,
                "qr_code_text": False,
                "phone_number": False,
            })
            rec.message_post(body=_("Instancia eliminada del servidor."))
        return True

    def action_register_webhook(self):
        """A diferencia del fetch de QR (best-effort, solo logea), esta
        acción SÍ debe fallar visiblemente si Evolution Go la rechaza —
        registrar el webhook es el propósito explícito del botón."""
        for rec in self:
            if not rec._sync_remote_id():
                raise UserError(_(
                    "No se pudo determinar el ID remoto de '%s' en Evolution Go. "
                    "Verificá que la instancia exista ahí y que nombre/token coincidan."
                ) % rec.name)
            if not rec.webhook_url:
                raise UserError(_(
                    "No se pudo calcular la URL del webhook. Configurá 'URL pública "
                    "de Odoo' en la pestaña Webhook."
                ))
            try:
                rec._global_api().connect_instance(
                    instance_id=rec.remote_id,
                    webhook_url=rec.webhook_url,
                    subscribe=["ALL"],
                    immediate=True,
                )
            except EvolutionAPIError as exc:
                rec.write({"last_error": str(exc)})
                raise  # sin swallow: el usuario ve el error real de Evolution Go
            rec.write({"last_error": False})
            rec.message_post(body=_("Webhook registrado en Evolution Go: %s") % rec.webhook_url)
        return True

    def unlink(self):
        skip_remote = self.env.context.get("evolution_skip_remote_delete")
        for rec in self:
            if skip_remote or not rec.token or rec.state == "draft" or not rec.remote_id:
                continue
            try:
                rec._instance_api().delete_instance(rec.remote_id)
                _logger.info(
                    "Instancia '%s' borrada de Evolution Go antes de unlink", rec.name
                )
            except EvolutionAPIError as exc:
                msg = str(exc).lower()
                if "404" in msg or "not found" in msg or "not exist" in msg:
                    _logger.info(
                        "Instancia '%s' ya no existía en Evolution Go (%s); continuo unlink local",
                        rec.name, exc,
                    )
                    continue
                raise UserError(_(
                    "No se pudo eliminar la instancia '%(name)s' de Evolution Go: "
                    "%(error)s\n\nSi querés borrarla solo localmente, usá el menú "
                    "Acciones → 'Forzar borrado local'."
                ) % {"name": rec.name, "error": exc})
        return super().unlink()

    def action_force_local_unlink(self):
        """Permite borrar el record en Odoo sin contactar Evolution Go.
        Útil cuando la instancia ya no existe en el servidor o el server está caído."""
        return self.with_context(evolution_skip_remote_delete=True).unlink()

    def action_open_send_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Enviar mensaje"),
            "res_model": "whatsapp.composer",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_instance_id": self.id,
                "default_use_template_recipient": False,
            },
        }

    def action_send_test_message(self):
        self.ensure_one()
        if self.state != "connected":
            raise UserError(_("La instancia debe estar conectada para enviar un mensaje de prueba."))
        if not self.test_recipient:
            raise UserError(_("Ingresá un número de destino para la prueba."))
        message = self.env["evolution.message"].create({
            "instance_id": self.id,
            "direction": "outgoing",
            "recipient": self.test_recipient,
            "message_type": "text",
            "body": _("✅ Mensaje de prueba enviado desde Odoo — %s")
            % fields.Datetime.now(),
        })
        message._send_text()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Mensaje de prueba enviado"),
                "message": _("Revisá el WhatsApp de %s.") % self.test_recipient,
                "type": "success",
            },
        }

    def action_view_messages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Mensajes"),
            "res_model": "evolution.message",
            "view_mode": "list,form",
            "domain": [("instance_id", "=", self.id)],
            "context": {"default_instance_id": self.id},
        }

    def _store_qr(self, qr_value):
        """Acepta:
        - string 'data:image/png;base64,...'
        - string base64 puro
        - dict {'base64': '...', 'code': '...', 'pairingCode': '...'}
        """
        self.ensure_one()
        if not qr_value:
            return
        if isinstance(qr_value, dict):
            code = qr_value.get("code") or qr_value.get("pairingCode")
            if code and not self.qr_code_text:
                self.qr_code_text = code
            qr_value = (
                qr_value.get("base64")
                or qr_value.get("qrcode")
                or qr_value.get("qr")
            )
            if not qr_value:
                _logger.warning(
                    "QR dict sin campo base64/qrcode/qr para %s: keys=%s",
                    self.name, list((qr_value or {}).keys()) if isinstance(qr_value, dict) else None,
                )
                return
        if not isinstance(qr_value, str):
            _logger.warning(
                "QR recibido tipo inesperado para %s: %s", self.name, type(qr_value)
            )
            return
        if "," in qr_value and qr_value.startswith("data:"):
            payload = qr_value.split(",", 1)[1]
        else:
            payload = qr_value
        try:
            base64.b64decode(payload, validate=True)
            self.qr_code = payload
            self.qr_fetched_at = fields.Datetime.now()
        except (ValueError, base64.binascii.Error):
            _logger.warning("QR recibido no es base64 válido para %s", self.name)

    def _open_form(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "current",
        }

    @api.model
    def action_open_singleton(self):
        """Abre la instancia de la compañía actual, creándola si todavía no existe."""
        company = self.env.company
        instance = self.search([("company_id", "=", company.id)], limit=1)
        if not instance:
            slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in (company.name or "whatsapp"))
            instance = self.create({"company_id": company.id, "name": slug[:50] or "whatsapp"})
        return instance._open_form()

    # ---------------- Hooks de extensión ----------------

    def _notify_inbound_message(self, phone, body, push_name):
        """No-op por defecto. Módulos puente (evolution_discuss, evolution_crm,
        etc.) lo sobreescriben para reaccionar a un mensaje de texto entrante
        1:1 sin acoplar este módulo a lo que hagan con él."""
        return

    # ---------------- Cron ----------------

    @api.model
    def _cron_sync_state(self):
        instances = self.search([("state", "in", ["qrcode", "connected"])])
        for inst in instances:
            try:
                inst.action_refresh_state()
            except Exception as exc:
                _logger.warning("Sync estado falló para %s: %s", inst.name, exc)
