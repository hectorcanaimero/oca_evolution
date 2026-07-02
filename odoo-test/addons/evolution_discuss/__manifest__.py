{
    "name": "Evolution - Discuss",
    "version": "18.0.1.0.0",
    "category": "Marketing",
    "summary": "Bandeja de WhatsApp nativa en Discuss, sin depender de Chatwoot",
    "description": """
Puente entre Evolution WhatsApp y Discuss:

* Cada número que escribe abre su propio canal en Discuss
* Crea un contacto liviano (res.partner) por número si no existe uno
* Agrega automáticamente a los agentes (grupo evolution_whatsapp.group_evolution_user)
* Responder desde el canal reenvía el mensaje por WhatsApp
* v1: solo texto 1:1, sin adjuntos ni SLA/routing — para eso conviene Chatwoot
""",
    "author": "Héctor Velásquez",
    "website": "https://github.com/knaimero",
    "license": "LGPL-3",
    "depends": ["evolution_whatsapp", "mail"],
    "data": [],
    "installable": True,
    "auto_install": False,
    "application": False,
}
