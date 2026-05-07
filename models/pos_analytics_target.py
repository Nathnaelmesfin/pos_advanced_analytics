# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class PosAnalyticsTarget(models.Model):
    _name = 'pos.analytics.target'
    _description = 'POS Sales Target'
    _order = 'date_start desc, name'

    name = fields.Char(string='Target Name', required=True)
    date_start = fields.Date(string='Start Date', required=True)
    date_end = fields.Date(string='End Date', required=True)
    pos_config_id = fields.Many2one(
        'pos.config', string='POS Branch / Shop',
        help='Leave empty for company-wide target.',
    )
    cashier_id = fields.Many2one(
        'res.users', string='Cashier',
        help='Leave empty for branch-level or general target.',
    )
    waiter_id = fields.Many2one(
        'hr.employee', string='Waiter',
        help='Leave empty for branch-level or general target.',
    )
    target_amount = fields.Float(string='Sales Target Amount', required=True, digits=(16, 2))
    target_orders = fields.Integer(string='Orders Target')
    target_avg_order_value = fields.Float(string='Average Order Value Target', digits=(16, 2))
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
        required=True,
    )
    notes = fields.Text(string='Notes')
    target_level = fields.Selection([
        ('company', 'Company Wide'),
        ('branch', 'Branch / Shop'),
        ('cashier', 'Cashier'),
        ('waiter', 'Waiter'),
    ], string='Target Level', compute='_compute_target_level', store=True)

    @api.depends('pos_config_id', 'cashier_id', 'waiter_id')
    def _compute_target_level(self):
        for rec in self:
            if rec.cashier_id:
                rec.target_level = 'cashier'
            elif rec.waiter_id:
                rec.target_level = 'waiter'
            elif rec.pos_config_id:
                rec.target_level = 'branch'
            else:
                rec.target_level = 'company'

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_start and rec.date_end and rec.date_start > rec.date_end:
                raise ValidationError(_('Start Date must be before End Date.'))

    @api.constrains('target_amount')
    def _check_target_amount(self):
        for rec in self:
            if rec.target_amount <= 0:
                raise ValidationError(_('Target Amount must be greater than zero.'))
