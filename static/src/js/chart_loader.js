/** @odoo-module **/

/**
 * Chart.js availability guard.
 *
 * chart.umd.min.js is declared in the module assets and loaded by the Odoo
 * bundle BEFORE this file, so window.Chart is already set in normal operation.
 * The CDN fallback only fires if the bundle somehow fails to deliver the lib
 * (e.g. during standalone development outside Odoo's asset pipeline).
 */

const CHART_JS_CDN =
    "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js";

let _chartReadyPromise = null;

export function ensureChartJs() {
    if (_chartReadyPromise) return _chartReadyPromise;

    // Bundled lib already loaded — fast path.
    if (window.Chart) {
        _chartReadyPromise = Promise.resolve(window.Chart);
        return _chartReadyPromise;
    }

    // CDN fallback (development / missing bundle scenario).
    console.warn(
        "[POS Analytics] Chart.js not found in bundle — loading from CDN as fallback."
    );
    _chartReadyPromise = new Promise((resolve) => {
        const script = document.createElement("script");
        script.src = CHART_JS_CDN;
        script.crossOrigin = "anonymous";
        script.onload = () => resolve(window.Chart);
        script.onerror = () => {
            console.error(
                "[POS Analytics] Failed to load Chart.js from CDN. Charts will not render."
            );
            resolve(null);
        };
        document.head.appendChild(script);
    });

    return _chartReadyPromise;
}
