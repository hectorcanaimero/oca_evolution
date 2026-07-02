"""Pre-init, post-init y uninstall hooks para el módulo Evolution WhatsApp."""
import logging

_logger = logging.getLogger(__name__)


# Modelos donde queremos que aparezca "Enviar por WhatsApp" automáticamente
# si están instalados. La key es el módulo que provee el modelo.
AUTO_BIND_MODELS = {
    "sale": "sale.order",
    "crm": "crm.lead",
    "account": "account.move",
    "purchase": "purchase.order",
    "project": "project.task",
    "helpdesk": "helpdesk.ticket",
    "stock": "stock.picking",
}

# Tablas Postgres a backfillear con company_id al hacer -u desde una versión
# pre-multitenant (v18.0.1.1.0 y anteriores). Todas apuntan al mismo:
# main_company (la primera res.company existente).
MULTITENANT_TABLES = (
    "evolution_instance",
    "evolution_message",
    "whatsapp_template",
)


def _pre_init_hook(env):
    """Backfill defensivo de company_id antes de que Odoo aplique NOT NULL.

    Ejecuta al -i y al -u del módulo. Si es una install fresh no hay data
    y los UPDATEs son no-op. Si es un upgrade desde v18.0.1.1.0, agrega la
    columna company_id (si no existe) y la puebla con el id de la primera
    res.company (main_company).
    """
    cr = env.cr
    cr.execute("SELECT id FROM res_company ORDER BY id LIMIT 1")
    row = cr.fetchone()
    if not row:
        # DB nueva, no hay companies aún. Odoo se encarga del default.
        return
    main_company_id = row[0]
    for table in MULTITENANT_TABLES:
        cr.execute(
            "SELECT to_regclass(%s)",
            (f"public.{table}",),
        )
        if cr.fetchone()[0] is None:
            # Tabla aún no existe (install fresh); Odoo la crea después.
            continue
        cr.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS company_id INTEGER"
        )
        cr.execute(
            f"UPDATE {table} SET company_id = %s WHERE company_id IS NULL",
            (main_company_id,),
        )
        _logger.info(
            "evolution_whatsapp pre_init: backfill company_id=%s en tabla %s",
            main_company_id, table,
        )


def _post_init_hook(env):
    """Crea bindings de la server action para modelos comunes instalados."""
    base_action = env.ref(
        "evolution_whatsapp.action_server_whatsapp_send_partner",
        raise_if_not_found=False,
    )
    if not base_action:
        _logger.warning("No se encontró la server action base; salteando bindings")
        return

    Model = env["ir.model"]
    Action = env["ir.actions.server"]

    for module_name, model_name in AUTO_BIND_MODELS.items():
        model = Model.search([("model", "=", model_name)], limit=1)
        if not model:
            continue
        existing = Action.search([
            ("binding_model_id", "=", model.id),
            ("name", "=", base_action.name),
        ], limit=1)
        if existing:
            continue
        Action.create({
            "name": base_action.name,
            "model_id": model.id,
            "binding_model_id": model.id,
            "binding_view_types": "form,list",
            "binding_type": "action",
            "state": "code",
            "code": base_action.code,
            "groups_id": [(6, 0, base_action.groups_id.ids)],
        })
        _logger.info(
            "evolution_whatsapp: binding creado para %s", model_name
        )


def _uninstall_hook(env):
    """Limpia las server actions creadas dinámicamente."""
    Action = env["ir.actions.server"]
    base_action = env.ref(
        "evolution_whatsapp.action_server_whatsapp_send_partner",
        raise_if_not_found=False,
    )
    base_name = base_action.name if base_action else "Enviar por WhatsApp"
    Action.search([
        ("name", "=", base_name),
        ("state", "=", "code"),
        ("code", "like", "whatsapp.composer"),
    ]).unlink()
