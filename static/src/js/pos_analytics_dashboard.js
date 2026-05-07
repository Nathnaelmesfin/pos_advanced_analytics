/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

// ─── tiny Chart.js wrapper ────────────────────────────────────────────────────
// We render charts via Chart.js loaded from CDN (standard in many Odoo installs)
// or fall back to a plain SVG bar if not available.

function renderBarChart(canvasEl, labels, datasets, options = {}) {
    if (!window.Chart) return;
    if (canvasEl._chartInstance) {
        canvasEl._chartInstance.destroy();
    }
    canvasEl._chartInstance = new window.Chart(canvasEl, {
        type: "bar",
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: "top" } },
            scales: { y: { beginAtZero: true } },
            ...options,
        },
    });
}

function renderLineChart(canvasEl, labels, datasets, options = {}) {
    if (!window.Chart) return;
    if (canvasEl._chartInstance) {
        canvasEl._chartInstance.destroy();
    }
    canvasEl._chartInstance = new window.Chart(canvasEl, {
        type: "line",
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: "top" } },
            scales: { y: { beginAtZero: true } },
            tension: 0.3,
            fill: false,
            ...options,
        },
    });
}

function renderDoughnutChart(canvasEl, labels, data, backgroundColors) {
    if (!window.Chart) return;
    if (canvasEl._chartInstance) {
        canvasEl._chartInstance.destroy();
    }
    canvasEl._chartInstance = new window.Chart(canvasEl, {
        type: "doughnut",
        data: {
            labels,
            datasets: [{ data, backgroundColor: backgroundColors, hoverOffset: 4 }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: "right" } },
        },
    });
}

// ─── Color palettes ───────────────────────────────────────────────────────────
const BLUE_PALETTE = [
    "#1F3864","#2E75B6","#4472C4","#5B9BD5","#9DC3E6","#BDD7EE",
    "#70AD47","#FFC000","#ED7D31","#FF0000","#7030A0","#00B0F0",
];

// ─── Main Dashboard Component ─────────────────────────────────────────────────

export class PosAnalyticsDashboard extends Component {
    static template = "pos_advanced_analytics.PosAnalyticsDashboard";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.actionService = useService("action");

        this.state = useState({
            loading: true,
            error: null,
            data: null,
            filterOptions: null,

            // Filters
            period: "today",
            date_start: "",
            date_end: "",
            pos_config_ids: [],
            cashier_ids: [],
            waiter_ids: [],
            product_category_ids: [],
            product_ids: [],
            payment_method_ids: [],
            report_basis: "sales_incl_tax",

            // UI state
            activeTab: "overview",
            showCustomDates: false,
            lastRefresh: null,
            settings: {
                refresh_interval: 60,
                enable_targets: true,
                enable_waiter: true,
                enable_cashier: true,
            },
        });

        // Canvas refs
        this.trendChartRef = useRef("trendChart");
        this.hourChartRef = useRef("hourChart");
        this.dayChartRef = useRef("dayChart");
        this.productChartRef = useRef("productChart");
        this.categoryChartRef = useRef("categoryChart");
        this.paymentChartRef = useRef("paymentChart");
        this.branchChartRef = useRef("branchChart");

        this._refreshTimer = null;

        onMounted(async () => {
            await this._loadFilterOptions();
            await this._loadSettings();
            await this._loadData();
            this._startAutoRefresh();
        });

        onWillUnmount(() => {
            this._stopAutoRefresh();
            this._destroyAllCharts();
        });
    }

    // ── Data loading ──────────────────────────────────────────────────────────

    async _loadFilterOptions() {
        try {
            const opts = await this.orm.call("pos.analytics.service", "get_filter_options", []);
            this.state.filterOptions = opts;
        } catch (e) {
            console.error("Filter options error", e);
        }
    }

    async _loadSettings() {
        try {
            const ICP = await this.orm.call("ir.config_parameter", "get_param", [
                "pos_advanced_analytics.refresh_interval", "60",
            ]);
            const enableTargets = await this.orm.call("ir.config_parameter", "get_param", [
                "pos_advanced_analytics.enable_targets", "True",
            ]);
            const enableWaiter = await this.orm.call("ir.config_parameter", "get_param", [
                "pos_advanced_analytics.enable_waiter", "True",
            ]);
            const enableCashier = await this.orm.call("ir.config_parameter", "get_param", [
                "pos_advanced_analytics.enable_cashier", "True",
            ]);
            const defaultPeriod = await this.orm.call("ir.config_parameter", "get_param", [
                "pos_advanced_analytics.default_period", "today",
            ]);
            this.state.settings = {
                refresh_interval: parseInt(ICP || "60", 10),
                enable_targets: enableTargets !== "False",
                enable_waiter: enableWaiter !== "False",
                enable_cashier: enableCashier !== "False",
            };
            if (!this.state.period || this.state.period === "today") {
                this.state.period = defaultPeriod || "today";
            }
        } catch (e) {
            // non-critical
        }
    }

    async _loadData() {
        this.state.loading = true;
        this.state.error = null;
        try {
            const filters = this._buildFilters();
            const data = await this.orm.call("pos.analytics.service", "get_dashboard_data", [filters]);
            this.state.data = data;
            this.state.lastRefresh = new Date().toLocaleTimeString();
            // Render charts after DOM update
            setTimeout(() => this._renderAllCharts(), 100);
        } catch (e) {
            this.state.error = e.message || String(e);
            this.notification.add(_t("Analytics Error: ") + this.state.error, { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    _buildFilters() {
        return {
            period: this.state.period,
            date_start: this.state.date_start,
            date_end: this.state.date_end,
            pos_config_ids: this.state.pos_config_ids,
            cashier_ids: this.state.cashier_ids,
            waiter_ids: this.state.waiter_ids,
            product_category_ids: this.state.product_category_ids,
            product_ids: this.state.product_ids,
            payment_method_ids: this.state.payment_method_ids,
            report_basis: this.state.report_basis,
        };
    }

    // ── Auto-refresh ──────────────────────────────────────────────────────────

    _startAutoRefresh() {
        this._stopAutoRefresh();
        const interval = this.state.settings.refresh_interval;
        if (interval > 0) {
            this._refreshTimer = setInterval(() => this._loadData(), interval * 1000);
        }
    }

    _stopAutoRefresh() {
        if (this._refreshTimer) {
            clearInterval(this._refreshTimer);
            this._refreshTimer = null;
        }
    }

    // ── Chart rendering ───────────────────────────────────────────────────────

    _destroyAllCharts() {
        [
            this.trendChartRef,
            this.hourChartRef,
            this.dayChartRef,
            this.productChartRef,
            this.categoryChartRef,
            this.paymentChartRef,
            this.branchChartRef,
        ].forEach((ref) => {
            if (ref.el && ref.el._chartInstance) {
                ref.el._chartInstance.destroy();
                ref.el._chartInstance = null;
            }
        });
    }

    _renderAllCharts() {
        if (!this.state.data) return;
        this._renderTrendChart();
        this._renderHourChart();
        this._renderDayChart();
        this._renderProductChart();
        this._renderCategoryChart();
        this._renderPaymentChart();
        this._renderBranchChart();
    }

    _renderTrendChart() {
        const el = this.trendChartRef.el;
        if (!el) return;
        const trend = this.state.data.sales_trend || [];
        renderLineChart(
            el,
            trend.map((d) => d.label),
            [
                {
                    label: _t("Total Sales"),
                    data: trend.map((d) => d.total_sales),
                    borderColor: "#2E75B6",
                    backgroundColor: "rgba(46,117,182,0.15)",
                    fill: true,
                },
                {
                    label: _t("Net Sales"),
                    data: trend.map((d) => d.net_sales),
                    borderColor: "#70AD47",
                    backgroundColor: "rgba(112,173,71,0.10)",
                    fill: false,
                },
            ],
        );
    }

    _renderHourChart() {
        const el = this.hourChartRef.el;
        if (!el) return;
        const hours = this.state.data.peak_hours || [];
        renderBarChart(
            el,
            hours.map((h) => h.label),
            [
                {
                    label: _t("Sales"),
                    data: hours.map((h) => h.total_sales),
                    backgroundColor: "#2E75B6",
                    yAxisID: "y",
                },
                {
                    label: _t("Orders"),
                    data: hours.map((h) => h.order_count),
                    backgroundColor: "#FFC000",
                    yAxisID: "y1",
                    type: "line",
                },
            ],
            {
                scales: {
                    y: { beginAtZero: true, position: "left" },
                    y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false } },
                },
            },
        );
    }

    _renderDayChart() {
        const el = this.dayChartRef.el;
        if (!el) return;
        const days = this.state.data.peak_days || [];
        renderBarChart(
            el,
            days.map((d) => d.day_name),
            [
                {
                    label: _t("Sales"),
                    data: days.map((d) => d.total_sales),
                    backgroundColor: "#4472C4",
                },
                {
                    label: _t("Orders"),
                    data: days.map((d) => d.order_count),
                    backgroundColor: "#ED7D31",
                    type: "line",
                    yAxisID: "y1",
                },
            ],
            {
                scales: {
                    y: { beginAtZero: true, position: "left" },
                    y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false } },
                },
            },
        );
    }

    _renderProductChart() {
        const el = this.productChartRef.el;
        if (!el) return;
        const products = (this.state.data.top_products || []).slice(0, 10);
        renderBarChart(
            el,
            products.map((p) => p.product_name),
            [
                {
                    label: _t("Gross Sales"),
                    data: products.map((p) => p.gross_sales),
                    backgroundColor: "#2E75B6",
                },
                {
                    label: _t("Net Sales"),
                    data: products.map((p) => p.net_sales),
                    backgroundColor: "#70AD47",
                },
            ],
            { indexAxis: "y" },
        );
    }

    _renderCategoryChart() {
        const el = this.categoryChartRef.el;
        if (!el) return;
        const cats = this.state.data.top_categories || [];
        renderDoughnutChart(
            el,
            cats.map((c) => c.categ_name),
            cats.map((c) => c.gross_sales),
            BLUE_PALETTE.slice(0, cats.length),
        );
    }

    _renderPaymentChart() {
        const el = this.paymentChartRef.el;
        if (!el) return;
        const pmts = this.state.data.payment_methods || [];
        renderDoughnutChart(
            el,
            pmts.map((p) => p.method_name),
            pmts.map((p) => p.total_amount),
            BLUE_PALETTE.slice(0, pmts.length),
        );
    }

    _renderBranchChart() {
        const el = this.branchChartRef.el;
        if (!el) return;
        const branches = this.state.data.branch_comparison || [];
        if (branches.length < 2) return;
        renderBarChart(
            el,
            branches.map((b) => b.branch_name),
            [
                {
                    label: _t("Total Sales"),
                    data: branches.map((b) => b.total_sales),
                    backgroundColor: "#1F3864",
                },
                {
                    label: _t("Net Sales"),
                    data: branches.map((b) => b.net_sales),
                    backgroundColor: "#2E75B6",
                },
            ],
        );
    }

    // ── Event handlers ────────────────────────────────────────────────────────

    onPeriodChange(ev) {
        this.state.period = ev.target.value;
        this.state.showCustomDates = this.state.period === "custom";
        if (this.state.period !== "custom") {
            this._loadData();
        }
    }

    onDateStartChange(ev) {
        this.state.date_start = ev.target.value;
    }

    onDateEndChange(ev) {
        this.state.date_end = ev.target.value;
    }

    onApplyCustomDates() {
        if (this.state.date_start && this.state.date_end) {
            this._loadData();
        } else {
            this.notification.add(_t("Please select both start and end dates."), { type: "warning" });
        }
    }

    onBranchChange(ev) {
        const selected = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this.state.pos_config_ids = selected;
        this._loadData();
    }

    onCashierChange(ev) {
        const selected = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this.state.cashier_ids = selected;
        this._loadData();
    }

    onWaiterChange(ev) {
        const selected = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this.state.waiter_ids = selected;
        this._loadData();
    }

    onCategoryChange(ev) {
        const selected = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this.state.product_category_ids = selected;
        this._loadData();
    }

    onPaymentMethodChange(ev) {
        const selected = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this.state.payment_method_ids = selected;
        this._loadData();
    }

    onReportBasisChange(ev) {
        this.state.report_basis = ev.target.value;
        this._loadData();
    }

    onTabChange(tab) {
        this.state.activeTab = tab;
        setTimeout(() => this._renderAllCharts(), 50);
    }

    onRefresh() {
        this._loadData();
    }

    onOpenReportWizard() {
        this.actionService.doAction("pos_advanced_analytics.action_pos_analytics_report_wizard");
    }

    // ── Formatting helpers ────────────────────────────────────────────────────

    fmt(value) {
        const currency = this.state.filterOptions?.currency_symbol || "";
        return `${currency} ${this._numFmt(value)}`;
    }

    _numFmt(value) {
        const n = parseFloat(value) || 0;
        return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    numFmt(value) {
        return this._numFmt(value);
    }

    pctFmt(value) {
        const n = parseFloat(value) || 0;
        return n.toFixed(1) + "%";
    }

    peakHourLabel(h) {
        if (h === null || h === undefined) return "—";
        return `${String(h).padStart(2, "0")}:00`;
    }

    get kpis() {
        return this.state.data?.kpis || {};
    }

    get salesTrend() {
        return this.state.data?.sales_trend || [];
    }

    get topProducts() {
        return this.state.data?.top_products || [];
    }

    get topCategories() {
        return this.state.data?.top_categories || [];
    }

    get waiterPerf() {
        return this.state.data?.waiter_performance || [];
    }

    get cashierPerf() {
        return this.state.data?.cashier_performance || [];
    }

    get peakHours() {
        return this.state.data?.peak_hours || [];
    }

    get peakDays() {
        return this.state.data?.peak_days || [];
    }

    get paymentMethods() {
        return this.state.data?.payment_methods || [];
    }

    get branchComparison() {
        return this.state.data?.branch_comparison || [];
    }

    get refundDiscountSummary() {
        return this.state.data?.refund_discount_summary || {};
    }

    get targetSummary() {
        return this.state.data?.target_summary || [];
    }

    get posConfigs() {
        return this.state.filterOptions?.pos_configs || [];
    }

    get cashiers() {
        return this.state.filterOptions?.cashiers || [];
    }

    get waiters() {
        return this.state.filterOptions?.waiters || [];
    }

    get categories() {
        return this.state.filterOptions?.categories || [];
    }

    get paymentMethodOptions() {
        return this.state.filterOptions?.payment_methods || [];
    }
}

registry.category("actions").add("pos_analytics_dashboard", PosAnalyticsDashboard);
