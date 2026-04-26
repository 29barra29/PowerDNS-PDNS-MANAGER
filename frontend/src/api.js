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
    async login(username, password, captchaToken = null, totpCode = null) {
        const form = new URLSearchParams();
        form.append('username', username);
        form.append('password', password);
        // Captcha-Token als zusaetzliches Form-Field (Backend nimmt es per Form(...) entgegen).
        if (captchaToken) form.append('captcha_token', captchaToken);
        if (totpCode) form.append('totp_code', totpCode);

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
            const err = new Error(extractErrorMessage(data, res.statusText, res.status) || 'Login fehlgeschlagen');
            err.status = res.status;
            err.payload = data;
            throw err;
        }
        if (data.need_two_factor) {
            return { needTwoFactor: true, twoFactorToken: data.two_factor_token };
        }
        this.setUser(data.user);
        return data;
    }

    async completeLogin2fa(twoFactorToken, totpCode) {
        const data = await this._publicJson(
            '/auth/login/2fa',
            { two_factor_token: twoFactorToken, totp_code: String(totpCode || '').replace(/\s/g, '') },
            '2FA fehlgeschlagen',
        );
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
            const err = new Error(extractErrorMessage(data, res.statusText, res.status) || fallbackMsg);
            err.status = res.status;
            err.payload = data;
            throw err;
        }
        return data;
    }

    getMe() { return this.request('GET', '/auth/me'); }
    // 2FA
    getTotpStatus() { return this.request('GET', '/auth/me/totp/status'); }
    // Leerer JSON-Body: manche Setups stolpern bei POST mit Content-Type json aber ohne Body
    totpBegin() { return this.request('POST', '/auth/me/totp/begin', {}); }
    totpEnable(code) { return this.request('POST', '/auth/me/totp/enable', { code }); }
    totpDisable(password, code) { return this.request('POST', '/auth/me/totp/disable', { password, code }); }
    // Panel-API-Token
    getPanelTokens() { return this.request('GET', '/auth/me/panel-tokens'); }
    createPanelToken(data) { return this.request('POST', '/auth/me/panel-tokens', data); }
    deletePanelToken(id) { return this.request('DELETE', `/auth/me/panel-tokens/${id}`); }
    // Webhooks
    getWebhooks() { return this.request('GET', '/auth/me/webhooks'); }
    createWebhook(data) { return this.request('POST', '/auth/me/webhooks', data); }
    updateWebhook(id, data) { return this.request('PUT', `/auth/me/webhooks/${id}`, data); }
    deleteWebhook(id) { return this.request('DELETE', `/auth/me/webhooks/${id}`); }
    // Metriken (Admin)
    getAppMetrics() { return this.request('GET', '/metrics'); }
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
    updateUserZones(id, payload) {
        return this.request('PUT', `/auth/users/${id}/zones`, payload);
    }
    getUserZones(id) { return this.request('GET', `/auth/users/${id}/zones`); }

    // ========== Server ==========
    getServers() { return this.request('GET', '/servers'); }

    // ========== Zones ==========
    listZones(server) { return this.request('GET', `/zones/${server}`); }
    /** Volle Zone inkl. Metadaten (dnssec, rrsets, …) – Backend: GET …/zones/{server}/{zone}/detail */
    getZone(server, zone) { return this.request('GET', `/zones/${server}/${zone}/detail`); }
    createZone(data) { return this.request('POST', '/zones', data); }
    deleteZone(server, zone) { return this.request('DELETE', `/zones/${server}/${zone}`); }
    importZone(data) { return this.request('POST', '/zones/import', data); }
    previewZoneImport(data) { return this.request('POST', '/zones/import/preview', data); }
    exportZone(server, zone) { return this.request('GET', `/zones/${server}/${zone}/export`); }

    // ========== Records ==========
    listRecords(server, zone) { return this.request('GET', `/records/${server}/${zone}`); }
    createRecord(server, zone, data) { return this.request('POST', `/records/${server}/${zone}`, data); }
    updateRecord(server, zone, data) { return this.request('PUT', `/records/${server}/${zone}`, data); }
    deleteRecord(server, zone, data) { return this.request('DELETE', `/records/${server}/${zone}/delete`, data); }

    // ========== DNSSEC ==========
    listKeys(server, zone) { return this.request('GET', `/dnssec/${server}/${zone}/keys`); }
    /** DS-RRs für Registrar (aus PowerDNS Cryptokeys) */
    getDsRecords(server, zone) { return this.request('GET', `/dnssec/${server}/${zone}/ds`); }
    enableDNSSEC(server, zone, data) { return this.request('POST', `/dnssec/${server}/${zone}/enable`, data); }
    disableDNSSEC(server, zone) { return this.request('POST', `/dnssec/${server}/${zone}/disable`); }

    // ========== Search ==========
    search(server, q) { return this.request('GET', `/search/${server}?q=${encodeURIComponent(q)}`); }

    // ========== Audit Log ==========
    getAuditLog(limit = 100) { return this.request('GET', `/audit-log?limit=${limit}`); }
    /** CSV-Download (Admin) – triggert Browser-Download, kein JSON */
    async downloadAuditLogCsv() {
        let res;
        try {
            res = await fetch(`${API_BASE}/audit-log/export`, { ...FETCH_OPTS, method: 'GET' });
        } catch (e) {
            throw new Error(`Server nicht erreichbar (${e.message || e})`, { cause: e });
        }
        if (res.status === 401) {
            this.clearUser();
            if (!window.location.pathname.startsWith('/login')) window.location.href = '/login';
            throw new Error('Sitzung abgelaufen – bitte erneut anmelden');
        }
        if (!res.ok) {
            const msg = (await res.text()) || res.statusText || `HTTP ${res.status}`;
            throw new Error(msg);
        }
        const blob = await res.blob();
        const name = 'audit-log.csv';
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        a.click();
        URL.revokeObjectURL(url);
    }

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

    // ========== Captcha ==========
    getCaptchaSettings() { return this.request('GET', '/settings/captcha'); }
    updateCaptchaSettings(data) { return this.request('PUT', '/settings/captcha', data); }
    testCaptcha(token) { return this.request('POST', '/settings/captcha/test', { token }); }

    // ========== Welcome Email ==========
    getWelcomeEmailSettings() { return this.request('GET', '/settings/welcome-email'); }
    updateWelcomeEmailSettings(data) { return this.request('PUT', '/settings/welcome-email', data); }
    sendWelcomeTestEmail(data) { return this.request('POST', '/settings/welcome-email/test', data); }

    // ========== ACME / Auto-TLS Tokens ==========
    // Liste der Tokens (ohne Plaintext - der existiert nur einmalig nach create).
    getAcmeTokens() { return this.request('GET', '/settings/acme/tokens'); }
    // Liefert { token, plaintext_token, warning } - plaintext_token nur einmal!
    createAcmeToken(data) { return this.request('POST', '/settings/acme/tokens', data); }
    deleteAcmeToken(id) { return this.request('DELETE', `/settings/acme/tokens/${id}`); }
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
