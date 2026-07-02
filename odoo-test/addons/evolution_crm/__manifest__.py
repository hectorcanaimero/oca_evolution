{
    "name": "Evolution - CRM",
    "version": "18.0.1.0.0",
    "category": "Marketing",
    "summary": "Crea leads de CRM automáticamente desde mensajes de WhatsApp entrantes",
    "description": """
Puente entre Evolution WhatsApp y CRM:

* Pestaña "Integraciones > CRM" en la instancia para activar y configurar
* Mensaje entrante sin lead abierto reciente → crea un crm.lead automático
* Matchea el número contra partners y leads existentes antes de crear uno nuevo
* Se instala solo (auto_install) cuando 'evolution_whatsapp' y 'crm' están
  ambos presentes; evolution_whatsapp sigue sin depender de CRM.
""",
    "author": "Héctor Velásquez",
    "website": "https://github.com/knaimero",
    "license": "LGPL-3",
    "depends": ["evolution_whatsapp", "crm"],
    "data": [
        "views/evolution_instance_views.xml",
        "views/evolution_message_views.xml",
    ],
    "installable": True,
    "auto_install": True,
    "application": False,
}
