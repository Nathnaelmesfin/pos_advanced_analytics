# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_analytics_default_period = fields.Selection([
        ('today', 'Today'),
        ('yesterday', 'Yesterday'),
        ('this_week', 'This Week'),
        ('this_month', 'This Month'),
        ('this_year', 'This Year'),
    ], string='Default Dashboard Period',
        config_parameter='pos_advanced_analytics.default_period',
        default='today',
    )
    pos_analytics_refresh_interval = fields.Selection([
        ('0', 'Disabled'),
        ('30', '30 Seconds'),
        ('60', '1 Minute'),
        ('120', '2 Minutes'),
        ('300', '5 Minutes'),
    ], string='Dashboard Auto-Refresh Interval',
        config_parameter='pos_advanced_analytics.refresh_interval',
        default='60',
    )
    pos_analytics_default_branch_id = fields.Many2one(
        'pos.config', string='Default POS Branch',
    )
    pos_analytics_default_branch_param = fields.Char(
        config_parameter='pos_advanced_analytics.default_branch_id',
    )
    pos_analytics_enable_targets = fields.Boolean(
        string='Enable Target Cards on Dashboard',
        config_parameter='pos_advanced_analytics.enable_targets',
        default=True,
    )
    pos_analytics_enable_waiter = fields.Boolean(
        string='Enable Waiter Analytics',
        config_parameter='pos_advanced_analytics.enable_waiter',
        default=True,
    )
    pos_analytics_enable_cashier = fields.Boolean(
        string='Enable Cashier Analytics',
        config_parameter='pos_advanced_analytics.enable_cashier',
        default=True,
    )
    pos_analytics_timezone = fields.Char(
        string='Report Timezone',
        config_parameter='pos_advanced_analytics.timezone',
        default='Africa/Addis_Ababa',
    )

    @api.onchange('pos_analytics_default_branch_id')
    def _onchange_default_branch(self):
        if self.pos_analytics_default_branch_id:
            self.pos_analytics_default_branch_param = str(self.pos_analytics_default_branch_id.id)
        else:
            self.pos_analytics_default_branch_param = ''

    def set_values(self):
        super().set_values()
        if self.pos_analytics_default_branch_id:
            self.env['ir.config_parameter'].sudo().set_param(
                'pos_advanced_analytics.default_branch_id',
                str(self.pos_analytics_default_branch_id.id),
            )

    def get_values(self):
        res = super().get_values()
        param_val = self.env['ir.config_parameter'].sudo().get_param(
            'pos_advanced_analytics.default_branch_id', default=''
        )
        if param_val and param_val.isdigit():
            res['pos_analytics_default_branch_id'] = int(param_val)
        return res
