/**
 * Audit-Aktionen WS-F9F11-BE (backend/app/routers/dyndns.py, settings_dyndns.py, services/dyndns.py, services/ptr.py).
 * - DYNDNS_UPDATE steht bereits in base.actions.js (Gruppe records, Zonenverlauf).
 * - PTR_SYNC: resource_type "record", zone_name = Reverse-Zone (Zonenverlauf, ruecksetzbar wie Record-Aenderungen).
 * - DYNDNS_AUTH_FAILED: resource_type "dyndns"; DYNDNS_TOKEN_*: resource_type "dyndns_token" (ohne Zone).
 * - Einstellungen: resource_type "settings", resource_name "dyndns" bzw. "ptr".
 * Details nennen nie Token-Klartext oder Hash. Labels: `audit.actions.*`, `audit.resourceTypes.dyndns|dyndns_token`
 * im Fragment ws-f9f11-be.<lang>.json.
 */
export default [
    { action: 'PTR_SYNC', group: 'records' },
    { action: 'DYNDNS_AUTH_FAILED', group: 'auth', history: false },
    { action: 'DYNDNS_TOKEN_CREATE', group: 'auth', history: false },
    { action: 'DYNDNS_TOKEN_UPDATE', group: 'auth', history: false },
    { action: 'DYNDNS_TOKEN_ROTATE', group: 'auth', history: false },
    { action: 'DYNDNS_TOKEN_DELETE', group: 'auth', history: false },
    { action: 'DYNDNS_TOKEN_REVOKED', group: 'auth', history: false },
    { action: 'DYNDNS_SETTINGS_UPDATE', group: 'settings' },
    { action: 'PTR_SETTINGS_UPDATE', group: 'settings' },
]

export const resourceTypes = ['dyndns', 'dyndns_token']
