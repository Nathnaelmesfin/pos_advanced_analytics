# -*- coding: utf-8 -*-
{
    'name': 'POS Advanced Analytics Dashboard',
    'version': '17.0.1.0.0',
    'category': 'Point of Sale',
    'summary': 'Advanced analytics dashboard for Point of Sale with live KPIs, reports, targets, and branch comparison',
    'description': """
        POS Advanced Analytics Dashboard
        ==================================
        A comprehensive analytics module for Odoo 17 Point of Sale designed for
        café and catering businesses.

        Features:
        - Live backend OWL dashboard with auto-refresh
        - KPI cards: Sales, Orders, AOV, Discounts, Refunds, Tax, Payment methods
        - Sales trend, peak hours, peak days charts
        - Top products and categories charts
        - Waiter and cashier performance tables
        - Branch comparison analytics
        - Payment method breakdown
        - Refund and discount analytics
        - Sales targets (branch, cashier, waiter level)
        - Report wizard with PDF and Excel export
        - Daily closing report for Z-report comparison
        - Multi-branch support using pos.config
        - Africa/Addis_Ababa timezone reporting
        - Security groups for analytics users and managers
    """,
    'author': 'POS Advanced Analytics',
    'license': 'LGPL-3',
    'depends': [
        'point_of_sale',
        'pos_restaurant',
        'pos_hr',
        'web',
        'product',
        'hr',
        'account',
    ],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/dashboard_actions.xml',
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
            'pos_advanced_analytics/static/src/js/pos_analytics_service.js',
            'pos_advanced_analytics/static/src/js/pos_analytics_dashboard.js',
            'pos_advanced_analytics/static/src/xml/pos_analytics_dashboard.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
