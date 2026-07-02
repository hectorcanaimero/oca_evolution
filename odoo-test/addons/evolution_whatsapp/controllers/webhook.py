import hmac
import json
import logging

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class EvolutionWebhookController(http.Controller):
    """Recibe eventos de Evolution Go.

    Apuntá el webhook de la instancia a:
        {odoo_base}/evolution/webhook/{token}

    (el token es el campo 'Token' de la instancia, visible en la pestaña
    Webhook una vez creada en Evolution Go).

    Contrato real (docs.evolutionfoundation.com.br/evolution-go/webhooks):
    envelope {event, data, instanceId, instanceToken}, eventos en PascalCase
    (QRCode, Connected, LoggedOut, Message, Receipt, ...).
    """

    @http.route(
        "/evolution/webhook/<string:token>",
        type="json",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def webhook(self, token, **kwargs):
        payload = request.get_json_data() or {}
        _logger.debug("Evolution webhook %s: %s", token, payload)

        instance = (
            request.env["evolution.instance"]
            .sudo()
            .search([("token", "=", token)], limit=1)
        )
        if not instance:
            return {"ok": False, "error": "instance_not_found"}

        if not hmac.compare_digest(payload.get("instanceToken") or "", instance.token or ""):
            _logger.warning("Evolution webhook: instanceToken no coincide para %s", instance.name)
            return {"ok": False, "error": "invalid_token"}

        event = payload.get("event") or ""
        data = payload.get("data") or {}
        try:
            if event == "QRCode":
                self._handle_qr(instance, data)
            elif event == "Connected":
                self._handle_connected(instance, data)
            elif event == "LoggedOut":
                self._handle_logged_out(instance)
            elif event == "Message":
                self._handle_message(instance, data)
            elif event == "Receipt":
                self._handle_receipt(instance, payload)
            instance.last_event_at = fields.Datetime.now()
        except Exception as exc:
            _logger.exception("Evolution webhook handler falló (%s): %s", event, exc)
            return {"ok": False, "error": str(exc)}

        return {"ok": True}

    def _handle_qr(self, instance, data):
        qr = data.get("qrcode")
        code = data.get("code")
        if qr:
            instance._store_qr(qr)
        if code:
            instance.qr_code_text = code
        if instance.state != "connected":
            instance.state = "qrcode"

    def _handle_connected(self, instance, data):
        jid = data.get("jid") or ""
        instance.write({
            "state": "connected",
            "phone_number": jid.split(":")[0] if jid else instance.phone_number,
            "qr_code": False,
            "qr_code_text": False,
        })

    def _handle_logged_out(self, instance):
        instance.state = "disconnected"

    def _handle_message(self, instance, data):
        info = data.get("Info") or {}
        msg_id = info.get("ID")
        from_me = bool(info.get("IsFromMe"))
        is_group = bool(info.get("IsGroup"))
        chat_jid = info.get("Chat") or ""
        phone = chat_jid.split("@")[0] if chat_jid else ""

        Message = request.env["evolution.message"].sudo()
        if msg_id and Message.search_count(
            [("external_id", "=", msg_id), ("instance_id", "=", instance.id)]
        ):
            return  # ya registrado (reintento del webhook o eco de un envío propio)

        body = self._extract_body(data.get("Message") or {})
        mtype = info.get("MediaType") or "text"
        selection_values = dict(Message._fields["message_type"].selection)

        Message.create({
            "instance_id": instance.id,
            "direction": "outgoing" if from_me else "incoming",
            "recipient": phone,
            "message_type": mtype if mtype in selection_values else "text",
            "body": body,
            "external_id": msg_id,
            "state": "sent" if from_me else "delivered",
            "raw_response": json.dumps(data)[:50000],
            "sent_at": fields.Datetime.now() if from_me else False,
        })

        # v1 del inbox nativo en Discuss: solo texto entrante 1:1.
        # Grupos y adjuntos quedan afuera por ahora (ver evolution_go_discuss_inbox memory).
        if is_group or from_me or not phone or not body:
            return

        channel = request.env["discuss.channel"].sudo()._evolution_get_or_create(
            instance, phone, info.get("PushName")
        )
        channel.with_context(evolution_skip_send=True).message_post(
            body=body,
            author_id=channel.evolution_partner_id.id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )

    def _handle_receipt(self, instance, payload):
        data = payload.get("data") or {}
        state = (payload.get("state") or "").lower()
        new_state = {"read": "read", "readself": "read", "delivered": "delivered"}.get(state)
        msg_ids = data.get("MessageIDs") or []
        if not new_state or not msg_ids:
            return
        request.env["evolution.message"].sudo().search([
            ("instance_id", "=", instance.id),
            ("external_id", "in", msg_ids),
        ]).write({"state": new_state})

    def _extract_body(self, message):
        if not isinstance(message, dict):
            return ""
        if "conversation" in message:
            return message["conversation"]
        if "extendedTextMessage" in message:
            return (message["extendedTextMessage"] or {}).get("text", "")
        return ""
