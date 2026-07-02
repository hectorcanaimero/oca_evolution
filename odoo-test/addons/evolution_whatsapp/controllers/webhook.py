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

    Los eventos típicos son: connection.update, messages.upsert, messages.update,
    qrcode.updated.
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

        event = payload.get("event") or payload.get("type") or ""
        try:
            if event in ("qrcode.updated", "qr"):
                self._handle_qr(instance, payload)
            elif event in ("connection.update", "connection"):
                self._handle_connection(instance, payload)
            elif event in ("messages.upsert", "messages.update", "message.ack"):
                self._handle_message(instance, payload)
            instance.last_event_at = fields.Datetime.now()
        except Exception as exc:
            _logger.exception("Evolution webhook handler falló: %s", exc)
            return {"ok": False, "error": str(exc)}

        return {"ok": True}

    def _handle_qr(self, instance, payload):
        data = payload.get("data") or {}
        qr = data.get("qrcode") or data.get("Qrcode") or data.get("base64")
        code = data.get("code") or data.get("Code")
        if qr:
            instance._store_qr(qr)
        if code:
            instance.qr_code_text = code
        if instance.state != "connected":
            instance.state = "qrcode"

    def _handle_connection(self, instance, payload):
        data = payload.get("data") or {}
        state = (data.get("state") or data.get("status") or "").lower()
        if state in ("open", "connected"):
            instance.write({
                "state": "connected",
                "phone_number": data.get("wuid") or data.get("phoneNumber")
                                or instance.phone_number,
                "qr_code": False,
                "qr_code_text": False,
            })
        elif state in ("close", "closed", "disconnected"):
            instance.state = "disconnected"
        elif state == "connecting":
            instance.state = "qrcode"

    def _handle_message(self, instance, payload):
        data = payload.get("data") or {}
        # Evolution suele venir con un array de mensajes o un único objeto
        messages = data if isinstance(data, list) else [data]
        Message = request.env["evolution.message"].sudo()
        for msg in messages:
            key = msg.get("key") or {}
            from_me = key.get("fromMe", False)
            remote_jid = key.get("remoteJid") or ""
            msg_id = key.get("id") or msg.get("id")
            status = (msg.get("status") or "").lower()

            existing = Message.search(
                [("external_id", "=", msg_id), ("instance_id", "=", instance.id)],
                limit=1,
            )

            new_state = {
                "pending": "sending",
                "server_ack": "sent",
                "delivery_ack": "delivered",
                "read": "read",
                "played": "read",
                "error": "failed",
            }.get(status)

            if existing:
                if new_state:
                    existing.state = new_state
                continue

            # Mensaje nuevo (probablemente entrante)
            body = self._extract_body(msg.get("message") or {})
            mtype = self._extract_type(msg.get("message") or {})
            Message.create({
                "instance_id": instance.id,
                "direction": "outgoing" if from_me else "incoming",
                "recipient": remote_jid.split("@")[0] if remote_jid else "",
                "message_type": mtype,
                "body": body,
                "external_id": msg_id,
                "state": new_state or ("sent" if from_me else "delivered"),
                "raw_response": json.dumps(msg)[:50000],
                "sent_at": fields.Datetime.now() if from_me else False,
            })

    def _extract_body(self, message):
        if not isinstance(message, dict):
            return ""
        if "conversation" in message:
            return message["conversation"]
        if "extendedTextMessage" in message:
            return (message["extendedTextMessage"] or {}).get("text", "")
        for key in ("imageMessage", "videoMessage", "documentMessage", "audioMessage"):
            if key in message:
                return (message[key] or {}).get("caption", "")
        return ""

    def _extract_type(self, message):
        if not isinstance(message, dict):
            return "text"
        mapping = {
            "imageMessage": "image",
            "videoMessage": "video",
            "documentMessage": "document",
            "audioMessage": "audio",
            "locationMessage": "location",
            "contactMessage": "contact",
        }
        for key, mtype in mapping.items():
            if key in message:
                return mtype
        return "text"
