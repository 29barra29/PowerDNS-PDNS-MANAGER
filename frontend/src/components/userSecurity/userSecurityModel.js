// Reine Hilfsfunktionen der Benutzerverwaltung (WS-F2F3): kein React, kein i18n – per `node --test` ladbar
// (frontend/tests/userSecurityModel.test.mjs). Genutzt von UsersPage, UserSecurityModal und den Abschnitten.

// Lokales Konto? Externe Konten (F10: oidc/ldap) verwalten ihr Passwort beim Identitaetsanbieter.
export function isLocalAccount(user) {
    return (user?.auth_source || 'local') === 'local'
}

// Reset-Link-Button (F3 §2.4 C): { enabled, reason: null | 'no_email' | 'no_smtp' | 'inactive' }
export function resetLinkState(user, mailAvailable) {
    if (!user?.email) return { enabled: false, reason: 'no_email' }
    if (!mailAvailable) return { enabled: false, reason: 'no_smtp' }
    if (user.is_active === false) return { enabled: false, reason: 'inactive' }
    return { enabled: true, reason: null }
}

// Badges der Benutzerkarte (F3 §2.3): Liste von { key, tone, count? } in fester Reihenfolge.
export function securityBadges(user) {
    if (!user) return []
    const out = []
    if (user.must_change_password) out.push({ key: 'mustChange', tone: 'warning' })
    if (user.totp_enabled) out.push({ key: user.totp_unreadable ? 'totpUnreadable' : '2fa', tone: user.totp_unreadable ? 'danger' : 'success' })
    const passkeys = Number(user.passkey_count || 0)
    if (passkeys > 0) out.push({ key: 'passkeys', tone: 'info', count: passkeys })
    return out
}

// Zonen-Editor (F8-I02, f152): zugewiesene Zonen, die in keiner geladenen Zonenliste vorkommen (geloescht oder
// Server offline). Vergleich ohne Gross/Klein und mit Trailing-Dot. Reihenfolge wie in `selected`.
export function orphanZones(selected, allZones) {
    const norm = (z) => {
        const v = String(z || '').trim().toLowerCase()
        return v && !v.endsWith('.') ? `${v}.` : v
    }
    const known = new Set((allZones || []).map((z) => norm(typeof z === 'string' ? z : z?.name)))
    return (selected || []).filter((z) => !known.has(norm(z)))
}

// Body fuer "Benutzer anlegen" (F3 §2.5): leere E-Mail nicht senden.
export function createUserPayload(form) {
    const email = String(form?.email || '').trim()
    const payload = {
        username: String(form?.username || '').trim(),
        password: form?.password || '',
        display_name: String(form?.display_name || '').trim() || undefined,
        role: form?.role === 'admin' ? 'admin' : 'user',
        must_change_password: !!form?.must_change_password,
    }
    if (email) payload.email = email
    return payload
}
