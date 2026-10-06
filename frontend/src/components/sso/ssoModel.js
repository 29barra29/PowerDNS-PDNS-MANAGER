// Reine Helfer fuer SSO (F10 §6, WS-F10-APP-FE): Login-Seite, Admin-Tab "Anmeldung / SSO", Konto-Verknuepfung.
// Kein React, kein i18n, kein import.meta – per `node --test` ladbar (frontend/tests/ssoModel.test.mjs).
//
// API-Vertraege (WS-F10-APP-BE / WS-F10-SVC): GET /auth/sso/providers (SsoProvidersOut), GET/PUT /settings/sso
// (SsoSettingsOut, SsoSettingsUpdate), POST /settings/sso/test (SsoTestRequest). Feldnamen wie
// backend/app/schemas/sso.py.

// Maske, mit der das Backend gespeicherte Secrets ausliefert (core/secret_mask.SECRET_MASK)
export const SECRET_MASK = '••••••••'

// ---------------------------------------------------------------------------------------------
// Login-Seite (F10 §2.1, §6.3)

// sso_error-Code (Redirect des Backends) -> Unter-Key von login.ssoError.*; unbekannt -> generic.
// totp_unreadable: Plan [S4] (2FA aktiv, Geheimnis nicht entschluesselbar -> kein Login).
export const SSO_ERROR_KEYS = Object.freeze({
    disabled: 'disabled',
    config: 'config',
    discovery: 'discovery',
    state: 'state',
    idp_error: 'idpError',
    token: 'token',
    id_token: 'idToken',
    no_account: 'noAccount',
    not_allowed: 'notAllowed',
    account_disabled: 'accountDisabled',
    link_conflict: 'linkConflict',
    link_failed: 'linkFailed',
    internal: 'internal',
    totp_unreadable: 'totp_unreadable',
})

export function ssoErrorKey(code) {
    const c = String(code || '')
    return `login.ssoError.${Object.prototype.hasOwnProperty.call(SSO_ERROR_KEYS, c) ? SSO_ERROR_KEYS[c] : 'generic'}`
}

// sso_detail ist ein Fehlercode des Anbieters (validiert im Backend); im Frontend nochmals hart begrenzt.
export function sanitizeSsoDetail(value) {
    return String(value || '').replace(/[^a-z_]/g, '').slice(0, 40)
}

// URL-Parameter der Login-Seite einmalig lesen (search = window.location.search).
export function readLoginUrlFlags(search) {
    let p
    try {
        p = new URLSearchParams(search || '')
    } catch {
        p = new URLSearchParams('')
    }
    const ssoError = (p.get('sso_error') || '').replace(/[^a-z_]/g, '').slice(0, 40)
    return {
        ssoError,
        ssoDetail: sanitizeSsoDetail(p.get('sso_detail')),
        sso2fa: p.get('sso_2fa') === '1',
        local: p.get('local') === '1',
        // Waren Parameter gesetzt, die nach dem Lesen aus der Adresszeile verschwinden sollen?
        hadParams: ['sso_error', 'sso_detail', 'sso_2fa', 'local'].some((k) => p.has(k)),
    }
}

// Ausgangszustand, solange /auth/sso/providers laeuft oder scheitert: Seite wie 2.4.1 (Formular sichtbar).
export const DEFAULT_PROVIDERS = Object.freeze({
    local_login_enabled: true,
    providers: [],
    ldap: { enabled: false, label: '' },
    linking: { oidc: false, ldap: false },
})

// Antwort von /auth/sso/providers defensiv normalisieren (nur Eintraege mit start_url unter /api/).
export function normalizeProviders(raw) {
    const r = raw && typeof raw === 'object' ? raw : {}
    const providers = Array.isArray(r.providers)
        ? r.providers.filter((p) => p && typeof p.start_url === 'string' && p.start_url.startsWith('/api/'))
            .map((p) => ({ id: String(p.id || p.type || 'sso'), type: String(p.type || ''), label: String(p.label || 'SSO'), start_url: p.start_url }))
        : []
    const ldap = r.ldap && typeof r.ldap === 'object' ? r.ldap : {}
    const linking = r.linking && typeof r.linking === 'object' ? r.linking : {}
    return {
        local_login_enabled: r.local_login_enabled !== false,
        providers,
        ldap: { enabled: !!ldap.enabled, label: ldap.enabled ? String(ldap.label || 'LDAP') : '' },
        linking: { oidc: !!linking.oidc, ldap: !!linking.ldap },
    }
}

// Sichtbarkeit der Login-Elemente (F10 §2.1 Nr. 4-8).
export function loginVisibility(providers, flags, appInfo = {}) {
    const p = providers || DEFAULT_PROVIDERS
    const f = flags || {}
    const localAllowed = p.local_login_enabled || !!f.local
    const hasSso = (p.providers || []).length > 0
    const showForm = localAllowed || !!p.ldap?.enabled
    return {
        showSsoButtons: hasSso && !f.sso2fa,
        ssoPrimary: hasSso && !p.local_login_enabled && !p.ldap?.enabled && !f.local,
        showForm,
        showDivider: hasSso && showForm && !f.sso2fa,
        showLdapHint: !!p.ldap?.enabled && !f.sso2fa,
        showPasskey: localAllowed && !f.sso2fa,
        showForgot: !!appInfo.forgot_password_enabled && localAllowed,
        showRegister: !!appInfo.registration_enabled,
        showEmergencyLink: !showForm,
        showBackToSso: !!f.local && hasSso,
        showLocalModeHint: !!f.local,
    }
}

// ---------------------------------------------------------------------------------------------
// Admin-Tab "Anmeldung / SSO" (F10 §2.5, §6.4; Plan [S1], [S5], [S8], [S16], E-F10-2)

// Unveraenderliche, vom Verzeichnis vergebene IDs (schemas/sso.SAFE_UNIQUE_ID_ATTRS) [S16]
export const SAFE_UNIQUE_ID_ATTRS = Object.freeze(['objectGUID', 'entryUUID', 'nsUniqueId', 'ipaUniqueID'])

export function isSafeUniqueIdAttr(attr) {
    const a = String(attr || '').trim().toLowerCase()
    return !!a && SAFE_UNIQUE_ID_ATTRS.some((s) => s.toLowerCase() === a)
}

// Felder, deren Aenderung das Backend nur mit Step-up annimmt (services/sso_settings.SENSITIVE_FIELDS) [S8].
// Das Frontend fragt bei lokalen Konten vorab; massgeblich bleibt die Antwort des Backends (403 stepup_required).
export const SENSITIVE_FIELDS = Object.freeze({
    general: Object.freeze(['local_login_enabled']),
    oidc: Object.freeze([
        'enabled', 'issuer', 'client_id', 'token_auth_method', 'jit_enabled', 'jit_allow_any_account',
        'role_mode', 'admin_groups', 'allowed_groups', 'allowed_email_domains', 'groups_claim',
    ]),
    ldap: Object.freeze([
        'enabled', 'server_urls', 'bind_dn', 'security', 'tls_verify', 'ca_cert', 'jit_enabled',
        'jit_allow_any_account', 'role_mode', 'admin_groups', 'allowed_groups', 'unique_id_attr',
        'unique_id_attr_confirmed', 'group_mode', 'group_base_dn', 'group_filter',
    ]),
})

// Empfohlene Obergrenze der Sitzungsdauer bei SSO (E-F10-2): 24 h.
export const SSO_SESSION_WARN_SECONDS = 86400

// Warnung, wenn die Sitzung (AUTH_COOKIE_MAX_AGE) laenger als 24 h gilt: Stunden (gerundet) oder null.
export function sessionLifetimeHours(seconds) {
    const n = Number(seconds)
    if (!Number.isFinite(n) || n <= SSO_SESSION_WARN_SECONDS) return null
    return Math.round(n / 3600)
}

// Textarea (eine Zeile je Eintrag) <-> Liste
export function linesToList(text) {
    return String(text || '').split('\n').map((s) => s.trim()).filter(Boolean)
}

export function listToLines(list) {
    return Array.isArray(list) ? list.join('\n') : ''
}

// DN in RDNs zerlegen (Komma mit Backslash-Escape wird respektiert). Liefert null bei ungueltigem Aufbau.
export function splitDn(value) {
    const s = String(value || '').trim()
    if (!s) return null
    const parts = []
    let cur = ''
    for (let i = 0; i < s.length; i++) {
        const c = s[i]
        if (c === '\\' && i + 1 < s.length) {
            cur += c + s[i + 1]
            i++
        } else if (c === ',' || c === ';') {
            parts.push(cur.trim())
            cur = ''
        } else {
            cur += c
        }
    }
    parts.push(cur.trim())
    if (parts.some((p) => !/^[A-Za-z][A-Za-z0-9-]*\s*=\s*\S/.test(p) && !/^\d+(\.\d+)+\s*=\s*\S/.test(p))) return null
    return parts
}

// LDAP-Gruppen nur als vollstaendige DN mit mindestens zwei RDNs (Backend: 422) [S1]. Liefert die Zeilen,
// die das nicht erfuellen (fuer einen Hinweis direkt am Feld).
export function invalidGroupDns(text) {
    return linesToList(text).filter((g) => {
        const parts = splitDn(g)
        return !parts || parts.length < 2
    })
}

// Formularzustand aus GET /settings/sso (bzw. settings aus der PUT-Antwort)
export function generalFormFromSettings(general) {
    const g = general || {}
    return {
        local_login_enabled: g.local_login_enabled !== false,
        require_totp: g.require_totp !== false,
    }
}

export function oidcFormFromSettings(oidc) {
    const o = oidc || {}
    return {
        enabled: !!o.enabled,
        display_name: o.display_name || 'SSO',
        issuer: o.issuer || '',
        client_id: o.client_id || '',
        client_secret_set: !!o.client_secret_set,
        client_secret_unreadable: !!o.client_secret_unreadable,
        client_secret_new: '',
        client_secret_remove: false,
        token_auth_method: o.token_auth_method || 'client_secret_basic',
        scopes: o.scopes || 'openid profile email',
        prompt: o.prompt || '',
        use_userinfo: o.use_userinfo !== false,
        username_claim: o.username_claim ?? 'preferred_username',
        email_claim: o.email_claim ?? 'email',
        name_claim: o.name_claim ?? 'name',
        groups_claim: o.groups_claim ?? 'groups',
        allowed_groups: listToLines(o.allowed_groups),
        admin_groups: listToLines(o.admin_groups),
        allowed_email_domains: listToLines(o.allowed_email_domains),
        role_mode: o.role_mode || 'off',
        jit_enabled: !!o.jit_enabled,
        jit_allow_any_account: !!o.jit_allow_any_account,
        allow_linking: o.allow_linking !== false,
        linked_accounts: Number(o.linked_accounts || 0),
    }
}

export function ldapFormFromSettings(ldap) {
    const l = ldap || {}
    return {
        enabled: !!l.enabled,
        display_name: l.display_name || 'LDAP',
        server_urls: listToLines(l.server_urls),
        security: l.security || 'ldaps',
        tls_verify: l.tls_verify !== false,
        ca_cert: l.ca_cert || '',
        timeout: Number.isFinite(Number(l.timeout)) && l.timeout !== null && l.timeout !== '' ? Number(l.timeout) : 8,
        bind_dn: l.bind_dn || '',
        bind_password_set: !!l.bind_password_set,
        bind_password_unreadable: !!l.bind_password_unreadable,
        bind_password_new: '',
        bind_password_remove: false,
        user_base_dn: l.user_base_dn || '',
        user_filter: l.user_filter || '',
        username_attr: l.username_attr ?? 'sAMAccountName',
        email_attr: l.email_attr ?? 'mail',
        name_attr: l.name_attr ?? 'displayName',
        unique_id_attr: l.unique_id_attr ?? 'objectGUID',
        unique_id_attr_confirmed: !!l.unique_id_attr_confirmed,
        group_mode: l.group_mode || 'memberof',
        group_base_dn: l.group_base_dn || '',
        group_filter: l.group_filter || '',
        allowed_groups: listToLines(l.allowed_groups),
        admin_groups: listToLines(l.admin_groups),
        role_mode: l.role_mode || 'off',
        jit_enabled: !!l.jit_enabled,
        jit_allow_any_account: !!l.jit_allow_any_account,
        allow_linking: l.allow_linking !== false,
        linked_accounts: Number(l.linked_accounts || 0),
    }
}

// Secret-Feld: entfernen -> "", neu eingegeben -> Wert (ungetrimmt), sonst weglassen (= gespeichertes behalten)
export function secretPayload(newValue, remove) {
    if (remove) return ''
    const v = String(newValue ?? '')
    return v.trim() ? v : undefined
}

function withoutUndefined(obj) {
    const out = {}
    for (const [k, v] of Object.entries(obj)) if (v !== undefined) out[k] = v
    return out
}

export function generalPayload(form) {
    const f = form || {}
    return { local_login_enabled: !!f.local_login_enabled, require_totp: !!f.require_totp }
}

export function oidcPayload(form) {
    const f = form || {}
    return withoutUndefined({
        enabled: !!f.enabled,
        display_name: String(f.display_name ?? '').trim(),
        issuer: String(f.issuer ?? '').trim(),
        client_id: String(f.client_id ?? '').trim(),
        client_secret: secretPayload(f.client_secret_new, f.client_secret_remove),
        token_auth_method: f.token_auth_method || 'client_secret_basic',
        scopes: String(f.scopes ?? '').trim(),
        prompt: f.prompt || '',
        use_userinfo: !!f.use_userinfo,
        username_claim: String(f.username_claim ?? '').trim(),
        email_claim: String(f.email_claim ?? '').trim(),
        name_claim: String(f.name_claim ?? '').trim(),
        groups_claim: String(f.groups_claim ?? '').trim(),
        allowed_groups: linesToList(f.allowed_groups),
        admin_groups: linesToList(f.admin_groups),
        allowed_email_domains: linesToList(f.allowed_email_domains),
        role_mode: f.role_mode || 'off',
        jit_enabled: !!f.jit_enabled,
        jit_allow_any_account: !!f.jit_allow_any_account,
        allow_linking: !!f.allow_linking,
    })
}

export function ldapPayload(form) {
    const f = form || {}
    const timeout = Number.parseInt(f.timeout, 10)
    const body = withoutUndefined({
        enabled: !!f.enabled,
        display_name: String(f.display_name ?? '').trim(),
        server_urls: linesToList(f.server_urls),
        security: f.security || 'ldaps',
        tls_verify: !!f.tls_verify,
        ca_cert: String(f.ca_cert ?? '').trim(),
        timeout: Number.isFinite(timeout) ? timeout : 8,
        bind_dn: String(f.bind_dn ?? '').trim(),
        bind_password: secretPayload(f.bind_password_new, f.bind_password_remove),
        user_base_dn: String(f.user_base_dn ?? '').trim(),
        user_filter: String(f.user_filter ?? '').trim(),
        username_attr: String(f.username_attr ?? '').trim(),
        email_attr: String(f.email_attr ?? '').trim(),
        name_attr: String(f.name_attr ?? '').trim(),
        unique_id_attr: String(f.unique_id_attr ?? '').trim(),
        group_mode: f.group_mode || 'memberof',
        group_base_dn: String(f.group_base_dn ?? '').trim(),
        group_filter: String(f.group_filter ?? '').trim(),
        allowed_groups: linesToList(f.allowed_groups),
        admin_groups: linesToList(f.admin_groups),
        role_mode: f.role_mode || 'off',
        jit_enabled: !!f.jit_enabled,
        jit_allow_any_account: !!f.jit_allow_any_account,
        allow_linking: !!f.allow_linking,
    })
    // Bestaetigung nur fuer nicht unveraenderliche ID-Attribute senden; beim Wechsel auf ein sicheres Attribut
    // setzt das Backend sie selbst zurueck [S16].
    if (!isSafeUniqueIdAttr(body.unique_id_attr)) body.unique_id_attr_confirmed = !!f.unique_id_attr_confirmed
    return body
}

export const SECTION_PAYLOAD = Object.freeze({ general: generalPayload, oidc: oidcPayload, ldap: ldapPayload })

function sameValue(a, b) {
    if (Array.isArray(a) || Array.isArray(b)) {
        const x = Array.isArray(a) ? a : []
        const y = Array.isArray(b) ? b : []
        return x.length === y.length && x.every((v, i) => String(v) === String(y[i]))
    }
    if (typeof a === 'boolean' || typeof b === 'boolean') return !!a === !!b
    return String(a ?? '') === String(b ?? '')
}

// Werden mit diesem Abschnitt sensible Felder geaendert? (Vergleich gegen den zuletzt geladenen Stand)
export function changedSensitiveFields(section, saved, payload) {
    const fields = SENSITIVE_FIELDS[section] || []
    const s = saved || {}
    const p = payload || {}
    return fields.filter((name) => {
        if (!(name in p)) return false
        if (name === 'unique_id_attr_confirmed' && !(name in s)) return !!p[name]
        return !sameValue(p[name], s[name])
    })
}

// JIT ohne jede Einschraenkung braucht die ausdrueckliche Bestaetigung jit_allow_any_account [S5].
export function jitNeedsConfirmation(section, form) {
    const f = form || {}
    if (!f.jit_enabled) return false
    if (linesToList(f.allowed_groups).length) return false
    if (section === 'oidc' && linesToList(f.allowed_email_domains).length) return false
    return true
}

// ID-Attribut ausserhalb der Allowlist (oder leer = DN) braucht unique_id_attr_confirmed [S16].
export function uniqueIdNeedsConfirmation(form) {
    return !isSafeUniqueIdAttr(form?.unique_id_attr)
}

// Clientseitige Pruefung vor dem Speichern: Liste von Fehler-Keys (i18n); leer = senden.
export function sectionBlockers(section, form) {
    const out = []
    if (section === 'oidc' || section === 'ldap') {
        if (jitNeedsConfirmation(section, form) && !form?.jit_allow_any_account) out.push('settings.sso.jitAllowAnyRequired')
    }
    if (section === 'ldap' && uniqueIdNeedsConfirmation(form) && !form?.unique_id_attr_confirmed) {
        out.push('settings.sso.uniqueIdConfirmRequired')
    }
    return out
}

// ---------------------------------------------------------------------------------------------
// Vorlagen (F10 §6.4). Werte werden erst mit "Speichern" uebernommen; Issuer nur als Platzhalter.

export const OIDC_PRESETS = Object.freeze({
    generic: Object.freeze({
        labelKey: 'settings.sso.presetGeneric',
        values: { scopes: 'openid profile email', username_claim: 'preferred_username', email_claim: 'email', name_claim: 'name', groups_claim: 'groups', token_auth_method: 'client_secret_basic' },
        issuerPlaceholder: 'https://sso.example.com',
    }),
    keycloak: Object.freeze({
        labelKey: 'settings.sso.presetKeycloak',
        values: { scopes: 'openid profile email', username_claim: 'preferred_username', email_claim: 'email', name_claim: 'name', groups_claim: 'groups', token_auth_method: 'client_secret_basic' },
        issuerPlaceholder: 'https://sso.example.com/realms/<realm>',
    }),
    authentik: Object.freeze({
        labelKey: 'settings.sso.presetAuthentik',
        values: { scopes: 'openid profile email', username_claim: 'preferred_username', email_claim: 'email', name_claim: 'name', groups_claim: 'groups', token_auth_method: 'client_secret_basic' },
        issuerPlaceholder: 'https://auth.example.com/application/o/<slug>/',
    }),
    entra: Object.freeze({
        labelKey: 'settings.sso.presetEntra',
        values: { scopes: 'openid profile email', username_claim: 'preferred_username', email_claim: 'email', name_claim: 'name', groups_claim: 'groups', token_auth_method: 'client_secret_basic' },
        issuerPlaceholder: 'https://login.microsoftonline.com/<tenant-id>/v2.0',
    }),
})

export const LDAP_PRESETS = Object.freeze({
    ad: Object.freeze({
        labelKey: 'settings.sso.ldapPresetAd',
        values: {
            user_filter: '(&(objectCategory=person)(objectClass=user)(sAMAccountName={username})(!(userAccountControl:1.2.840.113556.1.4.803:=2)))',
            username_attr: 'sAMAccountName', email_attr: 'mail', name_attr: 'displayName', unique_id_attr: 'objectGUID',
            group_mode: 'memberof', group_filter: '(&(objectClass=group)(member:1.2.840.113556.1.4.1941:={user_dn}))',
        },
        serverPlaceholder: 'ldaps://dc1.example.local:636',
    }),
    openldap: Object.freeze({
        labelKey: 'settings.sso.ldapPresetOpenldap',
        values: {
            user_filter: '(&(objectClass=inetOrgPerson)(uid={username}))',
            username_attr: 'uid', email_attr: 'mail', name_attr: 'cn', unique_id_attr: 'entryUUID',
            group_mode: 'search', group_filter: '(&(objectClass=groupOfNames)(member={user_dn}))',
        },
        serverPlaceholder: 'ldaps://ldap.example.local:636',
    }),
    freeipa: Object.freeze({
        labelKey: 'settings.sso.ldapPresetFreeipa',
        values: {
            user_filter: '(&(objectClass=person)(uid={username}))',
            username_attr: 'uid', email_attr: 'mail', name_attr: 'displayName', unique_id_attr: 'ipaUniqueID',
            group_mode: 'memberof', group_filter: '(&(objectClass=groupOfNames)(member={user_dn}))',
        },
        serverPlaceholder: 'ldaps://ipa.example.local:636',
    }),
})

// Vorlage auf ein Formular anwenden (nur die Vorlagenfelder, alles andere bleibt). Bei LDAP gilt das neue
// ID-Attribut als nicht bestaetigt (alle Vorlagen nutzen Attribute aus der Allowlist).
export function applyPreset(kind, presetId, form) {
    const table = kind === 'ldap' ? LDAP_PRESETS : OIDC_PRESETS
    const preset = table[presetId]
    if (!preset) return form
    const next = { ...(form || {}), ...preset.values }
    if (kind === 'ldap') next.unique_id_attr_confirmed = false
    return next
}

// ---------------------------------------------------------------------------------------------
// Konto-Verknuepfung (F10 §2.6, §6.5)

// Autorisierungs-URL des Anbieters (aus POST /auth/me/sso/oidc/link): nur http(s), sonst null.
export function safeAuthorizationUrl(url) {
    if (typeof url !== 'string' || !url) return null
    try {
        const u = new URL(url)
        return u.protocol === 'https:' || u.protocol === 'http:' ? u.toString() : null
    } catch {
        return null
    }
}

// Quelle eines externen Kontos -> Key unter settings.authSource.* (unbekannte Quellen wie "oidc")
export function authSourceKey(source) {
    const s = String(source || 'local')
    if (s === 'local') return 'settings.authSource.local'
    if (s === 'ldap') return 'settings.authSource.ldap'
    return 'settings.authSource.oidc'
}

export function isExternalAccount(user) {
    return (user?.auth_source || 'local') !== 'local'
}
