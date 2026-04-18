/**
 * API Client für das DNS Manager Backend.
 * Token wird nur per HttpOnly-Cookie gesetzt (nicht in localStorage – sicherer gegen XSS).
 */
const API_BASE = '/api/v1';

const FETCH_OPTS = { credentials: 'include' };

/**
 * Extrahiert eine lesbare Fehlermeldung aus einer beliebigen Backend-Antwort.
 * Behandelt:
 *  - FastAPI-Standard:    { detail: "..." }
 *  - PowerDNS-Wrapper:    { error: "PowerDNS API Error", server: "ns1", detail: "..." }
 *  - Pydantic-Validierung: { detail: [{loc:[...], msg:"..."}] }
 *  - Reine Strings:       "Fehlertext"
 *  - HTTP-Statustexte als Fallback.
 */
function extractErrorMessage(payload, statusText, status) {
    if (payload == null) return statusText || `HTTP ${status || ''}`.trim();
    if (typeof payload === 'string') return payload;

    const detail = payload.detail;
    // Pydantic-Validierungsfehler: Liste mit { loc, msg, type }
    if (Array.isArray(detail) && detail.length > 0) {
        return detail
            .map((d) => {
                if (typeof d === 'string') return d;
                if (d && typeof d === 'object') {
                    const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== 'body').join('.') : '';
                    const msg = d.msg || d.message || JSON.stringify(d);
                    return loc ? `${loc}: ${msg}` : msg;
                }
                return String(d);
            })
            .join(' · ');
    }
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (detail && typeof detail === 'object') {
        if (typeof detail.message === 'string') return detail.message;
    }

    // PowerDNS-Wrapper aus dem Backend (pdns_error_handler):
    //   { error: "PowerDNS API Error", server: "ns1", detail: "..." }
    if (payload.error && payload.detail) {
        const srv = payload.server ? `${payload.server}: ` : '';
        return `${srv}${typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail)}`;
    }
    if (typeof payload.error === 'string' && payload.error.trim()) return payload.error.trim();
    if (typeof payload.message === 'string' && payload.message.trim()) return payload.message.trim();

    return statusText || `HTTP ${status || ''}`.trim();
}

class APIClient {
    constructor() {
        this._userCache = null; // Nur im Speicher, nie in localStorage
    }

    isLoggedIn() {
        return this._userCache !== null;
    }

    getUser() {
        return this._userCache;
    }

    setUser(user) {
        this._userCache = user;
    }

    clearUser() {
        this._userCache = null;
    }

    async logout() {
        try {
            await fetch(`${API_BASE}/auth/logout`, { method: 'POST', ...FETCH_OPTS });
        } catch { /* ignore */ }
        this.clearUser();
        window.location.href = '/login';
    }

    async request(method, path, data = null) {
        const headers = { 'Content-Type': 'application/json' };
        const opts = { method, headers, ...FETCH_OPTS };
        if (data && method !== 'GET') {
            opts.body = JSON.stringify(data);
        }

        let res;
        try {
            res = await fetch(`${API_BASE}${path}`, opts);
        } catch (networkErr) {
            // Netzwerkfehler/CORS/Server offline – wirf eine sprechende Meldung
            throw new Error(`Server nicht erreichbar (${networkErr.message || networkErr})`, { cause: networkErr });
        }

        if (res.status === 401) {
            this.clearUser();
            // Setup/Login-Seiten nicht ungewollt verlassen
            if (!['/login', '/setup'].some((p) => window.location.pathname.startsWith(p))) {
                window.location.href = '/login';
            }
            throw new Error('Sitzung abgelaufen – bitte erneut anmelden');
        }

        // 204 No Content
        if (res.status === 204) return null;

        const ct = res.headers.get('content-type') || '';
        const isJson = ct.includes('application/json');
        const payload = isJson ? await res.json().catch(() => null) : await res.text().catch(() => null);

        if (!res.ok) {
            const msg = extractErrorMessage(payload, res.statusText, res.status);
            const err = new Error(msg);
            err.status = res.status;
            err.payload = payload;
            throw err;
        }

        return payload;
    }

    // ========== Auth ==========
    async login(username, password) {
        const form = new URLSearchParams();
        form.append('username', username);
        form.append('password', password);

        let res;
        try {
            res = await fetch(`${API_BASE}/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: form,
                ...FETCH_OPTS,
            });
        } catch (e) {
            throw new Error(`Server nicht erreichbar (${e.message || e})`, { cause: e });
        }

        const data = await res.json().catch(() => null);
        if (!res.ok) {
            throw new Error(extractErrorMessage(data, res.statusText, res.status) || 'Login fehlgeschlagen');
        }
        this.setUser(data.user);
        return data;
    }

    async _publicJson(path, body, fallbackMsg) {
        let res;
        try {
            res = await fetch(`${API_BASE}${path}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        } catch (e) {
            throw new Error(`Server nicht erreichbar (${e.message || e})`, { cause: e });
        }
        const data = await res.json().catch(() => null);
        if (!res.ok) {
            throw new Error(extractErrorMessage(data, res.statusText, res.status) || fallbackMsg);
        }
        return data;
    }

    getMe() { return this.request('GET', '/auth/me'); }
    updateProfile(data) { return this.request('PUT', '/auth/me', data); }
    changePassword(data) { return this.request('PUT', '/auth/me/password', data); }
    register(data) { return this._publicJson('/auth/register', data, 'Registrierung fehlgeschlagen'); }
    requestPasswordReset(data) { return this._publicJson('/auth/forgot-password', data, 'Anfrage fehlgeschlagen'); }
    resetPassword(data) { return this._publicJson('/auth/reset-password', data, 'Zurücksetzen fehlgeschlagen'); }
    listUsers() { return this.request('GET', '/auth/users'); }
    createUser(data) { return this.request('POST', '/auth/users', data); }
    updateUser(id, data) { return this.request('PUT', `/auth/users/${id}`, data); }
    deleteUser(id) { return this.request('DELETE', `/auth/users/${id}`); }
    resetUserPassword(id) { return this.request('PUT', `/auth/users/${id}/reset-password`); }
    updateUserZones(id, zones) { return this.request('PUT', `/auth/users/${id}/zones`, { zones }); }
    getUserZones(id) { return this.request('GET', `/auth/users/${id}/zones`); }

    // ========== Server ==========
    getServers() { return this.request('GET', '/servers'); }

    // ========== Zones ==========
    listZones(server) { return this.request('GET', `/zones/${server}`); }
    getZone(server, zone) { return this.request('GET', `/zones/${server}/${zone}`); }
    createZone(data) { return this.request('POST', '/zones', data); }
    deleteZone(server, zone) { return this.request('DELETE', `/zones/${server}/${zone}`); }
    importZone(data) { return this.request('POST', '/zones/import', data); }
    exportZone(server, zone) { return this.request('GET', `/zones/${server}/${zone}/export`); }

    // ========== Records ==========
    listRecords(server, zone) { return this.request('GET', `/records/${server}/${zone}`); }
    createRecord(server, zone, data) { return this.request('POST', `/records/${server}/${zone}`, data); }
    updateRecord(server, zone, data) { return this.request('PUT', `/records/${server}/${zone}`, data); }
    deleteRecord(server, zone, data) { return this.request('DELETE', `/records/${server}/${zone}/delete`, data); }

    // ========== DNSSEC ==========
    listKeys(server, zone) { return this.request('GET', `/dnssec/${server}/${zone}/keys`); }
    enableDNSSEC(server, zone, data) { return this.request('POST', `/dnssec/${server}/${zone}/enable`, data); }
    disableDNSSEC(server, zone) { return this.request('POST', `/dnssec/${server}/${zone}/disable`); }

    // ========== Search ==========
    search(server, q) { return this.request('GET', `/search/${server}?q=${encodeURIComponent(q)}`); }

    // ========== Audit Log ==========
    getAuditLog(limit = 100) { return this.request('GET', `/audit-log?limit=${limit}`); }

    // ========== Templates ==========
    getTemplates() { return this.request('GET', '/templates'); }
    createTemplate(data) { return this.request('POST', '/templates', data); }
    updateTemplate(id, data) { return this.request('PUT', `/templates/${id}`, data); }
    deleteTemplate(id) { return this.request('DELETE', `/templates/${id}`); }

    // ========== Settings / Server Config ==========
    getServerConfigs() { return this.request('GET', '/settings/servers'); }
    addServerConfig(data) { return this.request('POST', '/settings/servers', data); }
    updateServerConfig(id, data) { return this.request('PUT', `/settings/servers/${id}`, data); }
    deleteServerConfig(id) { return this.request('DELETE', `/settings/servers/${id}`); }
    testConnection(data) { return this.request('POST', '/settings/servers/test', data); }
    // Holt den vollen API-Key eines Servers nur auf Admin-Anforderung (auditiert).
    revealServerApiKey(id) { return this.request('GET', `/settings/servers/${id}/api-key`); }

    // ========== SMTP ==========
    getSmtpSettings() { return this.request('GET', '/settings/smtp'); }
    updateSmtpSettings(data) { return this.request('PUT', '/settings/smtp', data); }
    testSmtpConnection() { return this.request('POST', '/settings/smtp/test'); }
    sendTestEmail(data) { return this.request('POST', '/settings/smtp/test-email', data); }
    // ========== App Info ==========
    // Öffentliche, unkritische App-Daten (Name, Logo, Sprache).
    getAppInfo() { return this.request('GET', '/settings/app-info'); }
    // Admin-only: install_path, app_base_url etc.
    getAdminInfo() { return this.request('GET', '/settings/admin-info'); }
    updateAppInfo(data) { return this.request('PUT', '/settings/app-info', data); }
    async uploadAppLogo(file) {
        const form = new FormData();
        form.append('file', file);
        let res;
        try {
            res = await fetch(`${API_BASE}/settings/app-logo`, { method: 'POST', body: form, ...FETCH_OPTS });
        } catch (e) {
            throw new Error(`Server nicht erreichbar (${e.message || e})`, { cause: e });
        }
        const data = await res.json().catch(() => null);
        if (!res.ok) throw new Error(extractErrorMessage(data, res.statusText, res.status) || 'Logo-Upload fehlgeschlagen');
        return data;
    }
}
const api = new APIClient();
export default api;
