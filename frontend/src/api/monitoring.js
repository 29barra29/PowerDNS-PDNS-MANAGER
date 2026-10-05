// API-Modul Monitoring (F13 §6.1, Plan B.14 / Regel 10 und [S14]; Workstream WS-F12F13-FE).
// Prometheus-Endpunkt /metrics (Schalter, Scrape-Token) und Kurzstatus fuer Admins. Alle Endpunkte verlangen
// eine Admin-Browser-Session; Panel-Tokens sind gesperrt. `getAppMetrics()` aus api.js bleibt unberuehrt.

export default {
    getMetricsSettings() {
        return this.request('GET', '/settings/metrics')
    },
    // data: { enabled?, pdns_probe? }
    updateMetricsSettings(data) {
        return this.request('PUT', '/settings/metrics', data)
    },
    // 201 { token, token_hint, token_created_at, warning } – der Klartext kommt nur in dieser Antwort
    createMetricsToken() {
        return this.request('POST', '/settings/metrics/token', {})
    },
    deleteMetricsToken() {
        return this.request('DELETE', '/settings/metrics/token')
    },
    // GET /settings/monitoring/status – Hintergrund-Aufgaben, Migrationsfehler, nicht geladene Server, Geheimnisse
    getMonitoringStatus() {
        return this.request('GET', '/settings/monitoring/status')
    },
}
