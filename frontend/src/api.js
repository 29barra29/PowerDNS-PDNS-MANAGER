/**
 * API-Client fuer das PDNS Manager Backend.
 * Token wird nur per HttpOnly-Cookie gesetzt (nicht in localStorage – sicherer gegen XSS).
 *
 * Erweiterung ab 3.0 (Plan B.14, Regel 10): Feature-Module liefern weitere Methoden als
 * `src/api/<ws>.js` mit `export default { methodName() { ... } }`; sie werden unten per
 * import.meta.glob in den Prototyp gemischt (`this` = Client). Vertrag: `src/api/README.md`.
 */
import i18n from './i18n';

const API_BASE = '/api/v1';

// Window-Event: Backend verlangt einen Passwortwechsel (403 + X-Password-Change-Required, F3).
export const PASSWORD_CHANGE_EVENT = 'pdns:password-change-required';
// Window-Event: Backend verlangt eine erneute Bestaetigung (Step-up, S8). detail = { code, method, path, message }.
export const STEP_UP_EVENT = 'pdns:step-up-required';
// 403-Codes, die STEP_UP_EVENT ausloesen (stepup_failed = falsches Passwort im Dialog -> kein neues Event).
export const STEP_UP_CODES = Object.freeze(['stepup_required', 'reauth_required']);

// 401-Ausnahmeliste: Auf diesen (oeffentlichen) SPA-Seiten fuehrt ein 401 nicht zum Sprung auf /login.
// Neue oeffentliche Routen (z. B. ein SSO-Callback) hier eintragen, sonst droht eine Redirect-Schleife.
export const AUTH_REDIRECT_EXEMPT_PATHS = Object.freeze([
    '/login',
    '/setup',
    '/register',
    '/forgot-password',
    '/reset-password',
]);

function isExemptPath(pathname) {
    return AUTH_REDIRECT_EXEMPT_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`) || pathname.startsWith(`${p}?`));
}

// i18n-Text; Schluessel ohne Uebersetzung (oder freier Text von Altaufrufern) werden unveraendert benutzt.
function tr(keyOrText, opts) {
    if (!keyOrText) return '';
    return i18n.exists(keyOrText) ? i18n.t(keyOrText, opts) : keyOrText;
}

function emit(name, detail) {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(detail === undefined ? new Event(name) : new CustomEvent(name, { detail }));
}

function isAbortError(err) {
    return err?.name === 'AbortError';
}

/**
 * Extrahiert eine lesbare Fehlermeldung aus einer beliebigen Backend-Antwort.
 * Behandelt:
 *  - FastAPI-Standard:     { detail: "..." }
 *  - Strukturiert:         { detail: { message: "...", code: "..." } }
 *  - PowerDNS-Wrapper:     { error: "PowerDNS API Error", server: "ns1", detail: "..." }
 *  - Pydantic-Validierung: { detail: [{loc:[...], msg:"..."}] }
 *  - Reine Strings:        "Fehlertext" (HTML-Fehlerseiten eines Proxys werden ignoriert)
 *  - Fallback: `fallback` (bereits uebersetzter Text) mit HTTP-Status, sonst Statustext bzw. "HTTP nnn".
 */
export function extractErrorMessage(payload, statusText, status, fallback) {
    const httpText = () => (statusText || `HTTP ${status || ''}`).trim();
    const none = () => (fallback ? (status ? `${fallback} (HTTP ${status})` : fallback) : httpText());
    if (payload == null) return none();
    if (typeof payload === 'string') {
        const s = payload.trim();
        if (!s || s.startsWith('<')) return none();
        return s.length > 500 ? `${s.slice(0, 500)}…` : s;
    }
    if (typeof payload !== 'object') return String(payload);

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
    // PowerDNS-Wrapper aus dem Backend (pdns_error_handler) – vor dem reinen detail-String pruefen,
    // sonst ginge der Servername verloren: { error: "PowerDNS API Error", server: "ns1", detail: "..." }
    if (payload.error && detail && !Array.isArray(detail)) {
        const srv = payload.server ? `${payload.server}: ` : '';
        const text = typeof detail === 'string' ? detail : (typeof detail.message === 'string' ? detail.message : JSON.stringify(detail));
        return `${srv}${text}`;
    }
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (detail && typeof detail === 'object' && typeof detail.message === 'string' && detail.message.trim()) {
        return detail.message.trim();
    }

    if (typeof payload.error === 'string' && payload.error.trim()) return payload.error.trim();
    if (typeof payload.message === 'string' && payload.message.trim()) return payload.message.trim();

    return none();
}

// Maschinenlesbarer Fehlercode einer Antwort (detail.code, code oder detail als reiner Code-String).
export function extractErrorCode(payload) {
    if (!payload || typeof payload !== 'object') return null;
    const d = payload.detail;
    if (d && typeof d === 'object' && !Array.isArray(d) && typeof d.code === 'string') return d.code;
    if (typeof payload.code === 'string') return payload.code;
    if (typeof d === 'string' && /^[a-z][a-z0-9_]*$/.test(d)) return d;
    return null;
}

// Antwort-Body lesen (JSON oder Text); Lesefehler -> null, Abbruch wird durchgereicht.
async function readPayload(res) {
    const ct = res.headers.get('content-type') || '';
    try {
        return ct.includes('application/json') ? await res.json() : await res.text();
    } catch (err) {
        if (isAbortError(err)) throw err;
        return null;
    }
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
            await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'include' });
        } catch { /* ignore */ }
        this.clearUser();
        window.location.href = '/login';
    }

    // ========== Transport (auch fuer API-Module) ==========

    /**
     * Rohaufruf: fetch mit Netzwerkfehler-Text, 401-Behandlung, 403-Hooks und Fehlerobjekt.
     * Liefert die Response nur, wenn sie ok ist (2xx); sonst wird geworfen.
     * Optionen: body, headers, signal, authRedirect (Default true), credentials (Default true),
     *           fallback (i18n-Key/Text, wenn die Antwort keine Meldung enthaelt).
     * Abbruch per AbortSignal: der AbortError wird unveraendert durchgereicht.
     */
    async requestRaw(method, path, { body, headers, signal, authRedirect = true, credentials = true, fallback } = {}) {
        const init = { method, headers: headers || {} };
        if (credentials) init.credentials = 'include';
        if (signal) init.signal = signal;
        if (body !== undefined && body !== null) init.body = body;

        let res;
        try {
            res = await fetch(`${API_BASE}${path}`, init);
        } catch (networkErr) {
            if (isAbortError(networkErr)) throw networkErr;
            // Netzwerkfehler/CORS/Server offline – sprechende Meldung
            const err = new Error(i18n.t('apiErrors.serverUnreachable', { message: networkErr?.message || String(networkErr) }), { cause: networkErr });
            err.status = 0;
            err.network = true;
            throw err;
        }

        if (res.status === 401 && authRedirect) this._unauthorized();
        if (!res.ok) {
            const payload = await readPayload(res);
            throw this._httpError(res, payload, { method, path, fallback });
        }
        return res;
    }

    /**
     * JSON-Aufruf (Kern): request(method, path, data = null, { signal, authRedirect }).
     * 204 -> null; JSON-Antworten als Objekt, sonst Text. Fehler: Error mit status, payload, code.
     */
    async request(method, path, data = null, { signal, authRedirect = true } = {}) {
        const res = await this.requestRaw(method, path, {
            headers: { 'Content-Type': 'application/json' },
            body: data && method !== 'GET' ? JSON.stringify(data) : undefined,
            signal,
            authRedirect,
        });
        if (res.status === 204) return null;
        return readPayload(res);
    }

    // 401: Sitzung weg -> Cache leeren, ausser auf oeffentlichen Seiten hart zur Anmeldung.
    _unauthorized() {
        this.clearUser();
        if (typeof window !== 'undefined' && !isExemptPath(window.location.pathname)) {
            window.location.href = '/login';
        }
        const err = new Error(i18n.t('apiErrors.sessionExpired'));
        err.status = 401;
        throw err;
    }

    // Fehlerobjekt bauen und 403-Hooks ausloesen (Passwortwechsel F3, Step-up S8).
    _httpError(res, payload, { method, path, fallback } = {}) {
        const code = extractErrorCode(payload) || res.headers.get('X-Step-Up-Required') || null;
        const message = extractErrorMessage(payload, res.statusText, res.status, fallback ? tr(fallback) : undefined);
        if (res.status === 403) {
            if (res.headers.get('X-Password-Change-Required')) {
                if (this._userCache) this._userCache = { ...this._userCache, must_change_password: true };
                emit(PASSWORD_CHANGE_EVENT);
            }
            if (code && STEP_UP_CODES.includes(code)) {
                emit(STEP_UP_EVENT, { code, method, path, message });
            }
        }
        const err = new Error(message);
        err.status = res.status;
        err.payload = payload;
        if (code) err.code = code;
        return err;
    }

    // Oeffentlicher JSON-POST (Login-Varianten, Registrierung, Passwort vergessen); kein 401-Sprung.
    // `fallback`: i18n-Key (apiErrors.*) oder Text, falls die Antwort keine Meldung enthaelt.
    async _publicJson(path, body, fallback) {
        const res = await this.requestRaw('POST', path, {
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            credentials: false,
            authRedirect: false,
            fallback: fallback || 'apiErrors.requestFailed',
        });
        return readPayload(res);
    }

    // ========== Auth ==========
    async login(username, password, captchaToken = null, totpCode = null) {
        const form = new URLSearchParams();
        form.append('username', username);
        form.append('password', password);
        // Captcha-Token als zusaetzliches Form-Field (Backend nimmt es per Form(...) entgegen).
        if (captchaToken) form.append('captcha_token', captchaToken);
        if (totpCode) form.append('totp_code', totpCode);

        const res = await this.requestRaw('POST', '/auth/login', {
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: form,
            authRedirect: false,
            fallback: 'apiErrors.loginFailed',
        });
        const data = await readPayload(res);
        if (data?.need_two_factor) {
            return { needTwoFactor: true, twoFactorToken: data.two_factor_token };
        }
        this.setUser(data?.user ?? null);
        return data;
    }

    async completeLogin2fa(twoFactorToken, totpCode) {
        const data = await this._publicJson(
            '/auth/login/2fa',
            { two_factor_token: twoFactorToken, totp_code: String(totpCode || '').replace(/\s/g, '') },
            'apiErrors.twoFactorFailed',
        );
        this.setUser(data.user);
        return data;
    }

    getMe() { return this.request('GET', '/auth/me'); }
    // 2FA
    getTotpStatus() { return this.request('GET', '/auth/me/totp/status'); }
    // Leerer JSON-Body: manche Setups stolpern bei POST mit Content-Type json aber ohne Body
    totpBegin() { return this.request('POST', '/auth/me/totp/begin', {}); }
    totpEnable(code) { return this.request('POST', '/auth/me/totp/enable', { code }); }
    totpDisable(password, code) { return this.request('POST', '/auth/me/totp/disable', { password, code }); }
    // WebAuthn / Passkeys – Verwaltung (eingeloggt)
    getPasskeys() { return this.request('GET', '/auth/me/webauthn/credentials'); }
    passkeyRegisterBegin() { return this.request('POST', '/auth/me/webauthn/register/begin', {}); }
    passkeyRegisterComplete(data) { return this.request('POST', '/auth/me/webauthn/register/complete', data); }
    deletePasskey(id) { return this.request('DELETE', `/auth/me/webauthn/credentials/${id}`); }
    // WebAuthn / Passkeys – Login (öffentlich, kein Cookie nötig)
    passkeyLoginBegin() { return this._publicJson('/auth/webauthn/login/begin', {}, 'apiErrors.passkeyLoginFailed'); }
    async passkeyLoginComplete(challengeToken, credential) {
        const data = await this._publicJson(
            '/auth/webauthn/login/complete',
            { challenge_token: challengeToken, credential },
            'apiErrors.passkeyLoginFailed',
        );
        this.setUser(data.user);
        return data;
    }
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
    register(data) { return this._publicJson('/auth/register', data, 'apiErrors.registrationFailed'); }
    requestPasswordReset(data) { return this._publicJson('/auth/forgot-password', data, 'apiErrors.requestFailed'); }
    resetPassword(data) { return this._publicJson('/auth/reset-password', data, 'apiErrors.resetFailed'); }
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
    // F8-A13: abbrechbar (signal), optional max_results
    search(server, q, { signal, maxResults } = {}) {
        const max = maxResults ? `&max_results=${encodeURIComponent(maxResults)}` : '';
        return this.request('GET', `/search/${encodeURIComponent(server)}?q=${encodeURIComponent(q)}${max}`, null, { signal });
    }

    // ========== Audit Log ==========
    getAuditLog(limit = 100) { return this.request('GET', `/audit-log?limit=${limit}`); }
    /** CSV-Download (Admin) – triggert Browser-Download, kein JSON */
    async downloadAuditLogCsv() {
        const res = await this.requestRaw('GET', '/audit-log/export');
        const blob = await res.blob();
        const name = 'audit-log.csv';
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
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
        const res = await this.requestRaw('POST', '/settings/app-logo', { body: form, fallback: 'apiErrors.logoUploadFailed' });
        return readPayload(res);
    }
}

// ========== API-Module (src/api/<ws>.js) ==========
// Sortiert nach Pfad gemischt; Namenskonflikte und nicht deklarierte Ueberschreibungen prueft
// frontend/tests/api-modules.test.mjs statisch, hier zusaetzlich eine Warnung im Dev-Server.
const apiModules = import.meta.glob('./api/*.js', { eager: true });
for (const file of Object.keys(apiModules).sort()) {
    const mod = apiModules[file];
    const methods = mod?.default;
    if (!methods || typeof methods !== 'object') {
        console.error(`API-Modul ${file}: kein "export default { ... }"`);
        continue;
    }
    if (import.meta.env?.DEV) {
        const declared = new Set(Array.isArray(mod.overrides) ? mod.overrides : []);
        for (const name of Object.keys(methods)) {
            if (name in APIClient.prototype && !declared.has(name)) {
                console.warn(`API-Modul ${file} ueberschreibt "${name}" ohne Eintrag in overrides`);
            }
        }
    }
    Object.assign(APIClient.prototype, methods);
}

const api = new APIClient();
export default api;
