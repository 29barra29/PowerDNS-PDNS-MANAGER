// SSO (OIDC/LDAP): Login-Seite, Admin-Einstellungen, Konto-Verknuepfung, Umwandeln (F10 §6.1, WS-F10-APP-FE).
// Endpunkte: routers/sso.py und routers/settings_sso.py (WS-F10-APP-BE), convert-to-local in routers/auth.py.
//
// Dateiname = Schluessel in ALLOWED_OVERRIDES (frontend/tests/api-modules.test.mjs, Entscheidung des Orchestrators
// nach Welle 1; der Plan nennt api/sso.js). Ueberschreibungen laut Plan B.14:
//   completeLogin2fa – Cookie-Variante nach SSO (Pending-Token im HttpOnly-Cookie pdnsmgr_2fa, Body ohne Token)
//   totpDisable      – externe Konten schalten 2FA nur mit dem Code ab (Body ohne password)
// Step-up [S8]: Methoden mit `stepUp`-Parameter senden { current_password, totp_code } als Feld `step_up`
// (Schema SsoStepUpIn bzw. StepUpBody); Aufrufer nutzen components/sso/stepUpStore.withStepUp.

export const overrides = ['completeLogin2fa', 'totpDisable']

const seg = (v) => encodeURIComponent(String(v))

function stepUpField(stepUp) {
    if (!stepUp || typeof stepUp !== 'object') return {}
    const out = {}
    if (stepUp.current_password) out.current_password = String(stepUp.current_password)
    if (stepUp.totp_code) out.totp_code = String(stepUp.totp_code).replace(/\s/g, '')
    return Object.keys(out).length ? { step_up: out } : {}
}

export default {
    // Oeffentlich (Login-Seite): { local_login_enabled, providers[], ldap: { enabled, label }, linking: { oidc, ldap } }
    getSsoProviders({ signal } = {}) {
        return this.request('GET', '/auth/sso/providers', null, { signal, authRedirect: false })
    },

    // Zweiter Schritt der Anmeldung. Ohne twoFactorToken (nach OIDC) liegt das Pending-Token im HttpOnly-Cookie.
    async completeLogin2fa(twoFactorToken, totpCode) {
        const body = { totp_code: String(totpCode || '').replace(/\s/g, '') }
        if (twoFactorToken) body.two_factor_token = twoFactorToken
        const res = await this.requestRaw('POST', '/auth/login/2fa', {
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            authRedirect: false,
            fallback: 'apiErrors.twoFactorFailed',
        })
        const data = await res.json().catch(() => null)
        this.setUser(data?.user ?? null)
        return data
    },

    // 2FA abschalten: lokale Konten mit Passwort, externe nur mit Code.
    totpDisable(password, code) {
        return this.request('POST', '/auth/me/totp/disable', { ...(password ? { password } : {}), code })
    },

    // ===== Admin: Einstellungen (nur Browser-Session) =====
    getSsoSettings({ signal } = {}) {
        return this.request('GET', '/settings/sso', null, { signal })
    },

    // data = { general?, oidc?, ldap? } -> { message, settings, warnings }; 403 stepup_required|reauth_required
    updateSsoSettings(data = {}, stepUp = null) {
        return this.request('PUT', '/settings/sso', { ...data, ...stepUpField(stepUp) })
    },

    // data = { target: 'oidc'|'ldap', oidc?|ldap?, test_username?, test_password? } -> { success, message, error, warnings, details }
    testSsoSettings(data) {
        return this.request('POST', '/settings/sso/test', data)
    },

    // ===== Eigenes Konto verknuepfen =====
    // { current_password, totp_code? } -> { authorization_url } (+ State-Cookie)
    startOidcLink(data) {
        return this.request('POST', '/auth/me/sso/oidc/link', data)
    },

    // { current_password, totp_code?, ldap_username, ldap_password } -> { message, user } (+ neues Session-Cookie)
    async linkLdapAccount(data) {
        const res = await this.request('POST', '/auth/me/sso/ldap/link', data)
        if (res?.user) this.setUser(res.user)
        return res
    },

    // ===== Admin: externes Konto in lokales umwandeln =====
    // -> { message, username, new_password, hint, must_change_password }
    convertUserToLocal(id, data = { must_change_password: true }, stepUp = null) {
        return this.request('POST', `/auth/users/${seg(id)}/convert-to-local`, {
            must_change_password: data?.must_change_password !== false,
            ...stepUpField(stepUp),
        })
    },
}
