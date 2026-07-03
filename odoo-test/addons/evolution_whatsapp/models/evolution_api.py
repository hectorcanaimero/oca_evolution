import base64
import logging
from urllib.parse import urljoin

import requests

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


class EvolutionAPIError(UserError):
    """Error retornado por Evolution Go o por problemas de red."""


class EvolutionAPI:
    """Cliente HTTP minimalista para la Evolution Go API.

    Toda llamada usa el header ``apikey``. Para operaciones globales
    (crear instancia, listar todas) se usa la API key global del servidor.
    Para operaciones por instancia (QR, estado, envío) se usa el token
    de la instancia.
    """

    def __init__(self, base_url, apikey, instance_name=None, timeout=DEFAULT_TIMEOUT):
        if not base_url:
            raise EvolutionAPIError(_("Falta configurar la URL de Evolution Go."))
        if not apikey:
            raise EvolutionAPIError(_("Falta la API key (global o de instancia)."))
        self.base_url = base_url.rstrip("/") + "/"
        self.apikey = apikey
        self.instance_name = instance_name
        self.timeout = timeout

    def _headers(self, extra=None):
        headers = {
            "apikey": self.apikey,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method, path, json=None, params=None, extra_headers=None):
        url = urljoin(self.base_url, path.lstrip("/"))
        _logger.warning("Evolution %s %s payload=%s", method, url, json)
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(extra=extra_headers),
                json=json,
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise EvolutionAPIError(
                _("Error de red contactando Evolution Go: %s") % exc
            ) from exc

        _logger.warning(
            "Evolution %s %s → %s body=%s",
            method, url, response.status_code,
            (response.text or "")[:1000],
        )
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise EvolutionAPIError(
                _("Evolution Go devolvió %(code)s: %(detail)s") % {
                    "code": response.status_code,
                    "detail": detail,
                }
            )
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    # ---------------- Instancias ----------------

    def create_instance(self, name, token, proxy=None):
        payload = {"name": name, "token": token}
        if proxy:
            payload["proxy"] = proxy
        return self._request("POST", "/instance/create", json=payload)

    def fetch_all_instances(self):
        return self._request("GET", "/instance/all")

    def connect_instance(self, instance_id, webhook_url, subscribe=None, immediate=True):
        """POST /instance/connect — (re)registra el webhook de una instancia
        ya creada. Requiere el UUID remoto vía header 'instanceId', además
        del apikey global habitual."""
        payload = {
            "webhookUrl": webhook_url,
            "subscribe": subscribe or ["ALL"],
            "immediate": immediate,
        }
        return self._request(
            "POST", "/instance/connect",
            json=payload,
            extra_headers={"instanceId": instance_id},
        )

    def get_qr(self):
        """La instancia se resuelve por el header apikey (token de instancia), sin path param."""
        return self._request("GET", "/instance/qr")

    def get_status(self):
        """Idem get_qr: apikey = token de la instancia, sin path param."""
        return self._request("GET", "/instance/status")

    def logout(self):
        return self._request("DELETE", "/instance/logout")

    def delete_instance(self, instance_id):
        """instance_id es el UUID devuelto por create_instance (campo 'id'), no el name."""
        return self._request("DELETE", "/instance/delete/%s" % instance_id)

    # ---------------- Mensajería ----------------

    def send_text(self, number, text, delay=None, mention_all=False,
                  mentioned_jid=None, quoted=None):
        payload = {"number": _normalize_number(number), "text": text}
        if delay:
            payload["delay"] = int(delay)
        if mention_all:
            payload["mentionAll"] = True
        if mentioned_jid:
            payload["mentionedJid"] = mentioned_jid
        if quoted:
            payload["quoted"] = quoted
        return self._request("POST", "/send/text", json=payload)

    def send_media(self, number, media_type, url=None, base64_data=None,
                   mimetype=None, caption=None, filename=None, delay=None,
                   mention_all=False, mentioned_jid=None, quoted=None):
        """Envía media al endpoint POST /send/media de Evolution Go.

        Schema (según pkg/sendMessage/service/send_service.go MediaStruct):
            {number, url, type, caption, filename, delay, mentionedJid, mentionAll}
        El campo 'url' acepta URL http(s) O base64 crudo (Evolution decide
        por prefijo). No hay campo 'mimetype' — el server lo detecta.
        """
        if not url and not base64_data:
            raise EvolutionAPIError(
                _("Debés indicar URL o adjunto en base64 para enviar media.")
            )
        payload = {
            "number": _normalize_number(number),
            "type": media_type,
        }
        if url:
            payload["url"] = url
        elif base64_data:
            payload["url"] = base64_data
        if caption:
            payload["caption"] = caption
        if filename:
            payload["filename"] = filename
        if delay:
            payload["delay"] = int(delay)
        if mention_all:
            payload["mentionAll"] = True
        if mentioned_jid:
            payload["mentionedJid"] = mentioned_jid
        if quoted:
            payload["quoted"] = quoted
        return self._request("POST", "/send/media", json=payload)

    def send_location(self, number, latitude, longitude, name=None, address=None):
        payload = {
            "number": _normalize_number(number),
            "latitude": float(latitude),
            "longitude": float(longitude),
        }
        if name:
            payload["name"] = name
        if address:
            payload["address"] = address
        return self._request("POST", "/send/location", json=payload)

    def send_contact(self, number, full_name, phone, organization=None):
        """Evolution Go solo acepta un vcard por request (schema SendContact)."""
        vcard = {"fullName": full_name, "phone": phone}
        if organization:
            vcard["organization"] = organization
        payload = {
            "number": _normalize_number(number),
            "vcard": vcard,
        }
        return self._request("POST", "/send/contact", json=payload)

    def set_presence(self, number, state="composing", is_audio=False):
        payload = {
            "number": _normalize_number(number),
            "state": state,
        }
        if is_audio:
            payload["isAudio"] = True
        return self._request("POST", "/message/presence", json=payload)


def _normalize_number(number):
    """Saca espacios, guiones, paréntesis y el '+' inicial."""
    if not number:
        return number
    return (
        str(number)
        .strip()
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
        .lstrip("+")
    )


def file_to_base64(data_bytes):
    return base64.b64encode(data_bytes).decode("ascii")
