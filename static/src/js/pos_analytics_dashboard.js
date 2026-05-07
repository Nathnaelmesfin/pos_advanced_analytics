/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { ensureChartJs } from "./chart_loader";

// ─── Chart helpers ────────────────────────────────────────────────────────────

function renderBarChart(canvasEl, labels, datasets, options = {}) {
    if (!window.Chart) return;
    if (canvasEl._chartInstance) canvasEl._chartInstance.destroy();
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
    if (canvasEl._chartInstance) canvasEl._chartInstance.destroy();
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
    if (canvasEl._chartInstance) canvasEl._chartInstance.destroy();
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

const BLUE_PALETTE = [
    "#1F3864","#2E75B6","#4472C4","#5B9BD5","#9DC3E6","#BDD7EE",
    "#70AD47","#FFC000","#ED7D31","#FF0000","#7030A0","#00B0F0",
];

const PAGE_SIZE = 10;

// ─── Main Dashboard Component ─────────────────────────────────────────────────

export class PosAnalyticsDashboard extends Component {
    static template = "pos_advanced_analytics.PosAnalyticsDashboard";
    static props = {};

    setup() {
        this.posAnalytics = useService("pos_analytics_service");
        this.notification = useService("notification");
        this.actionService = useService("action");

        this.state = useState({
            loading: true,
            loadingClosing: false,
            error: null,
            data: null,
            filterOptions: null,
            dailyClosing: [],

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
            order_state: "all",
            group_by: "day",
            company_id: null,

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

            // Pagination
            productPage: PAGE_SIZE,
            categoryPage: PAGE_SIZE,
            waiterPage: PAGE_SIZE,
            cashierPage: PAGE_SIZE,
        });

        this.trendChartRef = useRef("trendChart");
        this.hourChartRef = useRef("hourChart");
        this.dayChartRef = useRef("dayChart");
        this.productChartRef = useRef("productChart");
        this.categoryChartRef = useRef("categoryChart");
        this.paymentChartRef = useRef("paymentChart");
        this.branchChartRef = useRef("branchChart");

        this._refreshTimer = null;

        onMounted(async () => {
            await ensureChartJs();
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
            const opts = await this.posAnalytics.getFilterOptions();
            this.state.filterOptions = opts;
            if (opts.companies && opts.companies.length > 0 && !this.state.company_id) {
                this.state.company_id = opts.companies[0].id;
            }
        } catch (e) {
            console.error("Filter options error", e);
        }
    }

    async _loadSettings() {
        try {
            const s = await this.posAnalytics.getSettings();
            const enableTargets = s.enable_targets ?? true;
            this.state.settings = {
                refresh_interval: s.refresh_interval ?? 60,
                enable_targets:   enableTargets,
                enable_waiter:    s.enable_waiter  ?? true,
                enable_cashier:   s.enable_cashier ?? true,
            };
            if (!this.state.period || this.state.period === "today") {
                this.state.period = s.default_period || "today";
            }
            // If the targets tab is active but targets are now disabled, fall
            // back to overview so the user doesn't see a blank tab panel.
            if (!enableTargets && this.state.activeTab === "targets") {
                this.state.activeTab = "overview";
            }
        } catch {
            // non-critical — defaults already set in state
        }
    }

    async _loadData() {
        this.state.loading = true;
        this.state.error = null;
        try {
            const filters = this._buildFilters();
            const data = await this.posAnalytics.getDashboardData(filters);
            this.state.data = data;
            this.state.lastRefresh = new Date().toLocaleTimeString();
            this.state.productPage = PAGE_SIZE;
            this.state.categoryPage = PAGE_SIZE;
            this.state.waiterPage = PAGE_SIZE;
            this.state.cashierPage = PAGE_SIZE;
            setTimeout(() => this._renderAllCharts(), 100);
        } catch (e) {
            this.state.error = e.message || String(e);
            this.notification.add(_t("Analytics Error: ") + this.state.error, { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    async _loadDailyClosing() {
        this.state.loadingClosing = true;
        try {
            const filters = this._buildFilters();
            const data = await this.posAnalytics.getDailyClosingData(filters);
            this.state.dailyClosing = data;
        } catch (e) {
            this.notification.add(_t("Daily closing error: ") + (e.message || String(e)), { type: "warning" });
            this.state.dailyClosing = [];
        } finally {
            this.state.loadingClosing = false;
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
            order_state: this.state.order_state,
            group_by: this.state.group_by,
            company_id: this.state.company_id,
            // Tell the backend to skip target SQL when targets are disabled.
            enable_targets: this.state.settings.enable_targets,
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
            this.trendChartRef, this.hourChartRef, this.dayChartRef,
            this.productChartRef, this.categoryChartRef, this.paymentChartRef, this.branchChartRef,
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
                { label: _t("Sales"), data: hours.map((h) => h.total_sales), backgroundColor: "#2E75B6", yAxisID: "y" },
                { label: _t("Orders"), data: hours.map((h) => h.order_count), backgroundColor: "#FFC000", yAxisID: "y1", type: "line" },
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
                { label: _t("Sales"), data: days.map((d) => d.total_sales), backgroundColor: "#4472C4" },
                { label: _t("Orders"), data: days.map((d) => d.order_count), backgroundColor: "#ED7D31", type: "line", yAxisID: "y1" },
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
                { label: _t("Gross Sales"), data: products.map((p) => p.gross_sales), backgroundColor: "#2E75B6" },
                { label: _t("Net Sales"), data: products.map((p) => p.net_sales), backgroundColor: "#70AD47" },
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
                { label: _t("Total Sales"), data: branches.map((b) => b.total_sales), backgroundColor: "#1F3864" },
                { label: _t("Net Sales"), data: branches.map((b) => b.net_sales), backgroundColor: "#2E75B6" },
            ],
        );
    }

    // ── Event handlers ────────────────────────────────────────────────────────

    onPeriodChange(ev) {
        this.state.period = ev.target.value;
        this.state.showCustomDates = this.state.period === "custom";
        if (this.state.period !== "custom") this._loadData();
    }

    onDateStartChange(ev) { this.state.date_start = ev.target.value; }
    onDateEndChange(ev) { this.state.date_end = ev.target.value; }

    onApplyCustomDates() {
        if (this.state.date_start && this.state.date_end) {
            this._loadData();
        } else {
            this.notification.add(_t("Please select both start and end dates."), { type: "warning" });
        }
    }

    onResetFilters() {
        this.state.period = "today";
        this.state.date_start = "";
        this.state.date_end = "";
        this.state.pos_config_ids = [];
        this.state.cashier_ids = [];
        this.state.waiter_ids = [];
        this.state.product_category_ids = [];
        this.state.product_ids = [];
        this.state.payment_method_ids = [];
        this.state.report_basis = "sales_incl_tax";
        this.state.order_state = "all";
        this.state.group_by = "day";
        this.state.showCustomDates = false;
        this._loadData();
    }

    onBranchChange(ev) {
        this.state.pos_config_ids = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this._loadData();
    }

    onCashierChange(ev) {
        this.state.cashier_ids = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this._loadData();
    }

    onWaiterChange(ev) {
        this.state.waiter_ids = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this._loadData();
    }

    onCategoryChange(ev) {
        this.state.product_category_ids = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this._loadData();
    }

    onPaymentMethodChange(ev) {
        this.state.payment_method_ids = Array.from(ev.target.selectedOptions).map((o) => parseInt(o.value, 10));
        this._loadData();
    }

    onReportBasisChange(ev) {
        this.state.report_basis = ev.target.value;
        this._loadData();
    }

    onOrderStateChange(ev) {
        this.state.order_state = ev.target.value;
        this._loadData();
    }

    onGroupByChange(ev) {
        this.state.group_by = ev.target.value;
        this._loadData();
    }

    onCompanyChange(ev) {
        const val = ev.target.value;
        this.state.company_id = val ? parseInt(val, 10) : null;
        this._loadData();
    }

    onTabChange(tab) {
        this.state.activeTab = tab;
        if (tab === "daily_closing") {
            this._loadDailyClosing();
        } else {
            setTimeout(() => this._renderAllCharts(), 50);
        }
    }

    onRefresh() { this._loadData(); }

    onOpenReportWizard() {
        this.actionService.doAction("pos_advanced_analytics.action_pos_analytics_report_wizard");
    }

    // ── Pagination ────────────────────────────────────────────────────────────

    onLoadMoreProducts() { this.state.productPage += PAGE_SIZE; }
    onLoadMoreCategories() { this.state.categoryPage += PAGE_SIZE; }
    onLoadMoreWaiters() { this.state.waiterPage += PAGE_SIZE; }
    onLoadMoreCashiers() { this.state.cashierPage += PAGE_SIZE; }

    // ── Formatting helpers ────────────────────────────────────────────────────

    fmt(value) {
        const currency = this.state.filterOptions?.currency_symbol || "";
        return `${currency} ${this._numFmt(value)}`;
    }

    _numFmt(value) {
        const n = parseFloat(value) || 0;
        return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    numFmt(value) { return this._numFmt(value); }

    pctFmt(value) { return (parseFloat(value) || 0).toFixed(1) + "%"; }

    peakHourLabel(h) {
        if (h === null || h === undefined) return "—";
        return `${String(h).padStart(2, "0")}:00`;
    }

    sessionStateLabel(state) {
        const map = { opening_control: "Opening", opened: "Open", closing_control: "Closing", closed: "Closed" };
        return map[state] || state || "—";
    }

    // ── Getters ───────────────────────────────────────────────────────────────

    get kpis() { return this.state.data?.kpis || {}; }
    get salesTrend() { return this.state.data?.sales_trend || []; }

    get topProducts() {
        return (this.state.data?.top_products || []).slice(0, this.state.productPage);
    }
    get topProductsTotal() { return (this.state.data?.top_products || []).length; }

    get topCategories() {
        return (this.state.data?.top_categories || []).slice(0, this.state.categoryPage);
    }
    get topCategoriesTotal() { return (this.state.data?.top_categories || []).length; }

    get waiterPerf() {
        return (this.state.data?.waiter_performance || []).slice(0, this.state.waiterPage);
    }
    get waiterPerfTotal() { return (this.state.data?.waiter_performance || []).length; }

    get cashierPerf() {
        return (this.state.data?.cashier_performance || []).slice(0, this.state.cashierPage);
    }
    get cashierPerfTotal() { return (this.state.data?.cashier_performance || []).length; }

    get peakHours() { return this.state.data?.peak_hours || []; }
    get peakDays() { return this.state.data?.peak_days || []; }
    get paymentMethods() { return this.state.data?.payment_methods || []; }
    get branchComparison() { return this.state.data?.branch_comparison || []; }
    get refundDiscountSummary() { return this.state.data?.refund_discount_summary || {}; }
    get targetSummary() { return this.state.data?.target_summary || []; }
    get posHrAvailable() { return this.state.data?.pos_hr_available !== false; }

    get posConfigs() { return this.state.filterOptions?.pos_configs || []; }
    get cashiers() { return this.state.filterOptions?.cashiers || []; }
    get waiters() { return this.state.filterOptions?.waiters || []; }
    get categories() { return this.state.filterOptions?.categories || []; }
    get paymentMethodOptions() { return this.state.filterOptions?.payment_methods || []; }
    get companies() { return this.state.filterOptions?.companies || []; }
}

registry.category("actions").add("pos_analytics_dashboard", PosAnalyticsDashboard);
