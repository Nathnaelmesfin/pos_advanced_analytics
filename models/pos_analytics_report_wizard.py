# -*- coding: utf-8 -*-
import base64
import io
import logging
from datetime import datetime

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosAnalyticsReportWizard(models.TransientModel):
    _name = 'pos.analytics.report.wizard'
    _description = 'POS Analytics Report Wizard'

    date_start = fields.Date(
        string='Start Date', required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
    )
    date_end = fields.Date(
        string='End Date', required=True,
        default=fields.Date.context_today,
    )
    pos_config_ids = fields.Many2many(
        'pos.config', string='POS Branch / Shop',
        help='Leave empty to include all branches.',
    )
    cashier_ids = fields.Many2many(
        'res.users', 'analytics_wizard_cashier_rel', 'wizard_id', 'user_id',
        string='Cashiers',
    )
    waiter_ids = fields.Many2many(
        'hr.employee', 'analytics_wizard_waiter_rel', 'wizard_id', 'employee_id',
        string='Waiters',
    )
    product_category_ids = fields.Many2many(
        'product.category', string='Product Categories',
    )
    product_ids = fields.Many2many(
        'product.product', string='Products',
    )
    payment_method_ids = fields.Many2many(
        'pos.payment.method', string='Payment Methods',
    )
    order_state = fields.Selection([
        ('all', 'All Valid States'),
        ('paid', 'Paid'),
        ('done', 'Done'),
        ('invoiced', 'Invoiced'),
    ], string='Order State', default='all')
    report_type = fields.Selection([
        ('sales_summary', 'Sales Summary Report'),
        ('daily_closing', 'Daily Closing Report'),
        ('product_sales', 'Product Sales Report'),
        ('category_sales', 'Category Sales Report'),
        ('waiter_performance', 'Waiter Performance Report'),
        ('cashier_performance', 'Cashier Performance Report'),
        ('branch_comparison', 'Branch Comparison Report'),
        ('hourly_sales', 'Hourly Sales Report'),
        ('daily_sales', 'Daily Sales Report'),
        ('refund_discount', 'Refund & Discount Report'),
        ('payment_method', 'Payment Method Report'),
        ('tax_report', 'Tax Report'),
        ('management_summary', 'Management Summary Report'),
    ], string='Report Type', required=True, default='management_summary')
    group_by = fields.Selection([
        ('day', 'Day'),
        ('week', 'Week'),
        ('month', 'Month'),
        ('year', 'Year'),
        ('product', 'Product'),
        ('product_category', 'Product Category'),
        ('cashier', 'Cashier'),
        ('waiter', 'Waiter'),
        ('pos_branch', 'POS Branch'),
        ('payment_method', 'Payment Method'),
        ('hour_of_day', 'Hour of Day'),
        ('day_of_week', 'Day of Week'),
        ('pos_session', 'POS Session'),
    ], string='Group By', default='day')
    report_basis = fields.Selection([
        ('sales_incl_tax', 'Sales Including Tax'),
        ('sales_excl_tax', 'Sales Excluding Tax'),
        ('qty_sold', 'Quantity Sold'),
        ('net_sales', 'Net Sales After Refunds'),
    ], string='Report Basis', default='sales_incl_tax')
    include_raw_orders = fields.Boolean(string='Include Raw Orders Sheet', default=False)
    include_summary = fields.Boolean(string='Include Summary', default=True)
    export_format = fields.Selection([
        ('pdf', 'PDF'),
        ('excel', 'Excel'),
    ], string='Export Format', required=True, default='pdf')

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_generate_pdf(self):
        self.ensure_one()
        self.export_format = 'pdf'
        return self._generate_pdf_report()

    def action_generate_excel(self):
        self.ensure_one()
        self.export_format = 'excel'
        return self._generate_excel_report()

    # -------------------------------------------------------------------------
    # BUILD FILTERS
    # -------------------------------------------------------------------------

    def _build_filters(self):
        return {
            'period': 'custom',
            'date_start': self.date_start.strftime('%Y-%m-%d'),
            'date_end': self.date_end.strftime('%Y-%m-%d'),
            'pos_config_ids': self.pos_config_ids.ids,
            'cashier_ids': self.cashier_ids.ids,
            'waiter_ids': self.waiter_ids.ids,
            'product_category_ids': self.product_category_ids.ids,
            'product_ids': self.product_ids.ids,
            'payment_method_ids': self.payment_method_ids.ids,
            'report_basis': self.report_basis,
        }

    # -------------------------------------------------------------------------
    # PDF GENERATION
    # -------------------------------------------------------------------------

    def _generate_pdf_report(self):
        filters = self._build_filters()
        data = self.env['pos.analytics.service'].get_dashboard_data(filters)
        data['wizard'] = {
            'date_start': self.date_start.strftime('%Y-%m-%d'),
            'date_end': self.date_end.strftime('%Y-%m-%d'),
            'report_type': dict(self._fields['report_type'].selection).get(self.report_type, ''),
            'report_type_key': self.report_type,
            'group_by': self.group_by,
            'report_basis': self.report_basis,
            'branches': ', '.join(self.pos_config_ids.mapped('name')) or 'All Branches',
            'generated_by': self.env.user.name,
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        # Also get closing data if daily_closing
        if self.report_type == 'daily_closing':
            data['closing_data'] = self.env['pos.analytics.service'].get_daily_closing_data(filters)

        return self.env.ref('pos_advanced_analytics.action_pos_analytics_pdf_report').report_action(
            self, data=data
        )

    # -------------------------------------------------------------------------
    # EXCEL GENERATION
    # -------------------------------------------------------------------------

    def _generate_excel_report(self):
        filters = self._build_filters()
        data = self.env['pos.analytics.service'].get_dashboard_data(filters)
        closing_data = []
        if self.report_type == 'daily_closing':
            closing_data = self.env['pos.analytics.service'].get_daily_closing_data(filters)

        ExcelReport = self.env['pos.analytics.excel.report']
        file_content = ExcelReport.generate(
            data=data,
            closing_data=closing_data,
            wizard=self,
        )
        filename = 'POS_Analytics_%s_%s.xlsx' % (
            self.date_start.strftime('%Y%m%d'),
            self.date_end.strftime('%Y%m%d'),
        )
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': base64.b64encode(file_content),
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%d?download=true' % attachment.id,
            'target': 'self',
        }
