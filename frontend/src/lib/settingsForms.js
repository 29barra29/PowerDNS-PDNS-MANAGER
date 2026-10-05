// Reine Hilfen fuer die Einstellungs-Tabs (F8-D02, D04, D05, D08, B07), ohne React/i18n-Import,
// damit sie per `node --test` geprueft werden koennen (tests/lib-f8.test.mjs).

// Optionale Profilfelder: werden immer als String gesendet, '' = Feld leeren (F8-D02, Backend F8 5.2)
export const PROFILE_OPTIONAL_FIELDS = Object.freeze(['phone', 'company', 'street', 'postal_code', 'city', 'country'])

/** Body fuer PUT /auth/me aus dem Profil-Formular. preferred_language nur, wenn eine Sprache gewaehlt ist. */
export function buildProfilePayload(form) {
    const f = form || {}
    const body = {
        username: f.username ?? '',
        display_name: f.display_name ?? '',
        email: f.email ?? '',
    }
    for (const key of PROFILE_OPTIONAL_FIELDS) body[key] = String(f[key] ?? '')
    body.date_of_birth = f.date_of_birth || ''
    if (f.preferred_language) body.preferred_language = f.preferred_language
    return body
}

/**
 * Body fuer PUT /settings/app-info (F8-D04): Texte getrimmt, leer = leeren (der Server liefert dann seinen
 * Standardtext), app_logo_url '' = Logo entfernen. app_base_url nur, wenn sie geladen war (includeBaseUrl) –
 * sonst wuerde ein Ladefehler die gespeicherte Basis-URL loeschen.
 */
export function buildAppInfoPayload(form, { includeBaseUrl = true } = {}) {
    const f = form || {}
    const body = {
        app_name: String(f.app_name ?? '').trim(),
        registration_enabled: !!f.registration_enabled,
        forgot_password_enabled: !!f.forgot_password_enabled,
        app_tagline: String(f.app_tagline ?? '').trim(),
        app_creator: String(f.app_creator ?? '').trim(),
        app_logo_url: f.app_logo_url ?? '',
    }
    if (includeBaseUrl) body.app_base_url = String(f.app_base_url ?? '').trim()
    return body
}

/**
 * SMTP-Passwort fuer PUT /settings/smtp (F8-D05): neuer Wert ersetzt, Haken "entfernen" loescht (''),
 * sonst null = gespeichertes Passwort behalten.
 */
export function smtpPasswordValue(password, clearStored) {
    if (password) return password
    return clearStored ? '' : null
}

/** GitHub-Fehler beim Laden der Commits einordnen (F8-D08): 404 -> notFound, 403/429 -> rateLimit, sonst other. */
export function commitErrorKind(status) {
    if (status === 404) return 'notFound'
    if (status === 403 || status === 429) return 'rateLimit'
    return 'other'
}

// Cache der GitHub-Versionspruefung (F8-B07): hoechstens alle 6 h ein Request
export const VERSION_CACHE_KEY = 'dns_manager_latest_version_cache'
export const VERSION_CACHE_MAX_AGE_MS = 6 * 60 * 60 * 1000

/** Gecachte Version, wenn der Eintrag gueltig und juenger als maxAgeMs ist, sonst null. raw = String aus localStorage. */
export function readVersionCache(raw, now = Date.now(), maxAgeMs = VERSION_CACHE_MAX_AGE_MS) {
    if (!raw) return null
    let entry
    try { entry = JSON.parse(raw) } catch { return null }
    if (!entry || typeof entry.v !== 'string' || !entry.v || typeof entry.at !== 'number') return null
    const age = now - entry.at
    if (age < 0 || age > maxAgeMs) return null
    return entry.v
}

/** Cache-Eintrag als String fuer localStorage. */
export function makeVersionCacheEntry(version, now = Date.now()) {
    return JSON.stringify({ v: String(version), at: now })
}
