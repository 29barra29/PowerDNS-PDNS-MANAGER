// Panel-API-Tokens (F14 §6.2, Plan WS-F14-APP). Router backend/app/routers/panel_tokens.py.
// Alle Aufrufe verlangen eine Browser-Session; per API-Token antwortet das Backend mit 403.
//
// Dateiname = Schluessel in ALLOWED_OVERRIDES (frontend/tests/api-modules.test.mjs; Orchestrator-Entscheidung
// nach Welle 1: f14-app.js statt panelTokens.js). Ueberschreibungen laut Plan B.14:
// - listPanelTokens: neuer Name fuer das Laden der Liste (Kern: getPanelTokens, bleibt fuer Altaufrufer),
// - createPanelToken: Body mit Scope, Berechtigung, Ablauf und Admin-Freigabe normalisiert,
// - deletePanelToken: ID kodiert (= widerrufen, endgueltig).

export const overrides = ['listPanelTokens', 'createPanelToken', 'deletePanelToken']

const BASE = '/auth/me/panel-tokens'
const seg = (v) => encodeURIComponent(String(v))

function normalizeZones(zones) {
    if (zones === null || zones === undefined) return null
    return [...new Set((Array.isArray(zones) ? zones : [zones]).map((z) => String(z).trim()).filter(Boolean))].sort()
}

export default {
    // -> { tokens: PanelTokenOut[], max_tokens, max_expiry_days }
    listPanelTokens({ signal } = {}) {
        return this.request('GET', BASE, null, { signal })
    },

    // data: { name, scope_zones (null = alle Zonen), permission, expires_in_days (null = kein Ablauf), allow_admin }
    // -> 201 { token, plaintext_token, warning }
    createPanelToken(data = {}) {
        const days = data.expires_in_days
        const body = {
            name: String(data.name ?? '').trim(),
            scope_zones: normalizeZones(data.scope_zones),
            permission: data.permission === 'read' ? 'read' : 'manage',
            expires_in_days: days === null || days === undefined || days === '' ? null : Number(days),
            allow_admin: !!data.allow_admin,
        }
        return this.request('POST', BASE, body)
    },

    // data: nur geaenderte Felder (lib/panelTokens.buildUpdatePayload) -> { message, token }
    updatePanelToken(id, data = {}) {
        return this.request('PUT', `${BASE}/${seg(id)}`, data)
    },

    // -> { message }
    deletePanelToken(id) {
        return this.request('DELETE', `${BASE}/${seg(id)}`)
    },

    // Admin: Tokens eines anderen Benutzers -> { user_id, username, tokens }
    getUserPanelTokens(userId, { signal } = {}) {
        return this.request('GET', `/auth/users/${seg(userId)}/panel-tokens`, null, { signal })
    },

    // Admin: einen Token widerrufen -> { message }
    revokeUserPanelToken(userId, tokenId) {
        return this.request('DELETE', `/auth/users/${seg(userId)}/panel-tokens/${seg(tokenId)}`)
    },

    // Admin: alle Tokens widerrufen -> { message, revoked }
    revokeAllUserPanelTokens(userId) {
        return this.request('DELETE', `/auth/users/${seg(userId)}/panel-tokens`)
    },
}
