/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * POS Analytics RPC service.
 * Wraps all backend calls so the dashboard component stays clean.
 */
export class PosAnalyticsService {
    constructor(orm) {
        this.orm = orm;
    }

    /**
     * Fetch the full dashboard data payload.
     * @param {Object} filters
     * @returns {Promise<Object>}
     */
    async getDashboardData(filters = {}) {
        return this.orm.call(
            "pos.analytics.service",
            "get_dashboard_data",
            [filters],
        );
    }

    /**
     * Fetch dropdown options for filter bars.
     * @returns {Promise<Object>}
     */
    async getFilterOptions() {
        return this.orm.call(
            "pos.analytics.service",
            "get_filter_options",
            [],
        );
    }

    /**
     * Fetch analytics settings from ir.config_parameter.
     * @returns {Promise<Object>}
     */
    async getSettings() {
        return this.orm.call(
            "pos.analytics.service",
            "get_dashboard_data",   // reuse with empty filters just to validate access
            [{}],
        ).then(() =>
            fetch("/pos_analytics/settings", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: {} }),
            })
            .then((r) => r.json())
            .then((r) => r.result?.data || {})
        ).catch(() => ({}));
    }
}

registry.category("services").add("pos_analytics_service", {
    dependencies: ["orm"],
    start(env, { orm }) {
        return new PosAnalyticsService(orm);
    },
});
