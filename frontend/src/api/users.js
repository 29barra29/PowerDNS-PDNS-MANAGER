// Benutzerverwaltung (WS-F2F3, F3 §6.1, Bauplan [S9]): Admin-Werkzeuge fuer Passwort, Reset-Link, 2FA/Passkeys
// und Zugangs-Widerruf. Alle Aufrufe brauchen eine Browser-Session (Backend: get_admin_session_user), ausser
// getUserAccessSummary (Admin, auch per Token mit allow_admin).
//
// Hinweis: Die Kernmethode `resetUserPassword(id)` aus api.js bleibt unveraendert (PUT ohne Body -> Backend-
// Defaults: Passwortwechsel erzwingen, nichts widerrufen). Mit Optionen ruft man `generateUserPassword(id, opts)`
// auf – eine Ueberschreibung der Kernmethode ist laut Bauplan B.14 fuer F2F3 nicht freigegeben.
export default {
    // Zufallspasswort (16 Zeichen, Einmalanzeige). opts: { must_change_password = true, revoke_all_access = false }
    generateUserPassword(id, opts = {}) {
        return this.request('PUT', `/auth/users/${encodeURIComponent(id)}/reset-password`, {
            must_change_password: true,
            revoke_all_access: false,
            ...opts,
        })
    },
    // Reset-Link per E-Mail (24 h gueltig, einmal verwendbar); das Passwort bleibt bis dahin unveraendert
    sendUserResetLink(id) {
        return this.request('POST', `/auth/users/${encodeURIComponent(id)}/send-reset-link`, {})
    },
    // 2FA (TOTP) des Nutzers zuruecksetzen -> { message, changed, user }
    resetUser2fa(id) {
        return this.request('POST', `/auth/users/${encodeURIComponent(id)}/reset-2fa`, {})
    },
    // Alle Passkeys des Nutzers entfernen -> { message, deleted }
    deleteUserPasskeys(id) {
        return this.request('DELETE', `/auth/users/${encodeURIComponent(id)}/webauthn-credentials`)
    },
    // Zaehler: { panel_tokens, dyndns_tokens, webhooks, pending_deliveries, passkeys, totp_enabled }
    getUserAccessSummary(id, { signal } = {}) {
        return this.request('GET', `/auth/users/${encodeURIComponent(id)}/access-summary`, null, { signal })
    },
    // "Alle Zugaenge widerrufen". opts: { panel_tokens, dyndns_tokens, webhooks, reset_2fa, remove_passkeys }
    revokeUserAccess(id, opts = {}) {
        return this.request('POST', `/auth/users/${encodeURIComponent(id)}/revoke-access`, {
            panel_tokens: true,
            dyndns_tokens: true,
            webhooks: true,
            reset_2fa: false,
            remove_passkeys: false,
            ...opts,
        })
    },
}
