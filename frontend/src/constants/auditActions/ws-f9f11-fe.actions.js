/**
 * Audit-Aktionen DynDNS und PTR-Pflege (F9/F11 §3.9; Backend WS-F9F11-BE: routers/dyndns.py,
 * routers/settings_dyndns.py, services/ptr.py). DYNDNS_UPDATE steht bereits in base.actions.js.
 * - PTR_SYNC ist zonenbezogen (zone_name = Reverse-Zone) und erscheint im Zonenverlauf.
 * - DYNDNS_AUTH_FAILED/TOKEN_* betreffen keine Zone (resource_type "dyndns" bzw. "dyndns_token").
 * Labels: `audit.actions.*`, `audit.resourceTypes.dyndns|dyndns_token` im Fragment ws-f9f11-fe.<lang>.json.
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
