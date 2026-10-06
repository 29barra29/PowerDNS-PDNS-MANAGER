// API-Modul Verschluesselung gespeicherter Geheimnisse (F5 §6.1, Plan B.14 / Regel 10; Workstream WS-F5-FE).
// Wird von src/api.js per import.meta.glob in den API-Client gemischt (`this` = Client).
// Backend: GET /settings/secrets/status (WS-F5-BE, routers/settings_secrets.py) nur fuer Admins mit
// Browser-Session (Panel-Token -> 403). Liefert nie Werte, nur Zaehler, Namen und Issue-Codes.
//
// Neben dem Default-Export stehen hier reine Auswertungshelfer fuer die Statusantwort (ohne React/i18n),
// die Statuskarte, Admin-Banner und Server-Tab gemeinsam nutzen.

export default {
    // { mode, fallback_reason, key_source, key_file, key_file_exists, key_fingerprint, decrypt_only_keys,
    //   health: ok|warning|error, issues: [code], columns: [{id, encrypted, encrypted_old, plaintext, unreadable, empty}],
    //   unreadable: [{kind, field, id, name, owner}], last_startup: {at, key_generated, migrated, rotated} | null }
    getSecretsStatus({ signal, authRedirect } = {}) {
        return this.request('GET', '/settings/secrets/status', null, { signal, authRedirect })
    },
    // SMTP-Verbindungstest mit noch nicht gespeicherten Formularwerten (F5-BE: optionaler Body von
    // POST /settings/smtp/test). Fehlende Felder = gespeicherter Wert; password null = gespeichertes Passwort
    // (nur erlaubt, wenn kein Zielfeld abweicht, sonst 400 code "secret_reentry_required").
    testSmtpSettings(data) {
        return this.request('POST', '/settings/smtp/test', data || {})
    },
}

// Feld-ID (Backend SECRET_COLUMNS / SECRET_SETTING_KEYS) -> i18n-Key der Feldbezeichnung.
// Unbekannte IDs zeigt die Karte roh an (neue Felder im Backend brechen die Anzeige nicht).
export const SECRET_FIELD_LABEL_KEYS = Object.freeze({
    'server_configs.api_key': 'settings.secrets.fieldServerApiKey',
    'webhooks.secret': 'settings.secrets.fieldWebhookSecret',
    'webhooks.url': 'settings.secrets.fieldWebhookUrl',
    'users.totp_secret': 'settings.secrets.fieldTotpSecret',
    'users.totp_pending_secret': 'settings.secrets.fieldTotpPending',
    'system_settings.smtp_password': 'settings.secrets.fieldSmtpPassword',
    'system_settings.captcha_secret_key': 'settings.secrets.fieldCaptchaSecret',
    'system_settings.oidc_client_secret': 'settings.secrets.fieldOidcClientSecret',
    'system_settings.ldap_bind_password': 'settings.secrets.fieldLdapBindPassword',
    'system_settings.metrics_token': 'settings.secrets.fieldMetricsToken',
})

// Setting-Feld -> Einstellungs-Tab, in dem der Wert neu eingetragen wird (null = kein Sprungziel).
const SETTING_FIELD_TABS = Object.freeze({
    'system_settings.smtp_password': 'smtp',
    'system_settings.captcha_secret_key': 'security',
    'system_settings.oidc_client_secret': 'sso',
    'system_settings.ldap_bind_password': 'sso',
    'system_settings.metrics_token': 'monitoring',
})

export function settingTabForField(field) {
    return SETTING_FIELD_TABS[field] || null
}

function count(n) {
    return Number.isFinite(n) && n > 0 ? n : 0
}

/** Summen ueber alle Felder: { encrypted (inkl. encrypted_old), encryptedOld, plaintext, unreadable, empty, stored }. */
export function secretsTotals(status) {
    const out = { encrypted: 0, encryptedOld: 0, plaintext: 0, unreadable: 0, empty: 0, stored: 0 }
    for (const c of Array.isArray(status?.columns) ? status.columns : []) {
        out.encrypted += count(c?.encrypted) + count(c?.encrypted_old)
        out.encryptedOld += count(c?.encrypted_old)
        out.plaintext += count(c?.plaintext)
        out.unreadable += count(c?.unreadable)
        out.empty += count(c?.empty)
    }
    out.stored = out.encrypted + out.plaintext + out.unreadable
    return out
}

/**
 * Zeilen fuer die Tabelle "Gespeicherte Geheimnisse": alle bekannten Felder in fester Reihenfolge (fehlende mit
 * Nullen, F5 §2 C.4 "Tabelle mit Nullen bleibt sichtbar"), danach unbekannte Felder aus der Antwort.
 */
export function secretColumnsForDisplay(status) {
    const zero = { encrypted: 0, encrypted_old: 0, plaintext: 0, unreadable: 0, empty: 0 }
    const byId = new Map()
    for (const c of Array.isArray(status?.columns) ? status.columns : []) {
        if (c && typeof c.id === 'string' && !byId.has(c.id)) byId.set(c.id, c)
    }
    const rows = Object.keys(SECRET_FIELD_LABEL_KEYS).map((id) => ({ ...zero, ...(byId.get(id) || {}), id }))
    for (const [id, c] of byId) {
        if (!Object.hasOwn(SECRET_FIELD_LABEL_KEYS, id)) rows.push({ ...zero, ...c, id })
    }
    return rows
}

/** Namen der Server mit nicht entschluesselbarem API-Key (sortiert, ohne Doppelte) – diese sind nicht geladen [D4]. */
export function unreadableServerNames(status) {
    const names = new Set()
    for (const item of Array.isArray(status?.unreadable) ? status.unreadable : []) {
        if (item?.kind === 'server' && item.name) names.add(String(item.name))
    }
    return [...names].sort((a, b) => a.localeCompare(b))
}

/** Anzahl nicht lesbarer Eintraege (Liste, ersatzweise Spaltensumme). */
export function unreadableCount(status) {
    const list = Array.isArray(status?.unreadable) ? status.unreadable.length : 0
    return list || secretsTotals(status).unreadable
}

/** Soll der Admin-Banner erscheinen? Nur bei health "error" (Klartext-Fallback oder unlesbare Werte, F5 §6.3). */
export function secretsBannerKind(status) {
    if (!status || status.health !== 'error') return null
    if (status.mode === 'plaintext_fallback') return 'plaintext'
    if (unreadableCount(status) > 0) return 'unreadable'
    return null
}

/**
 * Merker fuer "Banner geschlossen" (sessionStorage, F5 §6.3): `<fingerprint>:<anzahl unlesbar>:<mode>`.
 * Aendert sich die Lage (weiterer unlesbarer Wert, anderer Schluessel), erscheint der Banner wieder.
 */
export function secretsBannerSignature(status) {
    return `${status?.key_fingerprint || '-'}:${unreadableCount(status)}:${status?.mode || '-'}`
}
