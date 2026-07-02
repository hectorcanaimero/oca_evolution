from odoo import fields, models


class EvolutionInstance(models.Model):
    _inherit = "evolution.instance"

    crm_integration_enabled = fields.Boolean(
        string="Crear leads de CRM automáticamente",
    )
    crm_team_id = fields.Many2one("crm.team", string="Equipo de ventas")
    crm_user_id = fields.Many2one("res.users", string="Vendedor por defecto")
    crm_reopen_days = fields.Integer(
        string="Reabrir lead si tuvo actividad en los últimos N días",
        default=30,
        help="0 = nunca reabrir; cada mensaje entrante sin lead reciente crea uno nuevo.",
    )
