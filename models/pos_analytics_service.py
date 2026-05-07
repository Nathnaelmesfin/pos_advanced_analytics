# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

VALID_STATES = ('paid', 'done', 'invoiced', 'posted')
TZ_ADDIS = 'Africa/Addis_Ababa'


def _to_local(dt_utc, tz_name=TZ_ADDIS):
    """Convert a naive UTC datetime to local timezone aware datetime."""
    if not dt_utc:
        return None
    tz = pytz.timezone(tz_name)
    if dt_utc.tzinfo is None:
        dt_utc = pytz.utc.localize(dt_utc)
    return dt_utc.astimezone(tz)


def _to_utc(dt_local, tz_name=TZ_ADDIS):
    """Convert a local datetime string/object to UTC naive datetime."""
    if not dt_local:
        return None
    tz = pytz.timezone(tz_name)
    if isinstance(dt_local, str):
        dt_local = datetime.strptime(dt_local, '%Y-%m-%d %H:%M:%S')
    if dt_local.tzinfo is None:
        dt_local = tz.localize(dt_local)
    return dt_local.astimezone(pytz.utc).replace(tzinfo=None)


def _date_range_utc(date_start_str, date_end_str, tz_name=TZ_ADDIS):
    """Return (utc_start, utc_end) naive datetimes for full day range in local TZ."""
    tz = pytz.timezone(tz_name)
    if isinstance(date_start_str, str):
        d_start = datetime.strptime(date_start_str, '%Y-%m-%d')
    else:
        d_start = date_start_str
    if isinstance(date_end_str, str):
        d_end = datetime.strptime(date_end_str, '%Y-%m-%d')
    else:
        d_end = date_end_str
    local_start = tz.localize(datetime(d_start.year, d_start.month, d_start.day, 0, 0, 0))
    local_end = tz.localize(datetime(d_end.year, d_end.month, d_end.day, 23, 59, 59))
    utc_start = local_start.astimezone(pytz.utc).replace(tzinfo=None)
    utc_end = local_end.astimezone(pytz.utc).replace(tzinfo=None)
    return utc_start, utc_end


class PosAnalyticsService(models.AbstractModel):
    _name = 'pos.analytics.service'
    _description = 'POS Analytics Service'

    # -------------------------------------------------------------------------
    # PUBLIC API
    # -------------------------------------------------------------------------

    @api.model
    def get_dashboard_data(self, filters=None):
        """Main entry point called from OWL dashboard via RPC."""
        if filters is None:
            filters = {}
        try:
            params = self._prepare_params(filters)
            return {
                'kpis': self._get_kpis(params),
                'sales_trend': self._get_sales_trend(params),
                'top_products': self._get_top_products(params),
                'top_categories': self._get_top_categories(params),
                'waiter_performance': self._get_waiter_performance(params),
                'cashier_performance': self._get_cashier_performance(params),
                'peak_hours': self._get_peak_hours(params),
                'peak_days': self._get_peak_days(params),
                'payment_methods': self._get_payment_methods(params),
                'branch_comparison': self._get_branch_comparison(params),
                'refund_discount_summary': self._get_refund_discount_summary(params),
                'target_summary': self._get_target_summary(params),
            }
        except Exception as e:
            _logger.exception('POS Analytics get_dashboard_data error: %s', e)
            raise UserError(_('Analytics error: %s') % str(e))

    # -------------------------------------------------------------------------
    # PARAMETER PREPARATION
    # -------------------------------------------------------------------------

    @api.model
    def _prepare_params(self, filters):
        """Normalize and validate filters, compute UTC date boundaries."""
        tz = pytz.timezone(TZ_ADDIS)
        now_local = datetime.now(tz)
        today_local = now_local.date()

        period = filters.get('period', 'today')
        date_start_str = filters.get('date_start')
        date_end_str = filters.get('date_end')

        if period == 'today':
            d_start = d_end = today_local
        elif period == 'yesterday':
            d_start = d_end = today_local - timedelta(days=1)
        elif period == 'this_week':
            d_start = today_local - timedelta(days=today_local.weekday())
            d_end = today_local
        elif period == 'last_week':
            start_this = today_local - timedelta(days=today_local.weekday())
            d_start = start_this - timedelta(weeks=1)
            d_end = start_this - timedelta(days=1)
        elif period == 'this_month':
            d_start = today_local.replace(day=1)
            d_end = today_local
        elif period == 'last_month':
            first_this = today_local.replace(day=1)
            d_end = first_this - timedelta(days=1)
            d_start = d_end.replace(day=1)
        elif period == 'this_year':
            d_start = today_local.replace(month=1, day=1)
            d_end = today_local
        elif period == 'custom' and date_start_str and date_end_str:
            d_start = datetime.strptime(date_start_str, '%Y-%m-%d').date()
            d_end = datetime.strptime(date_end_str, '%Y-%m-%d').date()
        else:
            d_start = d_end = today_local

        utc_start, utc_end = _date_range_utc(d_start, d_end)

        params = {
            'utc_start': utc_start,
            'utc_end': utc_end,
            'd_start': d_start,
            'd_end': d_end,
            'period': period,
            'report_basis': filters.get('report_basis', 'sales_incl_tax'),
            'pos_config_ids': [int(x) for x in filters.get('pos_config_ids', []) if x],
            'cashier_ids': [int(x) for x in filters.get('cashier_ids', []) if x],
            'waiter_ids': [int(x) for x in filters.get('waiter_ids', []) if x],
            'product_category_ids': [int(x) for x in filters.get('product_category_ids', []) if x],
            'product_ids': [int(x) for x in filters.get('product_ids', []) if x],
            'payment_method_ids': [int(x) for x in filters.get('payment_method_ids', []) if x],
            'company_id': self.env.company.id,
            'tz': TZ_ADDIS,
        }
        return params

    # -------------------------------------------------------------------------
    # SQL HELPER
    # -------------------------------------------------------------------------

    def _build_order_where(self, params, alias='o'):
        """Build WHERE clauses and args for pos.order queries."""
        clauses = [
            f"{alias}.company_id = %s",
            f"{alias}.state IN %s",
            f"{alias}.date_order >= %s",
            f"{alias}.date_order <= %s",
        ]
        args = [
            params['company_id'],
            VALID_STATES,
            params['utc_start'],
            params['utc_end'],
        ]
        if params.get('pos_config_ids'):
            clauses.append(f"{alias}.config_id = ANY(%s)")
            args.append(params['pos_config_ids'])
        if params.get('cashier_ids'):
            clauses.append(f"{alias}.user_id = ANY(%s)")
            args.append(params['cashier_ids'])
        if params.get('waiter_ids'):
            clauses.append(f"{alias}.employee_id = ANY(%s)")
            args.append(params['waiter_ids'])
        return ' AND '.join(clauses), args

    def _build_line_where(self, params, order_alias='o', line_alias='l'):
        """Build WHERE for queries joining order lines."""
        base_where, args = self._build_order_where(params, alias=order_alias)
        if params.get('product_ids'):
            base_where += f" AND {line_alias}.product_id = ANY(%s)"
            args.append(params['product_ids'])
        if params.get('product_category_ids'):
            base_where += f" AND pt.categ_id = ANY(%s)"
            args.append(params['product_category_ids'])
        return base_where, args

    # -------------------------------------------------------------------------
    # KPI
    # -------------------------------------------------------------------------

    @api.model
    def _get_kpis(self, params):
        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                COALESCE(SUM(o.amount_total), 0)            AS total_sales,
                COALESCE(SUM(o.amount_tax), 0)              AS total_tax,
                COALESCE(SUM(o.amount_paid), 0)             AS total_paid,
                COALESCE(SUM(o.amount_return), 0)           AS total_return,
                COUNT(*)                                     AS total_orders,
                COALESCE(AVG(o.amount_total), 0)            AS avg_order_value
            FROM pos_order o
            WHERE {where}
              AND o.amount_total >= 0
        """, args)
        row = self.env.cr.dictfetchone()

        # Net sales = positive sales - refund amounts
        self.env.cr.execute(f"""
            SELECT COALESCE(SUM(ABS(o.amount_total)), 0) AS total_refunds,
                   COUNT(*) AS refund_orders
            FROM pos_order o
            WHERE {where}
              AND o.amount_total < 0
        """, args)
        refund_row = self.env.cr.dictfetchone()

        # Discounts from order lines
        where_line, args_line = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT COALESCE(SUM(
                CASE WHEN l.discount > 0
                     THEN (l.price_unit * l.qty * l.discount / 100.0)
                     ELSE 0 END
            ), 0) AS total_discounts,
            COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.qty ELSE 0 END), 0) AS total_qty
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            WHERE {where_line}
        """, args_line)
        line_row = self.env.cr.dictfetchone()

        # Payment method breakdown
        pay_where, pay_args = self._build_order_where(params)
        if params.get('payment_method_ids'):
            pay_where += " AND pp.payment_method_id = ANY(%s)"
            pay_args.append(params['payment_method_ids'])

        self.env.cr.execute(f"""
            SELECT
                pm.name                                 AS method_name,
                COALESCE(SUM(pp.amount), 0)             AS total_amount
            FROM pos_payment pp
            JOIN pos_payment_method pm ON pm.id = pp.payment_method_id
            JOIN pos_order o ON o.id = pp.pos_order_id
            WHERE {pay_where}
            GROUP BY pm.name
        """, pay_args)
        payments = self.env.cr.dictfetchall()

        cash_total = bank_total = mobile_total = other_total = 0.0
        for p in payments:
            name_lower = (p['method_name'] or '').lower()
            amt = float(p['total_amount'] or 0)
            if 'cash' in name_lower:
                cash_total += amt
            elif any(k in name_lower for k in ('bank', 'card', 'visa', 'master')):
                bank_total += amt
            elif any(k in name_lower for k in ('mobile', 'mpesa', 'birr', 'telebirr', 'cbe')):
                mobile_total += amt
            else:
                other_total += amt

        # Best selling product
        self.env.cr.execute(f"""
            SELECT pt.name AS product_name, SUM(l.qty) AS qty_sold
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN product_product pp2 ON pp2.id = l.product_id
            JOIN product_template pt ON pt.id = pp2.product_tmpl_id
            WHERE {where_line}
              AND l.qty > 0
            GROUP BY pt.name
            ORDER BY qty_sold DESC
            LIMIT 1
        """, args_line)
        best_product_row = self.env.cr.dictfetchone()

        # Best selling category
        self.env.cr.execute(f"""
            SELECT pc.name AS categ_name, SUM(l.qty) AS qty_sold
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN product_product pp2 ON pp2.id = l.product_id
            JOIN product_template pt ON pt.id = pp2.product_tmpl_id
            JOIN product_category pc ON pc.id = pt.categ_id
            WHERE {where_line}
              AND l.qty > 0
            GROUP BY pc.name
            ORDER BY qty_sold DESC
            LIMIT 1
        """, args_line)
        best_categ_row = self.env.cr.dictfetchone()

        # Top waiter
        self.env.cr.execute(f"""
            SELECT he.name AS waiter_name, SUM(o.amount_total) AS total_sales
            FROM pos_order o
            JOIN hr_employee he ON he.id = o.employee_id
            WHERE {where}
              AND o.amount_total >= 0
              AND o.employee_id IS NOT NULL
            GROUP BY he.name
            ORDER BY total_sales DESC
            LIMIT 1
        """, args)
        top_waiter_row = self.env.cr.dictfetchone()

        # Top cashier
        self.env.cr.execute(f"""
            SELECT ru.name AS cashier_name, SUM(o.amount_total) AS total_sales
            FROM pos_order o
            JOIN res_users ru ON ru.id = o.user_id
            WHERE {where}
              AND o.amount_total >= 0
              AND o.user_id IS NOT NULL
            GROUP BY ru.name
            ORDER BY total_sales DESC
            LIMIT 1
        """, args)
        top_cashier_row = self.env.cr.dictfetchone()

        # Peak sales hour (local time)
        self.env.cr.execute(f"""
            SELECT
                EXTRACT(HOUR FROM (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s))::int AS hour,
                COUNT(*) AS order_count
            FROM pos_order o
            WHERE {where}
              AND o.amount_total >= 0
            GROUP BY hour
            ORDER BY order_count DESC
            LIMIT 1
        """, [TZ_ADDIS] + args)
        peak_hour_row = self.env.cr.dictfetchone()

        # Peak sales day
        self.env.cr.execute(f"""
            SELECT
                TO_CHAR((o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s), 'Day') AS day_name,
                COUNT(*) AS order_count
            FROM pos_order o
            WHERE {where}
              AND o.amount_total >= 0
            GROUP BY day_name, EXTRACT(DOW FROM (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s))
            ORDER BY order_count DESC
            LIMIT 1
        """, [TZ_ADDIS] + args + [TZ_ADDIS])
        peak_day_row = self.env.cr.dictfetchone()

        total_sales = float(row['total_sales'] or 0)
        total_refunds = float(refund_row['total_refunds'] or 0)
        net_sales = total_sales - total_refunds
        total_orders = int(row['total_orders'] or 0)

        return {
            'total_sales': round(total_sales, 2),
            'net_sales': round(net_sales, 2),
            'total_orders': total_orders,
            'avg_order_value': round(float(row['avg_order_value'] or 0), 2),
            'total_qty_sold': round(float(line_row['total_qty'] or 0), 2),
            'total_discounts': round(float(line_row['total_discounts'] or 0), 2),
            'total_refunds': round(total_refunds, 2),
            'refund_orders': int(refund_row['refund_orders'] or 0),
            'total_tax': round(float(row['total_tax'] or 0), 2),
            'cash_sales': round(cash_total, 2),
            'bank_card_sales': round(bank_total, 2),
            'mobile_money_sales': round(mobile_total, 2),
            'other_payment_sales': round(other_total, 2),
            'best_selling_product': best_product_row['product_name'] if best_product_row else None,
            'best_selling_category': best_categ_row['categ_name'] if best_categ_row else None,
            'top_waiter': top_waiter_row['waiter_name'] if top_waiter_row else None,
            'top_cashier': top_cashier_row['cashier_name'] if top_cashier_row else None,
            'peak_hour': peak_hour_row['hour'] if peak_hour_row else None,
            'peak_day': (peak_day_row['day_name'] or '').strip() if peak_day_row else None,
            'currency_symbol': self.env.company.currency_id.symbol or '',
        }

    # -------------------------------------------------------------------------
    # SALES TREND
    # -------------------------------------------------------------------------

    @api.model
    def _get_sales_trend(self, params):
        period = params['period']
        if period in ('today', 'yesterday'):
            group_format = 'HH24'
            label_format = 'HH24:00'
            trunc = 'hour'
        elif period in ('this_week', 'last_week'):
            group_format = 'YYYY-MM-DD'
            label_format = 'DD Mon'
            trunc = 'day'
        elif period in ('this_month', 'last_month'):
            group_format = 'YYYY-MM-DD'
            label_format = 'DD'
            trunc = 'day'
        elif period == 'this_year':
            group_format = 'YYYY-MM'
            label_format = 'Mon YYYY'
            trunc = 'month'
        else:
            # custom - determine grouping by range
            delta = (params['d_end'] - params['d_start']).days
            if delta <= 1:
                group_format = 'HH24'
                label_format = 'HH24:00'
                trunc = 'hour'
            elif delta <= 31:
                group_format = 'YYYY-MM-DD'
                label_format = 'DD Mon'
                trunc = 'day'
            elif delta <= 366:
                group_format = 'YYYY-MM'
                label_format = 'Mon YYYY'
                trunc = 'month'
            else:
                group_format = 'YYYY'
                label_format = 'YYYY'
                trunc = 'year'

        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                TO_CHAR(DATE_TRUNC(%s, o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s), %s) AS period_label,
                DATE_TRUNC(%s, o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s) AS period_start,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS total_sales,
                COALESCE(SUM(CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total) ELSE 0 END), 0) AS total_refunds,
                COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END) AS order_count
            FROM pos_order o
            WHERE {where}
            GROUP BY period_start, period_label
            ORDER BY period_start
        """, [trunc, TZ_ADDIS, label_format, trunc, TZ_ADDIS] + args)
        rows = self.env.cr.dictfetchall()
        return [
            {
                'label': r['period_label'],
                'total_sales': round(float(r['total_sales'] or 0), 2),
                'total_refunds': round(float(r['total_refunds'] or 0), 2),
                'net_sales': round(float(r['total_sales'] or 0) - float(r['total_refunds'] or 0), 2),
                'order_count': int(r['order_count'] or 0),
            }
            for r in rows
        ]

    # -------------------------------------------------------------------------
    # TOP PRODUCTS
    # -------------------------------------------------------------------------

    @api.model
    def _get_top_products(self, params, limit=15):
        where, args = self._build_line_where(params)
        self.env.cr.execute(f"""
            SELECT
                l.product_id,
                pt.name                                         AS product_name,
                pc.name                                         AS categ_name,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.qty ELSE 0 END), 0)                AS qty_sold,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal_incl ELSE 0 END), 0) AS gross_sales,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal ELSE 0 END), 0)      AS net_sales,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS discount_amount,
                COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.qty) ELSE 0 END), 0)           AS refund_qty,
                COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.price_subtotal_incl) ELSE 0 END), 0) AS refund_amount
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN product_product pp ON pp.id = l.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN product_category pc ON pc.id = pt.categ_id
            WHERE {where}
            GROUP BY l.product_id, pt.name, pc.name
            ORDER BY gross_sales DESC
            LIMIT %s
        """, args + [limit])
        rows = self.env.cr.dictfetchall()
        return [
            {
                'product_id': r['product_id'],
                'product_name': r['product_name'],
                'categ_name': r['categ_name'],
                'qty_sold': round(float(r['qty_sold'] or 0), 2),
                'gross_sales': round(float(r['gross_sales'] or 0), 2),
                'net_sales': round(float(r['net_sales'] or 0), 2),
                'discount_amount': round(float(r['discount_amount'] or 0), 2),
                'refund_qty': round(float(r['refund_qty'] or 0), 2),
                'refund_amount': round(float(r['refund_amount'] or 0), 2),
            }
            for r in rows
        ]

    # -------------------------------------------------------------------------
    # TOP CATEGORIES
    # -------------------------------------------------------------------------

    @api.model
    def _get_top_categories(self, params, limit=10):
        where, args = self._build_line_where(params)
        self.env.cr.execute(f"""
            SELECT
                pc.id                                           AS categ_id,
                pc.name                                         AS categ_name,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.qty ELSE 0 END), 0)                AS qty_sold,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal_incl ELSE 0 END), 0) AS gross_sales,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal ELSE 0 END), 0)      AS net_sales,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS discount_amount,
                COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.qty) ELSE 0 END), 0)           AS refund_qty,
                COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.price_subtotal_incl) ELSE 0 END), 0) AS refund_amount
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN product_product pp ON pp.id = l.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN product_category pc ON pc.id = pt.categ_id
            WHERE {where}
            GROUP BY pc.id, pc.name
            ORDER BY gross_sales DESC
            LIMIT %s
        """, args + [limit])
        rows = self.env.cr.dictfetchall()
        return [
            {
                'categ_id': r['categ_id'],
                'categ_name': r['categ_name'],
                'qty_sold': round(float(r['qty_sold'] or 0), 2),
                'gross_sales': round(float(r['gross_sales'] or 0), 2),
                'net_sales': round(float(r['net_sales'] or 0), 2),
                'discount_amount': round(float(r['discount_amount'] or 0), 2),
                'refund_qty': round(float(r['refund_qty'] or 0), 2),
                'refund_amount': round(float(r['refund_amount'] or 0), 2),
            }
            for r in rows
        ]

    # -------------------------------------------------------------------------
    # WAITER PERFORMANCE
    # -------------------------------------------------------------------------

    @api.model
    def _get_waiter_performance(self, params):
        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                he.id                                           AS employee_id,
                he.name                                         AS waiter_name,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS total_sales,
                COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END)                                  AS total_orders,
                COALESCE(AVG(CASE WHEN o.amount_total >= 0 THEN o.amount_total END), 0)          AS avg_order_value,
                COALESCE(SUM(CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total) ELSE 0 END), 0) AS total_refunds
            FROM pos_order o
            JOIN hr_employee he ON he.id = o.employee_id
            WHERE {where}
              AND o.employee_id IS NOT NULL
            GROUP BY he.id, he.name
            ORDER BY total_sales DESC
        """, args)
        waiter_rows = self.env.cr.dictfetchall()

        # Discounts per waiter
        self.env.cr.execute(f"""
            SELECT
                o.employee_id,
                COALESCE(SUM(CASE WHEN l.discount > 0 THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS total_discounts,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.qty ELSE 0 END), 0) AS qty_sold
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            WHERE {where}
              AND o.employee_id IS NOT NULL
            GROUP BY o.employee_id
        """, args)
        discount_map = {r['employee_id']: r for r in self.env.cr.dictfetchall()}

        result = []
        for r in waiter_rows:
            disc = discount_map.get(r['employee_id'], {})
            result.append({
                'employee_id': r['employee_id'],
                'waiter_name': r['waiter_name'],
                'total_sales': round(float(r['total_sales'] or 0), 2),
                'total_orders': int(r['total_orders'] or 0),
                'avg_order_value': round(float(r['avg_order_value'] or 0), 2),
                'qty_sold': round(float(disc.get('qty_sold') or 0), 2),
                'total_refunds': round(float(r['total_refunds'] or 0), 2),
                'total_discounts': round(float(disc.get('total_discounts') or 0), 2),
            })
        return result

    # -------------------------------------------------------------------------
    # CASHIER PERFORMANCE
    # -------------------------------------------------------------------------

    @api.model
    def _get_cashier_performance(self, params):
        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                ru.id                                           AS user_id,
                ru.name                                         AS cashier_name,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS total_collected,
                COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END)                                  AS total_orders,
                COALESCE(AVG(CASE WHEN o.amount_total >= 0 THEN o.amount_total END), 0)          AS avg_ticket,
                COALESCE(SUM(CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total) ELSE 0 END), 0) AS total_refunds
            FROM pos_order o
            JOIN res_users ru ON ru.id = o.user_id
            WHERE {where}
              AND o.user_id IS NOT NULL
            GROUP BY ru.id, ru.name
            ORDER BY total_collected DESC
        """, args)
        cashier_rows = self.env.cr.dictfetchall()

        # Discounts per cashier
        self.env.cr.execute(f"""
            SELECT
                o.user_id,
                COALESCE(SUM(CASE WHEN l.discount > 0 THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS total_discounts
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            WHERE {where}
              AND o.user_id IS NOT NULL
            GROUP BY o.user_id
        """, args)
        discount_map = {r['user_id']: r for r in self.env.cr.dictfetchall()}

        # Payment methods per cashier
        pay_where, pay_args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                o.user_id,
                pm.name AS method_name,
                COALESCE(SUM(pp.amount), 0) AS amount
            FROM pos_payment pp
            JOIN pos_payment_method pm ON pm.id = pp.payment_method_id
            JOIN pos_order o ON o.id = pp.pos_order_id
            WHERE {pay_where}
              AND o.user_id IS NOT NULL
            GROUP BY o.user_id, pm.name
        """, pay_args)
        pay_rows = self.env.cr.dictfetchall()
        pay_map = {}
        for r in pay_rows:
            uid = r['user_id']
            if uid not in pay_map:
                pay_map[uid] = []
            pay_map[uid].append({'method': r['method_name'], 'amount': round(float(r['amount'] or 0), 2)})

        result = []
        for r in cashier_rows:
            disc = discount_map.get(r['user_id'], {})
            result.append({
                'user_id': r['user_id'],
                'cashier_name': r['cashier_name'],
                'total_collected': round(float(r['total_collected'] or 0), 2),
                'total_orders': int(r['total_orders'] or 0),
                'avg_ticket': round(float(r['avg_ticket'] or 0), 2),
                'total_refunds': round(float(r['total_refunds'] or 0), 2),
                'total_discounts': round(float(disc.get('total_discounts') or 0), 2),
                'payment_methods': pay_map.get(r['user_id'], []),
            })
        return result

    # -------------------------------------------------------------------------
    # PEAK HOURS
    # -------------------------------------------------------------------------

    @api.model
    def _get_peak_hours(self, params):
        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                EXTRACT(HOUR FROM (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s))::int AS hour,
                COUNT(*) AS order_count,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS total_sales
            FROM pos_order o
            WHERE {where}
              AND o.amount_total >= 0
            GROUP BY hour
            ORDER BY hour
        """, [TZ_ADDIS] + args)
        rows = self.env.cr.dictfetchall()
        return [
            {
                'hour': r['hour'],
                'label': f"{r['hour']:02d}:00",
                'order_count': int(r['order_count'] or 0),
                'total_sales': round(float(r['total_sales'] or 0), 2),
            }
            for r in rows
        ]

    # -------------------------------------------------------------------------
    # PEAK DAYS
    # -------------------------------------------------------------------------

    @api.model
    def _get_peak_days(self, params):
        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                EXTRACT(DOW FROM (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s))::int AS day_num,
                TO_CHAR((o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s), 'Day') AS day_name,
                COUNT(*) AS order_count,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS total_sales
            FROM pos_order o
            WHERE {where}
              AND o.amount_total >= 0
            GROUP BY day_num, day_name
            ORDER BY day_num
        """, [TZ_ADDIS, TZ_ADDIS] + args)
        rows = self.env.cr.dictfetchall()
        return [
            {
                'day_num': r['day_num'],
                'day_name': (r['day_name'] or '').strip(),
                'order_count': int(r['order_count'] or 0),
                'total_sales': round(float(r['total_sales'] or 0), 2),
            }
            for r in rows
        ]

    # -------------------------------------------------------------------------
    # PAYMENT METHODS
    # -------------------------------------------------------------------------

    @api.model
    def _get_payment_methods(self, params):
        pay_where, pay_args = self._build_order_where(params)
        if params.get('payment_method_ids'):
            pay_where += " AND pp.payment_method_id = ANY(%s)"
            pay_args.append(params['payment_method_ids'])
        self.env.cr.execute(f"""
            SELECT
                pm.id                               AS method_id,
                pm.name                             AS method_name,
                COALESCE(SUM(pp.amount), 0)         AS total_amount,
                COUNT(DISTINCT pp.pos_order_id)     AS order_count
            FROM pos_payment pp
            JOIN pos_payment_method pm ON pm.id = pp.payment_method_id
            JOIN pos_order o ON o.id = pp.pos_order_id
            WHERE {pay_where}
            GROUP BY pm.id, pm.name
            ORDER BY total_amount DESC
        """, pay_args)
        rows = self.env.cr.dictfetchall()
        return [
            {
                'method_id': r['method_id'],
                'method_name': r['method_name'],
                'total_amount': round(float(r['total_amount'] or 0), 2),
                'order_count': int(r['order_count'] or 0),
            }
            for r in rows
        ]

    # -------------------------------------------------------------------------
    # BRANCH COMPARISON
    # -------------------------------------------------------------------------

    @api.model
    def _get_branch_comparison(self, params):
        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                pc.id                                           AS config_id,
                pc.name                                         AS branch_name,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS total_sales,
                COALESCE(SUM(CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total) ELSE 0 END), 0) AS total_refunds,
                COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END) AS total_orders,
                COALESCE(AVG(CASE WHEN o.amount_total >= 0 THEN o.amount_total END), 0)          AS avg_order_value
            FROM pos_order o
            JOIN pos_config pc ON pc.id = o.config_id
            WHERE {where}
            GROUP BY pc.id, pc.name
            ORDER BY total_sales DESC
        """, args)
        branch_rows = self.env.cr.dictfetchall()

        # Top product per branch
        self.env.cr.execute(f"""
            SELECT DISTINCT ON (o.config_id)
                o.config_id,
                pt.name AS top_product
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN product_product pp ON pp.id = l.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            WHERE {where}
              AND l.qty > 0
            GROUP BY o.config_id, pt.name
            ORDER BY o.config_id, SUM(l.qty) DESC
        """, args)
        top_product_map = {r['config_id']: r['top_product'] for r in self.env.cr.dictfetchall()}

        # Peak hour per branch
        self.env.cr.execute(f"""
            SELECT DISTINCT ON (o.config_id)
                o.config_id,
                EXTRACT(HOUR FROM (o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s))::int AS peak_hour
            FROM pos_order o
            WHERE {where}
              AND o.amount_total >= 0
            GROUP BY o.config_id, peak_hour
            ORDER BY o.config_id, COUNT(*) DESC
        """, [TZ_ADDIS] + args)
        peak_hour_map = {r['config_id']: r['peak_hour'] for r in self.env.cr.dictfetchall()}

        result = []
        for r in branch_rows:
            cid = r['config_id']
            total_s = float(r['total_sales'] or 0)
            total_ref = float(r['total_refunds'] or 0)
            result.append({
                'config_id': cid,
                'branch_name': r['branch_name'],
                'total_sales': round(total_s, 2),
                'net_sales': round(total_s - total_ref, 2),
                'total_refunds': round(total_ref, 2),
                'total_orders': int(r['total_orders'] or 0),
                'avg_order_value': round(float(r['avg_order_value'] or 0), 2),
                'top_product': top_product_map.get(cid),
                'peak_hour': peak_hour_map.get(cid),
            })
        return result

    # -------------------------------------------------------------------------
    # REFUND & DISCOUNT SUMMARY
    # -------------------------------------------------------------------------

    @api.model
    def _get_refund_discount_summary(self, params):
        where, args = self._build_order_where(params)

        # Refund summary
        self.env.cr.execute(f"""
            SELECT
                COALESCE(SUM(ABS(o.amount_total)), 0) AS total_refund_amount,
                COUNT(*) AS refund_order_count
            FROM pos_order o
            WHERE {where}
              AND o.amount_total < 0
        """, args)
        ref_row = self.env.cr.dictfetchone()

        # Discount summary from lines
        self.env.cr.execute(f"""
            SELECT
                COALESCE(SUM(CASE WHEN l.discount > 0 THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS total_discount_amount,
                COUNT(DISTINCT CASE WHEN l.discount > 0 THEN l.order_id END) AS discounted_order_count
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            WHERE {where}
        """, args)
        disc_row = self.env.cr.dictfetchone()

        # Discount by cashier
        self.env.cr.execute(f"""
            SELECT
                ru.name AS cashier_name,
                COALESCE(SUM(CASE WHEN l.discount > 0 THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS discount_amount
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN res_users ru ON ru.id = o.user_id
            WHERE {where}
              AND l.discount > 0
            GROUP BY ru.name
            ORDER BY discount_amount DESC
            LIMIT 10
        """, args)
        disc_cashier = self.env.cr.dictfetchall()

        # Discount by product/category
        self.env.cr.execute(f"""
            SELECT
                pt.name AS product_name,
                pc.name AS categ_name,
                COALESCE(SUM(CASE WHEN l.discount > 0 THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS discount_amount
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN product_product pp ON pp.id = l.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN product_category pc ON pc.id = pt.categ_id
            WHERE {where}
              AND l.discount > 0
            GROUP BY pt.name, pc.name
            ORDER BY discount_amount DESC
            LIMIT 10
        """, args)
        disc_product = self.env.cr.dictfetchall()

        return {
            'total_refund_amount': round(float(ref_row['total_refund_amount'] or 0), 2),
            'refund_order_count': int(ref_row['refund_order_count'] or 0),
            'total_discount_amount': round(float(disc_row['total_discount_amount'] or 0), 2),
            'discounted_order_count': int(disc_row['discounted_order_count'] or 0),
            'discount_by_cashier': [
                {
                    'cashier_name': r['cashier_name'],
                    'discount_amount': round(float(r['discount_amount'] or 0), 2),
                }
                for r in disc_cashier
            ],
            'discount_by_product': [
                {
                    'product_name': r['product_name'],
                    'categ_name': r['categ_name'],
                    'discount_amount': round(float(r['discount_amount'] or 0), 2),
                }
                for r in disc_product
            ],
        }

    # -------------------------------------------------------------------------
    # TARGET SUMMARY
    # -------------------------------------------------------------------------

    @api.model
    def _get_target_summary(self, params):
        """Return active targets and achievement vs actual sales."""
        Target = self.env['pos.analytics.target']
        company_id = params['company_id']
        d_start = params['d_start']
        d_end = params['d_end']

        domain = [
            ('company_id', '=', company_id),
            ('active', '=', True),
            ('date_start', '<=', d_end),
            ('date_end', '>=', d_start),
        ]
        if params.get('pos_config_ids'):
            domain.append(('pos_config_id', 'in', params['pos_config_ids']))

        targets = Target.search(domain)
        if not targets:
            return []

        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                o.config_id,
                o.user_id,
                o.employee_id,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS actual_sales,
                COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END) AS actual_orders
            FROM pos_order o
            WHERE {where}
            GROUP BY o.config_id, o.user_id, o.employee_id
        """, args)
        actuals = self.env.cr.dictfetchall()

        # Build lookup: (config_id, user_id, employee_id) -> actual
        def make_key(config_id, user_id, employee_id):
            return (config_id, user_id, employee_id)

        actual_map = {}
        for a in actuals:
            k = make_key(a['config_id'], a['user_id'], a['employee_id'])
            actual_map[k] = a

        result = []
        for t in targets:
            config_id = t.pos_config_id.id if t.pos_config_id else None
            user_id = t.cashier_id.id if t.cashier_id else None
            employee_id = t.waiter_id.id if t.waiter_id else None

            # Sum up matching actuals
            actual_sales = 0.0
            actual_orders = 0
            for a in actuals:
                match = True
                if config_id and a['config_id'] != config_id:
                    match = False
                if user_id and a['user_id'] != user_id:
                    match = False
                if employee_id and a['employee_id'] != employee_id:
                    match = False
                if match:
                    actual_sales += float(a['actual_sales'] or 0)
                    actual_orders += int(a['actual_orders'] or 0)

            target_amount = float(t.target_amount or 0)
            achievement_pct = (actual_sales / target_amount * 100) if target_amount else 0
            remaining = max(0, target_amount - actual_sales)

            # Required average daily sales to meet target
            days_remaining = (t.date_end - d_end).days if d_end < t.date_end else 0
            req_daily = (remaining / days_remaining) if days_remaining > 0 else 0

            result.append({
                'target_id': t.id,
                'name': t.name,
                'branch': t.pos_config_id.name if t.pos_config_id else 'All Branches',
                'cashier': t.cashier_id.name if t.cashier_id else None,
                'waiter': t.waiter_id.name if t.waiter_id else None,
                'target_amount': round(target_amount, 2),
                'target_orders': t.target_orders or 0,
                'target_avg_order_value': float(t.target_avg_order_value or 0),
                'actual_sales': round(actual_sales, 2),
                'actual_orders': actual_orders,
                'actual_avg_order_value': round(actual_sales / actual_orders, 2) if actual_orders else 0,
                'achievement_pct': round(achievement_pct, 1),
                'remaining': round(remaining, 2),
                'req_daily_sales': round(req_daily, 2),
            })
        return result

    # -------------------------------------------------------------------------
    # FILTER OPTIONS (for frontend dropdowns)
    # -------------------------------------------------------------------------

    @api.model
    def get_filter_options(self):
        """Return available filter options for the dashboard frontend."""
        company_id = self.env.company.id

        pos_configs = self.env['pos.config'].search([('company_id', '=', company_id)])
        cashiers = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('point_of_sale.group_pos_user').ids),
        ])
        waiters = self.env['hr.employee'].search([('company_id', '=', company_id)])
        categories = self.env['product.category'].search([])
        payment_methods = self.env['pos.payment.method'].search([('company_id', '=', company_id)])

        return {
            'pos_configs': [{'id': c.id, 'name': c.name} for c in pos_configs],
            'cashiers': [{'id': u.id, 'name': u.name} for u in cashiers],
            'waiters': [{'id': e.id, 'name': e.name} for e in waiters],
            'categories': [{'id': c.id, 'name': c.name} for c in categories],
            'payment_methods': [{'id': p.id, 'name': p.name} for p in payment_methods],
            'currency_symbol': self.env.company.currency_id.symbol or '',
            'currency_name': self.env.company.currency_id.name or '',
            'timezone': TZ_ADDIS,
        }

    # -------------------------------------------------------------------------
    # DAILY CLOSING DATA
    # -------------------------------------------------------------------------

    @api.model
    def get_daily_closing_data(self, filters=None):
        """Data for the daily closing / Z-report comparison."""
        if filters is None:
            filters = {}
        params = self._prepare_params(filters)
        where, args = self._build_order_where(params)

        self.env.cr.execute(f"""
            SELECT
                ps.id                                   AS session_id,
                ps.name                                 AS session_name,
                pc.name                                 AS branch_name,
                ru.name                                 AS cashier_name,
                ps.start_at                             AS opening_time,
                ps.stop_at                              AS closing_time,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS total_sales,
                COALESCE(SUM(o.amount_tax), 0)          AS total_tax,
                COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END) AS total_orders,
                COALESCE(SUM(CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total) ELSE 0 END), 0) AS total_refunds,
                COALESCE(AVG(CASE WHEN o.amount_total >= 0 THEN o.amount_total END), 0) AS avg_order_value
            FROM pos_order o
            JOIN pos_session ps ON ps.id = o.session_id
            JOIN pos_config pc ON pc.id = o.config_id
            JOIN res_users ru ON ru.id = ps.user_id
            WHERE {where}
            GROUP BY ps.id, ps.name, pc.name, ru.name, ps.start_at, ps.stop_at
            ORDER BY ps.start_at
        """, args)
        sessions = self.env.cr.dictfetchall()

        # Payment breakdown per session
        pay_where, pay_args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                o.session_id,
                pm.name AS method_name,
                COALESCE(SUM(pp.amount), 0) AS amount
            FROM pos_payment pp
            JOIN pos_payment_method pm ON pm.id = pp.payment_method_id
            JOIN pos_order o ON o.id = pp.pos_order_id
            WHERE {pay_where}
            GROUP BY o.session_id, pm.name
        """, pay_args)
        pay_rows = self.env.cr.dictfetchall()
        pay_map = {}
        for r in pay_rows:
            sid = r['session_id']
            if sid not in pay_map:
                pay_map[sid] = []
            pay_map[sid].append({'method': r['method_name'], 'amount': round(float(r['amount'] or 0), 2)})

        # Discount per session
        self.env.cr.execute(f"""
            SELECT
                o.session_id,
                COALESCE(SUM(CASE WHEN l.discount > 0 THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS total_discounts
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            WHERE {where}
            GROUP BY o.session_id
        """, args)
        disc_map = {r['session_id']: float(r['total_discounts'] or 0) for r in self.env.cr.dictfetchall()}

        result = []
        for s in sessions:
            sid = s['session_id']
            total_s = float(s['total_sales'] or 0)
            total_ref = float(s['total_refunds'] or 0)
            result.append({
                'session_id': sid,
                'session_name': s['session_name'],
                'branch_name': s['branch_name'],
                'cashier_name': s['cashier_name'],
                'opening_time': s['opening_time'].isoformat() if s['opening_time'] else None,
                'closing_time': s['closing_time'].isoformat() if s['closing_time'] else None,
                'total_sales': round(total_s, 2),
                'net_sales': round(total_s - total_ref, 2),
                'total_tax': round(float(s['total_tax'] or 0), 2),
                'total_orders': int(s['total_orders'] or 0),
                'total_refunds': round(total_ref, 2),
                'total_discounts': round(disc_map.get(sid, 0), 2),
                'avg_order_value': round(float(s['avg_order_value'] or 0), 2),
                'payment_methods': pay_map.get(sid, []),
            })
        return result
