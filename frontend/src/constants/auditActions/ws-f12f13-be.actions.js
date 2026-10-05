/**
 * Audit-Aktionen WS-F12F13-BE (backend/app/routers/settings_monitoring.py): Einstellungen des
 * Propagations-Checks (F12) und der Prometheus-Metriken inkl. Scrape-Token (F13).
 * resource_type "settings", resource_name "propagation" bzw. "metrics"; nie mit Token-Klartext.
 * Labels: `audit.actions.*` im Fragment ws-f12f13-be.<lang>.json.
 */
export default [
    { action: 'PROPAGATION_SETTINGS_UPDATE', group: 'settings' },
    { action: 'METRICS_SETTINGS_UPDATE', group: 'settings' },
    { action: 'METRICS_TOKEN_CREATE', group: 'settings' },
    { action: 'METRICS_TOKEN_DELETE', group: 'settings' },
]
