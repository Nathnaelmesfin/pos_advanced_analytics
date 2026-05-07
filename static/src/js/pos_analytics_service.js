/** @odoo-module **/

import { registry } from "@web/core/registry";

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
     * Fetch daily closing / Z-report data.
     * @param {Object} filters
     * @returns {Promise<Array>}
     */
    async getDailyClosingData(filters = {}) {
        return this.orm.call(
            "pos.analytics.service",
            "get_daily_closing_data",
            [filters],
        );
    }

    /**
     * Fetch analytics settings from ir.config_parameter via ORM.
     * Returns: refresh_interval, enable_targets, enable_waiter,
     *          enable_cashier, default_period, timezone.
     * @returns {Promise<Object>}
     */
    async getSettings() {
        try {
            const [refreshInterval, enableTargets, enableWaiter, enableCashier,
                   defaultPeriod, timezone] = await Promise.all([
                this.orm.call("ir.config_parameter", "get_param",
                    ["pos_advanced_analytics.refresh_interval", "60"]),
                this.orm.call("ir.config_parameter", "get_param",
                    ["pos_advanced_analytics.enable_targets", "True"]),
                this.orm.call("ir.config_parameter", "get_param",
                    ["pos_advanced_analytics.enable_waiter", "True"]),
                this.orm.call("ir.config_parameter", "get_param",
                    ["pos_advanced_analytics.enable_cashier", "True"]),
                this.orm.call("ir.config_parameter", "get_param",
                    ["pos_advanced_analytics.default_period", "today"]),
                this.orm.call("ir.config_parameter", "get_param",
                    ["pos_advanced_analytics.timezone", "Africa/Addis_Ababa"]),
            ]);
            return {
                refresh_interval: parseInt(refreshInterval || "60", 10),
                enable_targets:   enableTargets  !== "False",
                enable_waiter:    enableWaiter   !== "False",
                enable_cashier:   enableCashier  !== "False",
                default_period:   defaultPeriod  || "today",
                timezone:         timezone        || "Africa/Addis_Ababa",
            };
        } catch {
            return {};
        }
    }
}

registry.category("services").add("pos_analytics_service", {
    dependencies: ["orm"],
    start(env, { orm }) {
        return new PosAnalyticsService(orm);
    },
});
