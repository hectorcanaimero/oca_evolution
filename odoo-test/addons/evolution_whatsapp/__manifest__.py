{
    "name": "Evolution",
    "version": "18.0.3.0.0",
    "category": "Marketing",
    "summary": "Integración WhatsApp con Evolution Go API + plantillas + chatter",
    "description": """
Integración completa con Evolution Go para WhatsApp:

* Gestión de instancias (crear, conectar por QR, monitorear, eliminar)
* Envío de mensajes de texto, imagen, video, audio, documento (PDF)
* Plantillas tipo mail.template con QWeb ({{ object.name }})
* Composer integrado: aparece "Enviar por WhatsApp" en el menú Acción
  de partners, leads, presupuestos, facturas, compras, tareas y tickets
* Registro en el chatter del documento origen
* Webhook controller para eventos entrantes (contrato real de Evolution Go)
* Bandeja de WhatsApp nativa en Discuss: cada número que escribe abre un canal,
  los agentes responden sin salir de Odoo
* Permisos por grupo (Usuario / Administrador)
""",
    "author": "Héctor Velásquez",
    "website": "https://github.com/knaimero",
    "license": "LGPL-3",
    "depends": ["base", "mail"],
    "data": [
        "security/evolution_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "data/whatsapp_server_actions.xml",
        "views/evolution_instance_views.xml",
        "views/evolution_message_views.xml",
        "views/whatsapp_template_views.xml",
        "views/whatsapp_composer_views.xml",
        "views/evolution_menus.xml",
    ],
    "pre_init_hook": "_pre_init_hook",
    "post_init_hook": "_post_init_hook",
    "uninstall_hook": "_uninstall_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
