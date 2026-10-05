// API-Modul DynDNS (F9 §3.5-3.7, Plan B.14 / Regel 10; Workstream WS-F9F11-FE).
// Wird von src/api.js per import.meta.glob in den API-Client gemischt (`this` = Client).
// Token-Verwaltung und Einstellungen laufen nur mit Browser-Session (Backend: get_session_user bzw.
// get_admin_session_user). Der Update-Endpunkt selbst (/nic/update, /api/v1/dyndns/update) wird nie vom
// Panel aufgerufen - nur von Routern/Skripten mit DynDNS-Token.

export default {
    // { enabled, base_url, allow_private_ips, ttl_min, ttl_max, ttl_default, max_hostnames, max_tokens,
    //   update_path, api_path, trust_proxy_headers? (nur Admin), proxy_warning? (nur Admin, Plan [S6]) }
    getDyndnsInfo({ signal } = {}) {
        return this.request('GET', '/dyndns/info', null, { signal })
    },
    // { zones: [{ name, servers }] } - Zonen mit Schreibrecht auf schreibbaren Servern
    getDyndnsZones({ signal } = {}) {
        return this.request('GET', '/dyndns/zones', null, { signal })
    },
    // { tokens: [...] } - eigene Tokens mit hostname_status
    getDyndnsTokens({ signal } = {}) {
        return this.request('GET', '/dyndns/tokens', null, { signal })
    },
    // -> { token, plaintext_token, warning }
    createDyndnsToken(data) {
        return this.request('POST', '/dyndns/tokens', data)
    },
    updateDyndnsToken(id, data) {
        return this.request('PUT', `/dyndns/tokens/${encodeURIComponent(id)}`, data)
    },
    // -> { token, plaintext_token, warning }
    rotateDyndnsToken(id) {
        return this.request('POST', `/dyndns/tokens/${encodeURIComponent(id)}/rotate`, {})
    },
    deleteDyndnsToken(id) {
        return this.request('DELETE', `/dyndns/tokens/${encodeURIComponent(id)}`)
    },
    // Admin: alle Tokens mit owner_username
    getAllDyndnsTokens({ signal } = {}) {
        return this.request('GET', '/dyndns/admin/tokens', null, { signal })
    },
    // Admin: { enabled, allow_private_ips, token_count, active_token_count }
    getDyndnsSettings({ signal } = {}) {
        return this.request('GET', '/settings/dyndns', null, { signal })
    },
    updateDyndnsSettings(data) {
        return this.request('PUT', '/settings/dyndns', data)
    },
}
