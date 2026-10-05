/**
 * Audit-Aktionen aus WS-F14-APP (Panel-Token-Verwaltung, F14 3.12). PANEL_TOKEN_CREATE/DELETE stehen in
 * base.actions.js. Labels: `audit.actions.<ACTION>` im Fragment f14-app.<lang>.json.
 * - PANEL_TOKEN_UPDATE: eigener Token geaendert (details.changed = {feld: {from, to}}).
 * - PANEL_TOKEN_ADMIN_REVOKE: Admin hat Tokens eines Benutzers widerrufen (details.target_user_id, count).
 */
export default [
    { action: 'PANEL_TOKEN_UPDATE', group: 'auth' },
    { action: 'PANEL_TOKEN_ADMIN_REVOKE', group: 'users' },
]
