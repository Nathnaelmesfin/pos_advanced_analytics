/** @odoo-module **/

/**
 * Chart.js loader — fetches from CDN once and exposes window.Chart.
 * All chart render functions wait for this promise before drawing.
 */

const CHART_JS_CDN =
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js";

let _chartReadyPromise = null;

export function ensureChartJs() {
    if (_chartReadyPromise) return _chartReadyPromise;

    if (window.Chart) {
        _chartReadyPromise = Promise.resolve(window.Chart);
        return _chartReadyPromise;
    }

    _chartReadyPromise = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = CHART_JS_CDN;
        script.crossOrigin = "anonymous";
        script.onload = () => resolve(window.Chart);
        script.onerror = (e) => {
            console.error("[POS Analytics] Failed to load Chart.js from CDN:", e);
            // Resolve with null so dashboard still loads — charts just stay blank
            resolve(null);
        };
        document.head.appendChild(script);
    });

    return _chartReadyPromise;
}
