# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta
import pytz

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

VALID_STATES = ('paid', 'done', 'invoiced', 'posted')
TZ_ADDIS = 'Africa/Addis_Ababa'

# Map group_by selection to SQL date_trunc / format pairs
_GROUP_BY_SQL = {
    'hour_of_day': ('hour',   'HH24',    'HH24":00"'),
    'day':         ('day',    'YYYY-MM-DD', 'DD Mon'),
    'week':        ('week',   'IYYY-IW', '"W"IW IYYY'),
    'month':       ('month',  'YYYY-MM', 'Mon YYYY'),
    'year':        ('year',   'YYYY',    'YYYY'),
    'day_of_week': ('dow',    None,      None),       # special
    'pos_session': ('session', None,     None),       # special
}


def _to_local(dt_utc, tz_name=TZ_ADDIS):
    if not dt_utc:
        return None
    tz = pytz.timezone(tz_name)
    if dt_utc.tzinfo is None:
        dt_utc = pytz.utc.localize(dt_utc)
    return dt_utc.astimezone(tz)


def _to_utc(dt_local, tz_name=TZ_ADDIS):
    if not dt_local:
        return None
    tz = pytz.timezone(tz_name)
    if isinstance(dt_local, str):
        dt_local = datetime.strptime(dt_local, '%Y-%m-%d %H:%M:%S')
    if dt_local.tzinfo is None:
        dt_local = tz.localize(dt_local)
    return dt_local.astimezone(pytz.utc).replace(tzinfo=None)


def _date_range_utc(date_start_str, date_end_str, tz_name=TZ_ADDIS):
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
        if filters is None:
            filters = {}
        try:
            params = self._prepare_params(filters)
            pos_hr_available = self._check_pos_hr()
            # Respect the enable_targets flag sent from the dashboard so we
            # skip the target SQL entirely when targets are disabled.
            enable_targets = filters.get('enable_targets', True)
            result = {
                'kpis': self._get_kpis(params),
                'sales_trend': self._get_sales_trend(params),
                'top_products': self._get_top_products(params),
                'top_categories': self._get_top_categories(params),
                'cashier_performance': self._get_cashier_performance(params),
                'peak_hours': self._get_peak_hours(params),
                'peak_days': self._get_peak_days(params),
                'payment_methods': self._get_payment_methods(params),
                'branch_comparison': self._get_branch_comparison(params),
                'refund_discount_summary': self._get_refund_discount_summary(params),
                'target_summary': self._get_target_summary(params) if enable_targets else [],
                'pos_hr_available': pos_hr_available,
                'waiter_performance': self._get_waiter_performance(params) if pos_hr_available else [],
            }
            return result
        except Exception as e:
            _logger.exception('POS Analytics get_dashboard_data error: %s', e)
            raise UserError(_('Analytics error: %s') % str(e))

    @api.model
    def get_filter_options(self):
        company_id = self.env.company.id
        pos_hr_available = self._check_pos_hr()

        pos_configs = self.env['pos.config'].search([('company_id', '=', company_id)])
        cashiers = self.env['res.users'].search([
            ('groups_id', 'in', self.env.ref('point_of_sale.group_pos_user').ids),
        ])
        waiters = self.env['hr.employee'].search(
            [('company_id', '=', company_id)]
        ) if pos_hr_available else self.env['hr.employee']
        categories = self.env['product.category'].search([])
        payment_methods = self.env['pos.payment.method'].search([('company_id', '=', company_id)])

        # Companies the current user can access (multi-company)
        allowed_companies = self.env['res.company'].search(
            [('id', 'in', self.env.user.company_ids.ids)]
        )

        return {
            'pos_configs': [{'id': c.id, 'name': c.name} for c in pos_configs],
            'cashiers': [{'id': u.id, 'name': u.name} for u in cashiers],
            'waiters': [{'id': e.id, 'name': e.name} for e in waiters],
            'categories': [{'id': c.id, 'name': c.name} for c in categories],
            'payment_methods': [{'id': p.id, 'name': p.name} for p in payment_methods],
            'companies': [{'id': c.id, 'name': c.name} for c in allowed_companies],
            'currency_symbol': self.env.company.currency_id.symbol or '',
            'currency_name': self.env.company.currency_id.name or '',
            'timezone': TZ_ADDIS,
            'pos_hr_available': pos_hr_available,
        }

    @api.model
    def get_daily_closing_data(self, filters=None):
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
                ps.state                                AS session_state,
                ps.start_at                             AS opening_time,
                ps.stop_at                              AS closing_time,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS total_sales,
                COALESCE(SUM(o.amount_tax), 0)          AS total_tax,
                COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END) AS total_orders,
                COALESCE(SUM(CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total) ELSE 0 END), 0) AS total_refunds,
                COALESCE(AVG(CASE WHEN o.amount_total >= 0 THEN o.amount_total END), 0) AS avg_order_value,
                COALESCE(SUM(o.amount_return), 0)       AS total_change_returned
            FROM pos_order o
            JOIN pos_session ps ON ps.id = o.session_id
            JOIN pos_config pc ON pc.id = o.config_id
            JOIN res_users ru ON ru.id = ps.user_id
            WHERE {where}
            GROUP BY ps.id, ps.name, pc.name, ru.name, ps.state, ps.start_at, ps.stop_at
            ORDER BY ps.start_at
        """, args)
        sessions = self.env.cr.dictfetchall()

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
        pay_map = {}
        for r in self.env.cr.dictfetchall():
            sid = r['session_id']
            pay_map.setdefault(sid, []).append(
                {'method': r['method_name'], 'amount': round(float(r['amount'] or 0), 2)}
            )

        disc_where, disc_args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                o.session_id,
                COALESCE(SUM(CASE WHEN l.discount > 0
                    THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS total_discounts
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            WHERE {disc_where}
            GROUP BY o.session_id
        """, disc_args)
        disc_map = {r['session_id']: float(r['total_discounts'] or 0)
                    for r in self.env.cr.dictfetchall()}

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
                'session_state': s['session_state'],
                'opening_time': s['opening_time'].isoformat() if s['opening_time'] else None,
                'closing_time': s['closing_time'].isoformat() if s['closing_time'] else None,
                'total_sales': round(total_s, 2),
                'net_sales': round(total_s - total_ref, 2),
                'total_tax': round(float(s['total_tax'] or 0), 2),
                'total_orders': int(s['total_orders'] or 0),
                'total_refunds': round(total_ref, 2),
                'total_discounts': round(disc_map.get(sid, 0), 2),
                'avg_order_value': round(float(s['avg_order_value'] or 0), 2),
                'total_change_returned': round(float(s['total_change_returned'] or 0), 2),
                'payment_methods': pay_map.get(sid, []),
            })
        return result

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    @api.model
    def _check_pos_hr(self):
        """Return True if pos_hr is installed and employee_id exists on pos.order."""
        try:
            return 'employee_id' in self.env['pos.order']._fields
        except Exception:
            return False

    @api.model
    def _prepare_params(self, filters):
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

        # order_state: 'all' means all VALID_STATES, otherwise single state
        order_state_filter = filters.get('order_state', 'all')
        if order_state_filter and order_state_filter != 'all':
            effective_states = (order_state_filter,)
        else:
            effective_states = VALID_STATES

        # company_id: use selected company or current company
        company_id_filter = filters.get('company_id')
        if company_id_filter:
            # Validate user has access
            allowed = self.env.user.company_ids.ids
            company_id = int(company_id_filter) if int(company_id_filter) in allowed else self.env.company.id
        else:
            company_id = self.env.company.id

        params = {
            'utc_start': utc_start,
            'utc_end': utc_end,
            'd_start': d_start,
            'd_end': d_end,
            'period': period,
            'report_basis': filters.get('report_basis', 'sales_incl_tax'),
            'group_by': filters.get('group_by', 'day'),
            'order_state': order_state_filter,
            'effective_states': effective_states,
            'pos_config_ids': [int(x) for x in filters.get('pos_config_ids', []) if x],
            'cashier_ids': [int(x) for x in filters.get('cashier_ids', []) if x],
            'waiter_ids': [int(x) for x in filters.get('waiter_ids', []) if x],
            'product_category_ids': [int(x) for x in filters.get('product_category_ids', []) if x],
            'product_ids': [int(x) for x in filters.get('product_ids', []) if x],
            'payment_method_ids': [int(x) for x in filters.get('payment_method_ids', []) if x],
            'company_id': company_id,
            'tz': TZ_ADDIS,
        }
        return params

    def _build_order_where(self, params, alias='o'):
        clauses = [
            f"{alias}.company_id = %s",
            f"{alias}.state IN %s",
            f"{alias}.date_order >= %s",
            f"{alias}.date_order <= %s",
        ]
        args = [
            params['company_id'],
            params['effective_states'],
            params['utc_start'],
            params['utc_end'],
        ]
        if params.get('pos_config_ids'):
            clauses.append(f"{alias}.config_id = ANY(%s)")
            args.append(params['pos_config_ids'])
        if params.get('cashier_ids'):
            clauses.append(f"{alias}.user_id = ANY(%s)")
            args.append(params['cashier_ids'])
        if params.get('waiter_ids') and self._check_pos_hr():
            clauses.append(f"{alias}.employee_id = ANY(%s)")
            args.append(params['waiter_ids'])
        return ' AND '.join(clauses), args

    def _build_line_where(self, params, order_alias='o', line_alias='l'):
        base_where, args = self._build_order_where(params, alias=order_alias)
        if params.get('product_ids'):
            base_where += f" AND {line_alias}.product_id = ANY(%s)"
            args.append(params['product_ids'])
        if params.get('product_category_ids'):
            base_where += " AND pt.categ_id = ANY(%s)"
            args.append(params['product_category_ids'])
        return base_where, args

    # -------------------------------------------------------------------------
    # KPI — includes 5 new fields (K)
    # -------------------------------------------------------------------------

    @api.model
    def _get_kpis(self, params):
        where, args = self._build_order_where(params)

        self.env.cr.execute(f"""
            SELECT
                COALESCE(SUM(o.amount_total), 0)     AS total_sales,
                COALESCE(SUM(o.amount_tax), 0)       AS total_tax,
                COALESCE(SUM(o.amount_paid), 0)      AS total_paid,
                COALESCE(SUM(o.amount_return), 0)    AS total_change_returned,
                COUNT(*)                              AS total_orders,
                COALESCE(AVG(o.amount_total), 0)     AS avg_order_value,
                COUNT(DISTINCT o.session_id)          AS total_sessions
            FROM pos_order o
            WHERE {where}
              AND o.amount_total >= 0
        """, args)
        row = self.env.cr.dictfetchone()

        self.env.cr.execute(f"""
            SELECT COALESCE(SUM(ABS(o.amount_total)), 0) AS total_refunds,
                   COUNT(*) AS refund_orders
            FROM pos_order o
            WHERE {where}
              AND o.amount_total < 0
        """, args)
        refund_row = self.env.cr.dictfetchone()

        where_line, args_line = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                COALESCE(SUM(CASE WHEN l.discount > 0
                    THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS total_discounts,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.qty ELSE 0 END), 0) AS total_qty
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            WHERE {where_line}
        """, args_line)
        line_row = self.env.cr.dictfetchone()

        pay_where, pay_args = self._build_order_where(params)
        if params.get('payment_method_ids'):
            pay_where += " AND pp.payment_method_id = ANY(%s)"
            pay_args.append(params['payment_method_ids'])
        self.env.cr.execute(f"""
            SELECT pm.name AS method_name, COALESCE(SUM(pp.amount), 0) AS total_amount
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

        top_waiter_row = None
        if self._check_pos_hr():
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
        refund_orders = int(refund_row['refund_orders'] or 0)
        total_qty_sold = float(line_row['total_qty'] or 0)
        total_discounts = float(line_row['total_discounts'] or 0)

        # Derived KPIs (K)
        refund_rate = round((refund_orders / total_orders * 100) if total_orders else 0, 2)
        discount_rate = round((total_discounts / total_sales * 100) if total_sales else 0, 2)
        avg_items_per_order = round((total_qty_sold / total_orders) if total_orders else 0, 2)
        total_sessions = int(row['total_sessions'] or 0)
        total_change_returned = round(float(row['total_change_returned'] or 0), 2)

        return {
            'total_sales': round(total_sales, 2),
            'net_sales': round(net_sales, 2),
            'total_orders': total_orders,
            'avg_order_value': round(float(row['avg_order_value'] or 0), 2),
            'total_qty_sold': round(total_qty_sold, 2),
            'total_discounts': round(total_discounts, 2),
            'total_refunds': round(total_refunds, 2),
            'refund_orders': refund_orders,
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
            # New KPIs (K)
            'refund_rate': refund_rate,
            'discount_rate': discount_rate,
            'avg_items_per_order': avg_items_per_order,
            'total_sessions': total_sessions,
            'total_change_returned': total_change_returned,
        }

    # -------------------------------------------------------------------------
    # REPORT BASIS — SQL expression helpers
    # -------------------------------------------------------------------------

    def _trend_sales_exprs(self, params):
        """Return (positive_sales_expr, refund_expr) SQL fragments for report_basis."""
        basis = params.get('report_basis', 'sales_incl_tax')
        if basis == 'sales_excl_tax':
            # Exclude tax component from both sales and refunds
            pos_expr = "CASE WHEN o.amount_total >= 0 THEN (o.amount_total - o.amount_tax) ELSE 0 END"
            ref_expr = "CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total - o.amount_tax) ELSE 0 END"
        elif basis == 'net_sales':
            # Positive orders only; refunds shown separately so net = sales - refunds
            pos_expr = "CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END"
            ref_expr = "CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total) ELSE 0 END"
        else:
            # sales_incl_tax (default)
            pos_expr = "CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END"
            ref_expr = "CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total) ELSE 0 END"
        return pos_expr, ref_expr

    def _trend_label(self, basis):
        """Human-readable y-axis label suffix for report_basis."""
        return {
            'sales_excl_tax': 'excl. tax',
            'net_sales': 'net',
            'qty_sold': 'qty',
        }.get(basis, '')

    # -------------------------------------------------------------------------
    # SALES TREND — respects group_by (C) and report_basis (G1)
    # -------------------------------------------------------------------------

    @api.model
    def _get_sales_trend(self, params):
        period = params['period']
        group_by = params.get('group_by', 'day')

        # Resolve trunc / label_format from group_by
        if group_by == 'hour_of_day':
            trunc, label_format = 'hour', 'HH24":00"'
        elif group_by in ('day', 'day_of_week'):
            delta = (params['d_end'] - params['d_start']).days
            trunc = 'hour' if delta <= 1 else 'day'
            label_format = 'HH24":00"' if delta <= 1 else 'DD Mon'
        elif group_by == 'week':
            trunc, label_format = 'week', '"W"IW IYYY'
        elif group_by == 'month':
            trunc, label_format = 'month', 'Mon YYYY'
        elif group_by == 'year':
            trunc, label_format = 'year', 'YYYY'
        elif group_by == 'pos_session':
            return self._get_sales_trend_by_session(params)
        else:
            # Auto-detect from period
            if period in ('today', 'yesterday'):
                trunc, label_format = 'hour', 'HH24":00"'
            elif period in ('this_week', 'last_week'):
                trunc, label_format = 'day', 'DD Mon'
            elif period in ('this_month', 'last_month'):
                trunc, label_format = 'day', 'DD'
            elif period == 'this_year':
                trunc, label_format = 'month', 'Mon YYYY'
            else:
                delta = (params['d_end'] - params['d_start']).days
                if delta <= 1:
                    trunc, label_format = 'hour', 'HH24":00"'
                elif delta <= 31:
                    trunc, label_format = 'day', 'DD Mon'
                elif delta <= 366:
                    trunc, label_format = 'month', 'Mon YYYY'
                else:
                    trunc, label_format = 'year', 'YYYY'

        basis = params.get('report_basis', 'sales_incl_tax')

        # qty_sold basis requires joining order lines — different query path
        if basis == 'qty_sold':
            return self._get_sales_trend_qty(params, trunc, label_format)

        pos_expr, ref_expr = self._trend_sales_exprs(params)
        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                TO_CHAR(DATE_TRUNC(%s, o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s), %s) AS period_label,
                DATE_TRUNC(%s, o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s) AS period_start,
                COALESCE(SUM({pos_expr}), 0) AS total_sales,
                COALESCE(SUM({ref_expr}), 0) AS total_refunds,
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

    def _get_sales_trend_qty(self, params, trunc, label_format):
        """Trend grouped by qty sold per period (report_basis='qty_sold')."""
        where, args = self._build_line_where(params)
        self.env.cr.execute(f"""
            SELECT
                TO_CHAR(DATE_TRUNC(%s, o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s), %s) AS period_label,
                DATE_TRUNC(%s, o.date_order AT TIME ZONE 'UTC' AT TIME ZONE %s) AS period_start,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.qty ELSE 0 END), 0) AS total_sales,
                COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.qty) ELSE 0 END), 0) AS total_refunds,
                COUNT(DISTINCT CASE WHEN o.amount_total >= 0 THEN o.id END) AS order_count
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
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

    def _get_sales_trend_by_session(self, params):
        """Trend grouped by POS session — respects report_basis."""
        basis = params.get('report_basis', 'sales_incl_tax')

        if basis == 'qty_sold':
            where, args = self._build_line_where(params)
            self.env.cr.execute(f"""
                SELECT
                    ps.name AS period_label,
                    ps.start_at AS period_start,
                    COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.qty ELSE 0 END), 0) AS total_sales,
                    COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.qty) ELSE 0 END), 0) AS total_refunds,
                    COUNT(DISTINCT CASE WHEN o.amount_total >= 0 THEN o.id END) AS order_count
                FROM pos_order_line l
                JOIN pos_order o ON o.id = l.order_id
                JOIN pos_session ps ON ps.id = o.session_id
                WHERE {where}
                GROUP BY ps.id, ps.name, ps.start_at
                ORDER BY ps.start_at
            """, args)
        else:
            pos_expr, ref_expr = self._trend_sales_exprs(params)
            where, args = self._build_order_where(params)
            self.env.cr.execute(f"""
                SELECT
                    ps.name AS period_label,
                    ps.start_at AS period_start,
                    COALESCE(SUM({pos_expr}), 0) AS total_sales,
                    COALESCE(SUM({ref_expr}), 0) AS total_refunds,
                    COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END) AS order_count
                FROM pos_order o
                JOIN pos_session ps ON ps.id = o.session_id
                WHERE {where}
                GROUP BY ps.id, ps.name, ps.start_at
                ORDER BY ps.start_at
            """, args)

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
    def _get_top_products(self, params, limit=20):
        basis = params.get('report_basis', 'sales_incl_tax')
        order_col = {
            'sales_excl_tax': 'net_sales',
            'qty_sold':        'qty_sold',
            'net_sales':       'after_refunds',
        }.get(basis, 'gross_sales')

        where, args = self._build_line_where(params)
        self.env.cr.execute(f"""
            SELECT
                l.product_id,
                pt.name                                                                             AS product_name,
                pc.name                                                                             AS categ_name,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.qty ELSE 0 END), 0)                       AS qty_sold,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal_incl ELSE 0 END), 0)       AS gross_sales,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal ELSE 0 END), 0)            AS net_sales,
                COALESCE(SUM(CASE WHEN l.qty > 0
                    THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0)              AS discount_amount,
                COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.qty) ELSE 0 END), 0)                 AS refund_qty,
                COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.price_subtotal_incl) ELSE 0 END), 0) AS refund_amount,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal_incl ELSE 0 END), 0)
                    - COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.price_subtotal_incl) ELSE 0 END), 0)
                                                                                                   AS after_refunds
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN product_product pp ON pp.id = l.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN product_category pc ON pc.id = pt.categ_id
            WHERE {where}
            GROUP BY l.product_id, pt.name, pc.name
            ORDER BY {order_col} DESC
            LIMIT %s
        """, args + [limit])
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
            for r in self.env.cr.dictfetchall()
        ]

    # -------------------------------------------------------------------------
    # TOP CATEGORIES
    # -------------------------------------------------------------------------

    @api.model
    def _get_top_categories(self, params, limit=20):
        basis = params.get('report_basis', 'sales_incl_tax')
        order_col = {
            'sales_excl_tax': 'net_sales',
            'qty_sold':        'qty_sold',
            'net_sales':       'after_refunds',
        }.get(basis, 'gross_sales')

        where, args = self._build_line_where(params)
        self.env.cr.execute(f"""
            SELECT
                pc.id                                                                             AS categ_id,
                pc.name                                                                           AS categ_name,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.qty ELSE 0 END), 0)                     AS qty_sold,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal_incl ELSE 0 END), 0)     AS gross_sales,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal ELSE 0 END), 0)          AS net_sales,
                COALESCE(SUM(CASE WHEN l.qty > 0
                    THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0)            AS discount_amount,
                COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.qty) ELSE 0 END), 0)               AS refund_qty,
                COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.price_subtotal_incl) ELSE 0 END), 0) AS refund_amount,
                COALESCE(SUM(CASE WHEN l.qty > 0 THEN l.price_subtotal_incl ELSE 0 END), 0)
                    - COALESCE(SUM(CASE WHEN l.qty < 0 THEN ABS(l.price_subtotal_incl) ELSE 0 END), 0)
                                                                                                  AS after_refunds
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN product_product pp ON pp.id = l.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN product_category pc ON pc.id = pt.categ_id
            WHERE {where}
            GROUP BY pc.id, pc.name
            ORDER BY {order_col} DESC
            LIMIT %s
        """, args + [limit])
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
            for r in self.env.cr.dictfetchall()
        ]

    # -------------------------------------------------------------------------
    # WAITER PERFORMANCE — guarded by pos_hr check (O)
    # -------------------------------------------------------------------------

    @api.model
    def _get_waiter_performance(self, params):
        if not self._check_pos_hr():
            return []
        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                he.id                                                                            AS employee_id,
                he.name                                                                          AS waiter_name,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0)  AS total_sales,
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

        self.env.cr.execute(f"""
            SELECT
                o.employee_id,
                COALESCE(SUM(CASE WHEN l.discount > 0
                    THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS total_discounts,
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
                ru.id                                                                            AS user_id,
                ru.name                                                                          AS cashier_name,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0)  AS total_collected,
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

        self.env.cr.execute(f"""
            SELECT
                o.user_id,
                COALESCE(SUM(CASE WHEN l.discount > 0
                    THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS total_discounts
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            WHERE {where}
              AND o.user_id IS NOT NULL
            GROUP BY o.user_id
        """, args)
        discount_map = {r['user_id']: r for r in self.env.cr.dictfetchall()}

        pay_where, pay_args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT o.user_id, pm.name AS method_name, COALESCE(SUM(pp.amount), 0) AS amount
            FROM pos_payment pp
            JOIN pos_payment_method pm ON pm.id = pp.payment_method_id
            JOIN pos_order o ON o.id = pp.pos_order_id
            WHERE {pay_where}
              AND o.user_id IS NOT NULL
            GROUP BY o.user_id, pm.name
        """, pay_args)
        pay_map = {}
        for r in self.env.cr.dictfetchall():
            uid = r['user_id']
            pay_map.setdefault(uid, []).append(
                {'method': r['method_name'], 'amount': round(float(r['amount'] or 0), 2)}
            )

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
        return [
            {
                'hour': r['hour'],
                'label': f"{r['hour']:02d}:00",
                'order_count': int(r['order_count'] or 0),
                'total_sales': round(float(r['total_sales'] or 0), 2),
            }
            for r in self.env.cr.dictfetchall()
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
        return [
            {
                'day_num': r['day_num'],
                'day_name': (r['day_name'] or '').strip(),
                'order_count': int(r['order_count'] or 0),
                'total_sales': round(float(r['total_sales'] or 0), 2),
            }
            for r in self.env.cr.dictfetchall()
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
                pm.id AS method_id,
                pm.name AS method_name,
                COALESCE(SUM(pp.amount), 0) AS total_amount,
                COUNT(DISTINCT pp.pos_order_id) AS order_count
            FROM pos_payment pp
            JOIN pos_payment_method pm ON pm.id = pp.payment_method_id
            JOIN pos_order o ON o.id = pp.pos_order_id
            WHERE {pay_where}
            GROUP BY pm.id, pm.name
            ORDER BY total_amount DESC
        """, pay_args)
        return [
            {
                'method_id': r['method_id'],
                'method_name': r['method_name'],
                'total_amount': round(float(r['total_amount'] or 0), 2),
                'order_count': int(r['order_count'] or 0),
            }
            for r in self.env.cr.dictfetchall()
        ]

    # -------------------------------------------------------------------------
    # BRANCH COMPARISON
    # -------------------------------------------------------------------------

    @api.model
    def _get_branch_comparison(self, params):
        where, args = self._build_order_where(params)
        self.env.cr.execute(f"""
            SELECT
                pc.id AS config_id,
                pc.name AS branch_name,
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS total_sales,
                COALESCE(SUM(CASE WHEN o.amount_total < 0 THEN ABS(o.amount_total) ELSE 0 END), 0) AS total_refunds,
                COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END) AS total_orders,
                COALESCE(AVG(CASE WHEN o.amount_total >= 0 THEN o.amount_total END), 0) AS avg_order_value
            FROM pos_order o
            JOIN pos_config pc ON pc.id = o.config_id
            WHERE {where}
            GROUP BY pc.id, pc.name
            ORDER BY total_sales DESC
        """, args)
        branch_rows = self.env.cr.dictfetchall()

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
        top_product_map = {r['config_id']: r['top_product']
                           for r in self.env.cr.dictfetchall()}

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
        peak_hour_map = {r['config_id']: r['peak_hour']
                         for r in self.env.cr.dictfetchall()}

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

        self.env.cr.execute(f"""
            SELECT
                COALESCE(SUM(ABS(o.amount_total)), 0) AS total_refund_amount,
                COUNT(*) AS refund_order_count
            FROM pos_order o
            WHERE {where}
              AND o.amount_total < 0
        """, args)
        ref_row = self.env.cr.dictfetchone()

        self.env.cr.execute(f"""
            SELECT
                COALESCE(SUM(CASE WHEN l.discount > 0
                    THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS total_discount_amount,
                COUNT(DISTINCT CASE WHEN l.discount > 0 THEN l.order_id END) AS discounted_order_count
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            WHERE {where}
        """, args)
        disc_row = self.env.cr.dictfetchone()

        self.env.cr.execute(f"""
            SELECT
                ru.name AS cashier_name,
                COALESCE(SUM(CASE WHEN l.discount > 0
                    THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS discount_amount
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN res_users ru ON ru.id = o.user_id
            WHERE {where}
              AND l.discount > 0
            GROUP BY ru.name
            ORDER BY discount_amount DESC
            LIMIT 20
        """, args)
        disc_cashier = self.env.cr.dictfetchall()

        self.env.cr.execute(f"""
            SELECT
                pt.name AS product_name,
                pc.name AS categ_name,
                COALESCE(SUM(CASE WHEN l.discount > 0
                    THEN (l.price_unit * l.qty * l.discount / 100.0) ELSE 0 END), 0) AS discount_amount
            FROM pos_order_line l
            JOIN pos_order o ON o.id = l.order_id
            JOIN product_product pp ON pp.id = l.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN product_category pc ON pc.id = pt.categ_id
            WHERE {where}
              AND l.discount > 0
            GROUP BY pt.name, pc.name
            ORDER BY discount_amount DESC
            LIMIT 20
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
                {', o.employee_id' if self._check_pos_hr() else ', NULL::integer AS employee_id'},
                COALESCE(SUM(CASE WHEN o.amount_total >= 0 THEN o.amount_total ELSE 0 END), 0) AS actual_sales,
                COUNT(CASE WHEN o.amount_total >= 0 THEN 1 END) AS actual_orders
            FROM pos_order o
            WHERE {where}
            GROUP BY o.config_id, o.user_id{', o.employee_id' if self._check_pos_hr() else ''}
        """, args)
        actuals = self.env.cr.dictfetchall()

        result = []
        for t in targets:
            config_id = t.pos_config_id.id if t.pos_config_id else None
            user_id = t.cashier_id.id if t.cashier_id else None
            employee_id = t.waiter_id.id if t.waiter_id else None

            actual_sales = 0.0
            actual_orders = 0
            for a in actuals:
                match = True
                if config_id and a['config_id'] != config_id:
                    match = False
                if user_id and a['user_id'] != user_id:
                    match = False
                if employee_id and a.get('employee_id') != employee_id:
                    match = False
                if match:
                    actual_sales += float(a['actual_sales'] or 0)
                    actual_orders += int(a['actual_orders'] or 0)

            target_amount = float(t.target_amount or 0)
            achievement_pct = (actual_sales / target_amount * 100) if target_amount else 0
            remaining = max(0, target_amount - actual_sales)
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
    # SCHEDULED NOTIFICATIONS (Q)
    # -------------------------------------------------------------------------

    @api.model
    def _send_target_notifications(self):
        """Cron: notify managers when a target is achieved (>=100%) or close (>=90%)."""
        try:
            tz = pytz.timezone(TZ_ADDIS)
            today = datetime.now(tz).date()
            Target = self.env['pos.analytics.target']
            active_targets = Target.search([
                ('active', '=', True),
                ('date_start', '<=', today),
                ('date_end', '>=', today),
            ])
            if not active_targets:
                return

            manager_group = self.env.ref(
                'pos_advanced_analytics.group_pos_analytics_manager', raise_if_not_found=False
            )
            managers = manager_group.users if manager_group else self.env['res.users']

            for target in active_targets:
                filters = {
                    'period': 'custom',
                    'date_start': target.date_start.strftime('%Y-%m-%d'),
                    'date_end': today.strftime('%Y-%m-%d'),
                    'pos_config_ids': [target.pos_config_id.id] if target.pos_config_id else [],
                    'cashier_ids': [target.cashier_id.id] if target.cashier_id else [],
                    'waiter_ids': [target.waiter_id.id] if target.waiter_id else [],
                }
                try:
                    params = self._prepare_params(filters)
                    where, args = self._build_order_where(params)
                    self.env.cr.execute(f"""
                        SELECT COALESCE(SUM(o.amount_total), 0) AS actual_sales
                        FROM pos_order o
                        WHERE {where} AND o.amount_total >= 0
                    """, args)
                    row = self.env.cr.dictfetchone()
                    actual_sales = float(row['actual_sales'] or 0)
                    target_amount = float(target.target_amount or 1)
                    pct = actual_sales / target_amount * 100

                    if pct >= 100:
                        subject = _('Target Achieved: %s') % target.name
                        body = _(
                            'Target <b>%s</b> has been <b>achieved</b>! '
                            'Actual: %s / Target: %s (%.1f%%).'
                        ) % (target.name, actual_sales, target_amount, pct)
                    elif pct >= 90:
                        subject = _('Target Almost Reached: %s') % target.name
                        body = _(
                            'Target <b>%s</b> is at <b>%.1f%%</b>. '
                            'Actual: %s / Target: %s. Push to close the gap!'
                        ) % (target.name, pct, actual_sales, target_amount)
                    else:
                        continue

                    for user in managers:
                        self.env['mail.activity'].sudo().create({
                            'res_model_id': self.env['ir.model']._get('pos.analytics.target').id,
                            'res_id': target.id,
                            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                            'summary': subject,
                            'note': body,
                            'user_id': user.id,
                        })
                except Exception as inner_err:
                    _logger.warning('Target notify error for target %s: %s', target.id, inner_err)

        except Exception as e:
            _logger.error('_send_target_notifications cron error: %s', e)

    @api.model
    def _send_rate_alerts(self):
        """Cron: alert managers when today's refund rate or discount rate is high."""
        try:
            tz = pytz.timezone(TZ_ADDIS)
            today = datetime.now(tz).date()
            filters = {
                'period': 'today',
                'date_start': today.strftime('%Y-%m-%d'),
                'date_end': today.strftime('%Y-%m-%d'),
            }
            params = self._prepare_params(filters)
            kpis = self._get_kpis(params)

            refund_rate = kpis.get('refund_rate', 0)
            discount_rate = kpis.get('discount_rate', 0)

            # configurable thresholds (or use defaults)
            ICP = self.env['ir.config_parameter'].sudo()
            refund_threshold = float(ICP.get_param(
                'pos_advanced_analytics.refund_rate_threshold', '10'))
            discount_threshold = float(ICP.get_param(
                'pos_advanced_analytics.discount_rate_threshold', '20'))

            alerts = []
            if refund_rate > refund_threshold:
                alerts.append(_(
                    'Today\'s refund rate is <b>%.2f%%</b> (threshold: %.0f%%).'
                ) % (refund_rate, refund_threshold))
            if discount_rate > discount_threshold:
                alerts.append(_(
                    'Today\'s discount rate is <b>%.2f%%</b> (threshold: %.0f%%).'
                ) % (discount_rate, discount_threshold))

            if not alerts:
                return

            manager_group = self.env.ref(
                'pos_advanced_analytics.group_pos_analytics_manager', raise_if_not_found=False
            )
            managers = manager_group.users if manager_group else self.env['res.users']
            body = '<br/>'.join(alerts)
            subject = _('POS Analytics Alert: High Rate Detected')

            company = self.env.company
            company.message_post(
                subject=subject,
                body=body,
                partner_ids=managers.mapped('partner_id').ids,
                subtype_id=self.env.ref('mail.mt_comment').id,
            )

        except Exception as e:
            _logger.error('_send_rate_alerts cron error: %s', e)
