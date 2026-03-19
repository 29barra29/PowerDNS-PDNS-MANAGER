/**
 * API Client für das DNS Manager Backend.
 * Token wird nur per HttpOnly-Cookie gesetzt (nicht in localStorage – sicherer gegen XSS).
 */
const API_BASE = '/api/v1';

const FETCH_OPTS = { credentials: 'include' };

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
        } catch (_) { /* ignore */ }
        this.clearUser();
        window.location.href = '/login';
    }

    async request(method, path, data = null) {
        const headers = { 'Content-Type': 'application/json' };
        const opts = { method, headers, ...FETCH_OPTS };
        if (data && method !== 'GET') {
            opts.body = JSON.stringify(data);
        }

        const res = await fetch(`${API_BASE}${path}`, opts);

        if (res.status === 401) {
            this.clearUser();
            window.location.href = '/login';
            throw new Error('Sitzung abgelaufen – bitte erneut anmelden');
        }

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            throw new Error(err.detail || err.error || `HTTP ${res.status}`);
        }

        return res.json();
    }

    // ========== Auth ==========
    async login(username, password) {
        const form = new URLSearchParams();
        form.append('username', username);
        form.append('password', password);

        const res = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: form,
            ...FETCH_OPTS,
        });

        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || 'Login fehlgeschlagen');
        }

        const data = await res.json();
        this.setUser(data.user);
        return data;
    }

    getMe() { return this.request('GET', '/auth/me'); }
    updateProfile(data) { return this.request('PUT', '/auth/me', data); }
    changePassword(data) { return this.request('PUT', '/auth/me/password', data); }
    register(data) {
        return fetch(`${API_BASE}/auth/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        }).then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || 'Registrierung fehlgeschlagen');
            return data;
        });
    }
    requestPasswordReset(data) {
        return fetch(`${API_BASE}/auth/forgot-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        }).then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || 'Anfrage fehlgeschlagen');
            return data;
        });
    }
    resetPassword(data) {
        return fetch(`${API_BASE}/auth/reset-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        }).then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || 'Zurücksetzen fehlgeschlagen');
            return data;
        });
    }
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

    // ========== SMTP ==========
    getSmtpSettings() { return this.request('GET', '/settings/smtp'); }
    updateSmtpSettings(data) { return this.request('PUT', '/settings/smtp', data); }
    testSmtpConnection() { return this.request('POST', '/settings/smtp/test'); }
    sendTestEmail(data) { return this.request('POST', '/settings/smtp/test-email', data); }
    // ========== App Info ==========
    getAppInfo() { return this.request('GET', '/settings/app-info'); }
    updateAppInfo(data) { return this.request('PUT', '/settings/app-info', data); }
    uploadAppLogo(file) {
        const form = new FormData();
        form.append('file', file);
        return fetch(`${API_BASE}/settings/app-logo`, {
            method: 'POST',
            body: form,
            ...FETCH_OPTS,
        }).then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || 'Logo-Upload fehlgeschlagen');
            return data;
        });
    }
}
const api = new APIClient();
export default api;
