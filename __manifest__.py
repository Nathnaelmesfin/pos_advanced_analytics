# -*- coding: utf-8 -*-
{
    'name': 'POS Advanced Analytics Dashboard',
    'version': '17.0.2.0.0',
    'category': 'Point of Sale',
    'summary': 'Advanced analytics dashboard for Point of Sale with live KPIs, reports, targets, and branch comparison',
    'description': """
        POS Advanced Analytics Dashboard
        ==================================
        A comprehensive analytics module for Odoo 17 Point of Sale designed for
        café and catering businesses.

        Features:
        - Live backend OWL dashboard with auto-refresh
        - 23+ KPI cards: Sales, Orders, AOV, Discounts, Refunds, Tax,
          Refund Rate, Discount Rate, Sessions, Avg Items/Order, Change Returned
        - Sales trend, peak hours, peak days charts (Chart.js)
        - Top products and categories charts
        - Waiter and cashier performance tables with pagination
        - Branch comparison analytics
        - Payment method breakdown
        - Refund and discount analytics
        - Sales targets (branch, cashier, waiter level)
        - Report wizard with PDF and Excel export (13 type-specific reports)
        - Daily closing tab (live Z-report / session dashboard)
        - Multi-branch + multi-company support
        - Africa/Addis_Ababa timezone reporting
        - Security groups for analytics users and managers
        - Scheduled notifications (target achieved, high refund/discount rate)
    """,
    'author': 'POS Advanced Analytics',
    'license': 'LGPL-3',
    'depends': [
        'point_of_sale',
        'web',
        'product',
        'hr',
        'account',
        'mail',
        'bus',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/dashboard_actions.xml',
        'data/cron_data.xml',
        'views/pos_analytics_menu.xml',
        'views/pos_analytics_target_views.xml',
        'views/pos_analytics_report_wizard_views.xml',
        'views/res_config_settings_views.xml',
        'reports/pos_sales_report_action.xml',
        'reports/pos_sales_report_template.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'pos_advanced_analytics/static/src/scss/pos_analytics_dashboard.scss',
            'pos_advanced_analytics/static/lib/chart.umd.min.js',
            'pos_advanced_analytics/static/src/js/chart_loader.js',
            'pos_advanced_analytics/static/src/js/pos_analytics_service.js',
            'pos_advanced_analytics/static/src/js/pos_analytics_dashboard.js',
            'pos_advanced_analytics/static/src/xml/pos_analytics_dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
