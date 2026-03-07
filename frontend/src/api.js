/**
 * API Client für das DNS Manager Backend.
 * Verwaltet JWT-Token-Handling und alle API-Aufrufe.
 */
const API_BASE = '/api/v1';

class APIClient {
    constructor() {
        this.token = localStorage.getItem('token');
    }

    setToken(token) {
        this.token = token;
        if (token) {
            localStorage.setItem('token', token);
        } else {
            localStorage.removeItem('token');
        }
    }

    getToken() {
        return this.token;
    }

    isLoggedIn() {
        return !!this.token;
    }

    logout() {
        this.setToken(null);
        localStorage.removeItem('user');
        window.location.href = '/login';
    }

    async request(method, path, data = null) {
        const headers = { 'Content-Type': 'application/json' };
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }

        const opts = { method, headers };
        if (data && method !== 'GET') {
            opts.body = JSON.stringify(data);
        }

        const res = await fetch(`${API_BASE}${path}`, opts);

        if (res.status === 401) {
            this.logout();
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
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Login fehlgeschlagen');
        }

        const data = await res.json();
        this.setToken(data.access_token);
        localStorage.setItem('user', JSON.stringify(data.user));
        return data;
    }

    getMe() { return this.request('GET', '/auth/me'); }
    updateProfile(data) { return this.request('PUT', '/auth/me', data); }
    changePassword(data) { return this.request('PUT', '/auth/me/password', data); }
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
    deleteRecord(server, zone, data) { return this.request('DELETE', `/records/${server}/${zone}/delete`, data); }

    // ========== DNSSEC ==========
    listKeys(server, zone) { return this.request('GET', `/dnssec/${server}/${zone}/keys`); }
    enableDNSSEC(server, zone, data) { return this.request('POST', `/dnssec/${server}/${zone}/enable`, data); }
    disableDNSSEC(server, zone) { return this.request('POST', `/dnssec/${server}/${zone}/disable`); }

    // ========== Search ==========
    search(server, q) { return this.request('GET', `/search/${server}?q=${encodeURIComponent(q)}`); }

    // ========== Audit Log ==========
    getAuditLog(limit = 100) { return this.request('GET', `/audit-log?limit=${limit}`); }

    // ========== Settings / Server Config ==========
    getServerConfigs() { return this.request('GET', '/settings/servers'); }
    addServerConfig(data) { return this.request('POST', '/settings/servers', data); }
    updateServerConfig(id, data) { return this.request('PUT', `/settings/servers/${id}`, data); }
    deleteServerConfig(id) { return this.request('DELETE', `/settings/servers/${id}`); }
    testConnection(data) { return this.request('POST', '/settings/servers/test', data); }
}
const api = new APIClient();
export default api;
