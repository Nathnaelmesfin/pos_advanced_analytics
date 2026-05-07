# -*- coding: utf-8 -*-
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class PosAnalyticsController(http.Controller):

    @http.route('/pos_analytics/dashboard_data', type='json', auth='user', methods=['POST'])
    def get_dashboard_data(self, filters=None):
        """JSON endpoint for the OWL dashboard to fetch analytics data."""
        try:
            if filters is None:
                filters = {}
            data = request.env['pos.analytics.service'].get_dashboard_data(filters)
            return {'status': 'ok', 'data': data}
        except Exception as e:
            _logger.exception('Dashboard data error: %s', e)
            return {'status': 'error', 'message': str(e)}

    @http.route('/pos_analytics/filter_options', type='json', auth='user', methods=['POST'])
    def get_filter_options(self):
        """Return dropdown options for dashboard filters."""
        try:
            options = request.env['pos.analytics.service'].get_filter_options()
            return {'status': 'ok', 'data': options}
        except Exception as e:
            _logger.exception('Filter options error: %s', e)
            return {'status': 'error', 'message': str(e)}

    @http.route('/pos_analytics/closing_data', type='json', auth='user', methods=['POST'])
    def get_closing_data(self, filters=None):
        """Return daily closing / Z-report data."""
        try:
            if filters is None:
                filters = {}
            data = request.env['pos.analytics.service'].get_daily_closing_data(filters)
            return {'status': 'ok', 'data': data}
        except Exception as e:
            _logger.exception('Closing data error: %s', e)
            return {'status': 'error', 'message': str(e)}

    @http.route('/pos_analytics/settings', type='json', auth='user', methods=['POST'])
    def get_analytics_settings(self):
        """Return current analytics configuration parameters."""
        try:
            ICP = request.env['ir.config_parameter'].sudo()
            settings = {
                'default_period': ICP.get_param('pos_advanced_analytics.default_period', 'today'),
                'refresh_interval': int(ICP.get_param('pos_advanced_analytics.refresh_interval', '60')),
                'default_branch_id': ICP.get_param('pos_advanced_analytics.default_branch_id', ''),
                'enable_targets': ICP.get_param('pos_advanced_analytics.enable_targets', 'True') == 'True',
                'enable_waiter': ICP.get_param('pos_advanced_analytics.enable_waiter', 'True') == 'True',
                'enable_cashier': ICP.get_param('pos_advanced_analytics.enable_cashier', 'True') == 'True',
                'timezone': ICP.get_param('pos_advanced_analytics.timezone', 'Africa/Addis_Ababa'),
            }
            return {'status': 'ok', 'data': settings}
        except Exception as e:
            _logger.exception('Settings error: %s', e)
            return {'status': 'error', 'message': str(e)}
