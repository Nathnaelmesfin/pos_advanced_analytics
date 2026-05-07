# -*- coding: utf-8 -*-
import io
import logging
from datetime import datetime

from odoo import api, models, _

_logger = logging.getLogger(__name__)

try:
    import xlsxwriter
except ImportError:
    xlsxwriter = None


class PosAnalyticsExcelReport(models.AbstractModel):
    _name = 'pos.analytics.excel.report'
    _description = 'POS Analytics Excel Report Generator'

    @api.model
    def generate(self, data, closing_data=None, wizard=None):
        """Generate a multi-sheet Excel workbook and return raw bytes."""
        if not xlsxwriter:
            raise ImportError(
                'xlsxwriter is required for Excel export. '
                'Install it with: pip install xlsxwriter'
            )
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        # ── Formats ──────────────────────────────────────────────────────────
        fmt = self._build_formats(workbook, wizard)

        # ── Meta info ─────────────────────────────────────────────────────────
        company = self.env.company
        date_start = wizard.date_start.strftime('%Y-%m-%d') if wizard else ''
        date_end = wizard.date_end.strftime('%Y-%m-%d') if wizard else ''
        branches = ', '.join(wizard.pos_config_ids.mapped('name')) if wizard and wizard.pos_config_ids else 'All Branches'
        generated_by = self.env.user.name
        generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        currency = company.currency_id.name or ''

        meta = {
            'company': company.name,
            'date_range': f'{date_start} to {date_end}',
            'branches': branches,
            'generated_by': generated_by,
            'generated_at': generated_at,
            'currency': currency,
        }

        kpis = data.get('kpis', {})

        # ── Sheets ────────────────────────────────────────────────────────────
        self._write_summary_sheet(workbook, fmt, meta, kpis, data)
        self._write_daily_sales_sheet(workbook, fmt, meta, data.get('sales_trend', []))
        self._write_product_sales_sheet(workbook, fmt, meta, data.get('top_products', []))
        self._write_category_sales_sheet(workbook, fmt, meta, data.get('top_categories', []))
        self._write_waiter_sheet(workbook, fmt, meta, data.get('waiter_performance', []))
        self._write_cashier_sheet(workbook, fmt, meta, data.get('cashier_performance', []))
        self._write_peak_hours_sheet(workbook, fmt, meta, data.get('peak_hours', []))
        self._write_peak_days_sheet(workbook, fmt, meta, data.get('peak_days', []))
        self._write_payment_methods_sheet(workbook, fmt, meta, data.get('payment_methods', []))
        self._write_refund_discount_sheet(workbook, fmt, meta, data.get('refund_discount_summary', {}))
        self._write_branch_comparison_sheet(workbook, fmt, meta, data.get('branch_comparison', []))

        # Tax summary (derived from kpis)
        self._write_tax_summary_sheet(workbook, fmt, meta, kpis)

        # Raw orders (optional)
        if wizard and wizard.include_raw_orders and closing_data:
            self._write_raw_orders_sheet(workbook, fmt, meta, closing_data)

        workbook.close()
        output.seek(0)
        return output.read()

    # ── Format builder ────────────────────────────────────────────────────────

    def _build_formats(self, wb, wizard):
        currency = self.env.company.currency_id.name or 'ETB'
        num_fmt = f'#,##0.00 "{currency}"'
        return {
            'title': wb.add_format({'bold': True, 'font_size': 14, 'font_color': '#1F3864', 'bg_color': '#FFFFFF'}),
            'header': wb.add_format({'bold': True, 'bg_color': '#1F3864', 'font_color': '#FFFFFF',
                                     'border': 1, 'align': 'center', 'valign': 'vcenter', 'text_wrap': True}),
            'meta_label': wb.add_format({'bold': True, 'font_color': '#333333'}),
            'meta_value': wb.add_format({'font_color': '#555555'}),
            'money': wb.add_format({'num_format': num_fmt, 'border': 1}),
            'money_total': wb.add_format({'num_format': num_fmt, 'bold': True, 'bg_color': '#DCE6F1', 'border': 1}),
            'number': wb.add_format({'num_format': '#,##0.00', 'border': 1}),
            'integer': wb.add_format({'num_format': '#,##0', 'border': 1}),
            'integer_total': wb.add_format({'num_format': '#,##0', 'bold': True, 'bg_color': '#DCE6F1', 'border': 1}),
            'text': wb.add_format({'border': 1}),
            'text_bold': wb.add_format({'bold': True, 'border': 1}),
            'text_total': wb.add_format({'bold': True, 'bg_color': '#DCE6F1', 'border': 1}),
            'percent': wb.add_format({'num_format': '0.00%', 'border': 1}),
            'date': wb.add_format({'num_format': 'yyyy-mm-dd', 'border': 1}),
            'kpi_label': wb.add_format({'bold': True, 'bg_color': '#EBF3FB', 'border': 1}),
            'kpi_value': wb.add_format({'bold': True, 'bg_color': '#EBF3FB', 'border': 1,
                                        'num_format': '#,##0.00', 'align': 'right'}),
        }

    # ── Meta writer ───────────────────────────────────────────────────────────

    def _write_meta(self, ws, fmt, meta, title, start_row=0):
        ws.write(start_row, 0, title, fmt['title'])
        ws.write(start_row + 1, 0, 'Company:', fmt['meta_label'])
        ws.write(start_row + 1, 1, meta['company'], fmt['meta_value'])
        ws.write(start_row + 2, 0, 'Date Range:', fmt['meta_label'])
        ws.write(start_row + 2, 1, meta['date_range'], fmt['meta_value'])
        ws.write(start_row + 3, 0, 'Branch(es):', fmt['meta_label'])
        ws.write(start_row + 3, 1, meta['branches'], fmt['meta_value'])
        ws.write(start_row + 4, 0, 'Generated By:', fmt['meta_label'])
        ws.write(start_row + 4, 1, meta['generated_by'], fmt['meta_value'])
        ws.write(start_row + 5, 0, 'Generated At:', fmt['meta_label'])
        ws.write(start_row + 5, 1, meta['generated_at'], fmt['meta_value'])
        return start_row + 7

    # ── Summary Sheet ─────────────────────────────────────────────────────────

    def _write_summary_sheet(self, wb, fmt, meta, kpis, data):
        ws = wb.add_worksheet('Summary')
        ws.set_column(0, 0, 32)
        ws.set_column(1, 1, 22)
        row = self._write_meta(ws, fmt, meta, 'POS Analytics – Executive Summary')

        kpi_rows = [
            ('Total Sales', kpis.get('total_sales', 0)),
            ('Net Sales (After Refunds)', kpis.get('net_sales', 0)),
            ('Total Orders', kpis.get('total_orders', 0)),
            ('Average Order Value', kpis.get('avg_order_value', 0)),
            ('Total Quantity Sold', kpis.get('total_qty_sold', 0)),
            ('Total Tax', kpis.get('total_tax', 0)),
            ('Total Discounts', kpis.get('total_discounts', 0)),
            ('Total Refunds', kpis.get('total_refunds', 0)),
            ('Refund Orders', kpis.get('refund_orders', 0)),
            ('Cash Sales', kpis.get('cash_sales', 0)),
            ('Bank / Card Sales', kpis.get('bank_card_sales', 0)),
            ('Mobile Money Sales', kpis.get('mobile_money_sales', 0)),
            ('Other Payment Sales', kpis.get('other_payment_sales', 0)),
            ('Best Selling Product', kpis.get('best_selling_product', '—')),
            ('Best Selling Category', kpis.get('best_selling_category', '—')),
            ('Top Waiter', kpis.get('top_waiter', '—')),
            ('Top Cashier', kpis.get('top_cashier', '—')),
            ('Peak Hour', f"{kpis.get('peak_hour', '—')}:00" if kpis.get('peak_hour') is not None else '—'),
            ('Peak Day', kpis.get('peak_day', '—')),
        ]

        ws.write(row, 0, 'KPI', fmt['header'])
        ws.write(row, 1, 'Value', fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        for label, value in kpi_rows:
            ws.write(row, 0, label, fmt['kpi_label'])
            if isinstance(value, (int, float)):
                ws.write(row, 1, value, fmt['kpi_value'])
            else:
                ws.write(row, 1, str(value) if value else '—', fmt['meta_value'])
            row += 1

    # ── Daily / Trend Sales Sheet ──────────────────────────────────────────────

    def _write_daily_sales_sheet(self, wb, fmt, meta, trend_data):
        ws = wb.add_worksheet('Sales Trend')
        ws.set_column(0, 0, 20)
        ws.set_column(1, 4, 18)
        row = self._write_meta(ws, fmt, meta, 'Sales Trend')

        headers = ['Period', 'Total Sales', 'Total Refunds', 'Net Sales', 'Order Count']
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        total_sales = total_refunds = net_sales = order_count = 0
        for d in trend_data:
            ws.write(row, 0, d.get('label', ''), fmt['text'])
            ws.write(row, 1, d.get('total_sales', 0), fmt['money'])
            ws.write(row, 2, d.get('total_refunds', 0), fmt['money'])
            ws.write(row, 3, d.get('net_sales', 0), fmt['money'])
            ws.write(row, 4, d.get('order_count', 0), fmt['integer'])
            total_sales += d.get('total_sales', 0)
            total_refunds += d.get('total_refunds', 0)
            net_sales += d.get('net_sales', 0)
            order_count += d.get('order_count', 0)
            row += 1

        ws.write(row, 0, 'TOTAL', fmt['text_total'])
        ws.write(row, 1, total_sales, fmt['money_total'])
        ws.write(row, 2, total_refunds, fmt['money_total'])
        ws.write(row, 3, net_sales, fmt['money_total'])
        ws.write(row, 4, order_count, fmt['integer_total'])

    # ── Product Sales Sheet ───────────────────────────────────────────────────

    def _write_product_sales_sheet(self, wb, fmt, meta, products):
        ws = wb.add_worksheet('Product Sales')
        ws.set_column(0, 0, 30)
        ws.set_column(1, 1, 20)
        ws.set_column(2, 8, 16)
        row = self._write_meta(ws, fmt, meta, 'Product Sales')

        headers = ['Product', 'Category', 'Qty Sold', 'Gross Sales', 'Discount', 'Net Sales', 'Refund Qty', 'Refund Amount']
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        totals = [0] * 6
        for p in products:
            ws.write(row, 0, p.get('product_name', ''), fmt['text'])
            ws.write(row, 1, p.get('categ_name', ''), fmt['text'])
            ws.write(row, 2, p.get('qty_sold', 0), fmt['number'])
            ws.write(row, 3, p.get('gross_sales', 0), fmt['money'])
            ws.write(row, 4, p.get('discount_amount', 0), fmt['money'])
            ws.write(row, 5, p.get('net_sales', 0), fmt['money'])
            ws.write(row, 6, p.get('refund_qty', 0), fmt['number'])
            ws.write(row, 7, p.get('refund_amount', 0), fmt['money'])
            totals[0] += p.get('qty_sold', 0)
            totals[1] += p.get('gross_sales', 0)
            totals[2] += p.get('discount_amount', 0)
            totals[3] += p.get('net_sales', 0)
            totals[4] += p.get('refund_qty', 0)
            totals[5] += p.get('refund_amount', 0)
            row += 1

        ws.write(row, 0, 'TOTAL', fmt['text_total'])
        ws.write(row, 1, '', fmt['text_total'])
        ws.write(row, 2, totals[0], fmt['integer_total'])
        ws.write(row, 3, totals[1], fmt['money_total'])
        ws.write(row, 4, totals[2], fmt['money_total'])
        ws.write(row, 5, totals[3], fmt['money_total'])
        ws.write(row, 6, totals[4], fmt['integer_total'])
        ws.write(row, 7, totals[5], fmt['money_total'])

    # ── Category Sales Sheet ──────────────────────────────────────────────────

    def _write_category_sales_sheet(self, wb, fmt, meta, categories):
        ws = wb.add_worksheet('Category Sales')
        ws.set_column(0, 0, 28)
        ws.set_column(1, 7, 16)
        row = self._write_meta(ws, fmt, meta, 'Category Sales')

        headers = ['Category', 'Qty Sold', 'Gross Sales', 'Discount', 'Net Sales', 'Refund Qty', 'Refund Amount']
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        totals = [0] * 5
        for c in categories:
            ws.write(row, 0, c.get('categ_name', ''), fmt['text'])
            ws.write(row, 1, c.get('qty_sold', 0), fmt['number'])
            ws.write(row, 2, c.get('gross_sales', 0), fmt['money'])
            ws.write(row, 3, c.get('discount_amount', 0), fmt['money'])
            ws.write(row, 4, c.get('net_sales', 0), fmt['money'])
            ws.write(row, 5, c.get('refund_qty', 0), fmt['number'])
            ws.write(row, 6, c.get('refund_amount', 0), fmt['money'])
            totals[0] += c.get('qty_sold', 0)
            totals[1] += c.get('gross_sales', 0)
            totals[2] += c.get('discount_amount', 0)
            totals[3] += c.get('net_sales', 0)
            totals[4] += c.get('refund_amount', 0)
            row += 1

        ws.write(row, 0, 'TOTAL', fmt['text_total'])
        ws.write(row, 1, totals[0], fmt['integer_total'])
        ws.write(row, 2, totals[1], fmt['money_total'])
        ws.write(row, 3, totals[2], fmt['money_total'])
        ws.write(row, 4, totals[3], fmt['money_total'])
        ws.write(row, 5, '', fmt['text_total'])
        ws.write(row, 6, totals[4], fmt['money_total'])

    # ── Waiter Sheet ──────────────────────────────────────────────────────────

    def _write_waiter_sheet(self, wb, fmt, meta, waiters):
        ws = wb.add_worksheet('Waiter Sales')
        ws.set_column(0, 0, 28)
        ws.set_column(1, 7, 16)
        row = self._write_meta(ws, fmt, meta, 'Waiter Performance')

        headers = ['Waiter', 'Total Sales', 'Total Orders', 'Avg Order Value', 'Qty Sold', 'Refunds', 'Discounts']
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        for w in waiters:
            ws.write(row, 0, w.get('waiter_name', ''), fmt['text'])
            ws.write(row, 1, w.get('total_sales', 0), fmt['money'])
            ws.write(row, 2, w.get('total_orders', 0), fmt['integer'])
            ws.write(row, 3, w.get('avg_order_value', 0), fmt['money'])
            ws.write(row, 4, w.get('qty_sold', 0), fmt['number'])
            ws.write(row, 5, w.get('total_refunds', 0), fmt['money'])
            ws.write(row, 6, w.get('total_discounts', 0), fmt['money'])
            row += 1

    # ── Cashier Sheet ─────────────────────────────────────────────────────────

    def _write_cashier_sheet(self, wb, fmt, meta, cashiers):
        ws = wb.add_worksheet('Cashier Sales')
        ws.set_column(0, 0, 28)
        ws.set_column(1, 6, 18)
        row = self._write_meta(ws, fmt, meta, 'Cashier Performance')

        headers = ['Cashier', 'Total Collected', 'Total Orders', 'Avg Ticket', 'Refunds', 'Discounts', 'Payment Methods']
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        for c in cashiers:
            methods_str = ', '.join(
                f"{m['method']}: {m['amount']}" for m in c.get('payment_methods', [])
            )
            ws.write(row, 0, c.get('cashier_name', ''), fmt['text'])
            ws.write(row, 1, c.get('total_collected', 0), fmt['money'])
            ws.write(row, 2, c.get('total_orders', 0), fmt['integer'])
            ws.write(row, 3, c.get('avg_ticket', 0), fmt['money'])
            ws.write(row, 4, c.get('total_refunds', 0), fmt['money'])
            ws.write(row, 5, c.get('total_discounts', 0), fmt['money'])
            ws.write(row, 6, methods_str, fmt['text'])
            row += 1

    # ── Peak Hours Sheet ──────────────────────────────────────────────────────

    def _write_peak_hours_sheet(self, wb, fmt, meta, hours):
        ws = wb.add_worksheet('Peak Hours')
        ws.set_column(0, 0, 14)
        ws.set_column(1, 3, 18)
        row = self._write_meta(ws, fmt, meta, 'Peak Hours Analysis')

        headers = ['Hour', 'Label', 'Order Count', 'Total Sales']
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        for h in hours:
            ws.write(row, 0, h.get('hour', 0), fmt['integer'])
            ws.write(row, 1, h.get('label', ''), fmt['text'])
            ws.write(row, 2, h.get('order_count', 0), fmt['integer'])
            ws.write(row, 3, h.get('total_sales', 0), fmt['money'])
            row += 1

    # ── Peak Days Sheet ───────────────────────────────────────────────────────

    def _write_peak_days_sheet(self, wb, fmt, meta, days):
        ws = wb.add_worksheet('Peak Days')
        ws.set_column(0, 0, 14)
        ws.set_column(1, 3, 18)
        row = self._write_meta(ws, fmt, meta, 'Peak Days Analysis')

        headers = ['Day #', 'Day Name', 'Order Count', 'Total Sales']
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        for d in days:
            ws.write(row, 0, d.get('day_num', 0), fmt['integer'])
            ws.write(row, 1, d.get('day_name', ''), fmt['text'])
            ws.write(row, 2, d.get('order_count', 0), fmt['integer'])
            ws.write(row, 3, d.get('total_sales', 0), fmt['money'])
            row += 1

    # ── Payment Methods Sheet ─────────────────────────────────────────────────

    def _write_payment_methods_sheet(self, wb, fmt, meta, methods):
        ws = wb.add_worksheet('Payment Methods')
        ws.set_column(0, 0, 28)
        ws.set_column(1, 2, 18)
        row = self._write_meta(ws, fmt, meta, 'Payment Method Breakdown')

        headers = ['Payment Method', 'Total Amount', 'Order Count']
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        total_amt = 0
        total_cnt = 0
        for m in methods:
            ws.write(row, 0, m.get('method_name', ''), fmt['text'])
            ws.write(row, 1, m.get('total_amount', 0), fmt['money'])
            ws.write(row, 2, m.get('order_count', 0), fmt['integer'])
            total_amt += m.get('total_amount', 0)
            total_cnt += m.get('order_count', 0)
            row += 1

        ws.write(row, 0, 'TOTAL', fmt['text_total'])
        ws.write(row, 1, total_amt, fmt['money_total'])
        ws.write(row, 2, total_cnt, fmt['integer_total'])

    # ── Refunds & Discounts Sheet ─────────────────────────────────────────────

    def _write_refund_discount_sheet(self, wb, fmt, meta, summary):
        ws = wb.add_worksheet('Refunds & Discounts')
        ws.set_column(0, 0, 32)
        ws.set_column(1, 1, 20)
        row = self._write_meta(ws, fmt, meta, 'Refunds & Discounts Summary')

        ws.write(row, 0, 'Metric', fmt['header'])
        ws.write(row, 1, 'Value', fmt['header'])
        row += 1

        ws.write(row, 0, 'Total Refund Amount', fmt['kpi_label'])
        ws.write(row, 1, summary.get('total_refund_amount', 0), fmt['money'])
        row += 1
        ws.write(row, 0, 'Refund Order Count', fmt['kpi_label'])
        ws.write(row, 1, summary.get('refund_order_count', 0), fmt['integer'])
        row += 1
        ws.write(row, 0, 'Total Discount Amount', fmt['kpi_label'])
        ws.write(row, 1, summary.get('total_discount_amount', 0), fmt['money'])
        row += 1
        ws.write(row, 0, 'Discounted Order Count', fmt['kpi_label'])
        ws.write(row, 1, summary.get('discounted_order_count', 0), fmt['integer'])
        row += 2

        # By cashier
        disc_cashier = summary.get('discount_by_cashier', [])
        if disc_cashier:
            ws.write(row, 0, 'Discount by Cashier', fmt['header'])
            ws.write(row, 1, 'Amount', fmt['header'])
            row += 1
            for r in disc_cashier:
                ws.write(row, 0, r.get('cashier_name', ''), fmt['text'])
                ws.write(row, 1, r.get('discount_amount', 0), fmt['money'])
                row += 1
            row += 1

        # By product
        disc_product = summary.get('discount_by_product', [])
        if disc_product:
            ws.write(row, 0, 'Product', fmt['header'])
            ws.write(row, 1, 'Discount Amount', fmt['header'])
            ws.write(row, 2, 'Category', fmt['header']) if row < 10000 else None
            ws.set_column(2, 2, 20)
            row += 1
            for r in disc_product:
                ws.write(row, 0, r.get('product_name', ''), fmt['text'])
                ws.write(row, 1, r.get('discount_amount', 0), fmt['money'])
                ws.write(row, 2, r.get('categ_name', ''), fmt['text'])
                row += 1

    # ── Tax Summary Sheet ─────────────────────────────────────────────────────

    def _write_tax_summary_sheet(self, wb, fmt, meta, kpis):
        ws = wb.add_worksheet('Tax Summary')
        ws.set_column(0, 0, 32)
        ws.set_column(1, 1, 20)
        row = self._write_meta(ws, fmt, meta, 'Tax Summary')

        ws.write(row, 0, 'Metric', fmt['header'])
        ws.write(row, 1, 'Value', fmt['header'])
        row += 1

        ws.write(row, 0, 'Total Sales (incl. tax)', fmt['kpi_label'])
        ws.write(row, 1, kpis.get('total_sales', 0), fmt['money'])
        row += 1
        ws.write(row, 0, 'Total Tax Amount', fmt['kpi_label'])
        ws.write(row, 1, kpis.get('total_tax', 0), fmt['money'])
        row += 1
        net_excl = round(kpis.get('total_sales', 0) - kpis.get('total_tax', 0), 2)
        ws.write(row, 0, 'Net Sales (excl. tax)', fmt['kpi_label'])
        ws.write(row, 1, net_excl, fmt['money'])
        row += 1

    # ── Branch Comparison Sheet ───────────────────────────────────────────────

    def _write_branch_comparison_sheet(self, wb, fmt, meta, branches):
        ws = wb.add_worksheet('Branch Comparison')
        ws.set_column(0, 0, 28)
        ws.set_column(1, 8, 18)
        row = self._write_meta(ws, fmt, meta, 'Branch Comparison')

        headers = ['Branch', 'Total Sales', 'Net Sales', 'Refunds', 'Total Orders', 'Avg Order Value', 'Top Product', 'Peak Hour']
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        for b in branches:
            ws.write(row, 0, b.get('branch_name', ''), fmt['text'])
            ws.write(row, 1, b.get('total_sales', 0), fmt['money'])
            ws.write(row, 2, b.get('net_sales', 0), fmt['money'])
            ws.write(row, 3, b.get('total_refunds', 0), fmt['money'])
            ws.write(row, 4, b.get('total_orders', 0), fmt['integer'])
            ws.write(row, 5, b.get('avg_order_value', 0), fmt['money'])
            ws.write(row, 6, b.get('top_product') or '—', fmt['text'])
            ph = b.get('peak_hour')
            ws.write(row, 7, f"{ph:02d}:00" if ph is not None else '—', fmt['text'])
            row += 1

    # ── Raw Orders Sheet ──────────────────────────────────────────────────────

    def _write_raw_orders_sheet(self, wb, fmt, meta, closing_data):
        ws = wb.add_worksheet('Raw POS Orders')
        ws.set_column(0, 0, 20)
        ws.set_column(1, 1, 18)
        ws.set_column(2, 3, 22)
        ws.set_column(4, 8, 16)
        row = self._write_meta(ws, fmt, meta, 'Raw POS Order Data')

        headers = [
            'Session', 'Branch', 'Opening Time', 'Closing Time',
            'Cashier', 'Total Sales', 'Net Sales', 'Tax',
            'Orders', 'Refunds', 'Discounts', 'Avg Order Value',
        ]
        for c, h in enumerate(headers):
            ws.write(row, c, h, fmt['header'])
        ws.freeze_panes(row + 1, 0)
        row += 1

        for s in closing_data:
            ws.write(row, 0, s.get('session_name', ''), fmt['text'])
            ws.write(row, 1, s.get('branch_name', ''), fmt['text'])
            ws.write(row, 2, s.get('opening_time', '') or '', fmt['text'])
            ws.write(row, 3, s.get('closing_time', '') or '', fmt['text'])
            ws.write(row, 4, s.get('cashier_name', ''), fmt['text'])
            ws.write(row, 5, s.get('total_sales', 0), fmt['money'])
            ws.write(row, 6, s.get('net_sales', 0), fmt['money'])
            ws.write(row, 7, s.get('total_tax', 0), fmt['money'])
            ws.write(row, 8, s.get('total_orders', 0), fmt['integer'])
            ws.write(row, 9, s.get('total_refunds', 0), fmt['money'])
            ws.write(row, 10, s.get('total_discounts', 0), fmt['money'])
            ws.write(row, 11, s.get('avg_order_value', 0), fmt['money'])
            row += 1
