// Reine Helfer der Panel-Token-Verwaltung (F14 §6.3) – ohne React/i18n, per `node --test` ladbar
// (getrennt von den Komponenten wegen react-refresh/only-export-components).
//
// Formularzustand (PanelTokenFormModal):
//   { name, scopeMode: 'all'|'selected', zones: string[] (normalisiert, sortiert), permission: 'manage'|'read',
//     expiry: 'unchanged'|'never'|'<tage>', allowAdmin: bool }

// identisch zu backend/app/services/panel_token.py (ZONE_NAME_RE)
export const ZONE_NAME_RE = /^(?=.{2,254}$)(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?\.)+$/
export const EXPIRY_OPTIONS = [7, 30, 90, 180, 365]
export const DEFAULT_EXPIRY_DAYS = 90
export const EXPIRES_SOON_DAYS = 14
export const MAX_RENDERED_ZONES = 500
export const NAME_MAX = 100

const DAY_MS = 86400000

// trim, lower, Trailing-Dot; '' bleibt ''
export function normalizeZoneInput(s) {
    const z = String(s ?? '').trim().toLowerCase()
    if (!z) return ''
    return z.endsWith('.') ? z : `${z}.`
}

export function isValidZone(z) {
    const n = normalizeZoneInput(z)
    return !!n && ZONE_NAME_RE.test(n)
}

// Ganze Tage bis zum Zeitpunkt (aufgerundet) oder null
export function daysUntil(iso, now = Date.now()) {
    if (!iso) return null
    const t = new Date(iso).getTime()
    if (Number.isNaN(t)) return null
    return Math.ceil((t - now) / DAY_MS)
}

// "Weitreichend": alle Zonen und kein Ablauf (betrifft nach dem Upgrade alle Bestandstokens)
export function isBroad(token) {
    return !!token && token.scope_zones === null && token.expires_at === null
}

// Anzeige-Zustand eines Listeneintrags (F14 §2.1)
//   status          'active'|'paused'|'expired'
//   broad           Badge "Weitreichend"
//   expiresSoon     Resttage (1..14) oder null
//   admin           'active' | 'inactive' (allow_admin, Besitzer kein Admin mehr) | null
//   inaccessible    Zonen im Scope ohne aktuelles Zonenrecht
export function tokenView(token, now = Date.now()) {
    const status = ['active', 'paused', 'expired'].includes(token?.status) ? token.status : 'active'
    const left = status === 'active' ? daysUntil(token?.expires_at, now) : null
    return {
        status,
        broad: isBroad(token),
        expiresSoon: left !== null && left > 0 && left <= EXPIRES_SOON_DAYS ? left : null,
        admin: token?.allow_admin ? (token.admin_effective ? 'active' : 'inactive') : null,
        inaccessible: Array.isArray(token?.inaccessible_zones) ? token.inaccessible_zones : [],
    }
}

// Vereinigung beliebiger Zonenlisten: normalisiert, ohne Duplikate, sortiert
export function mergeZoneNames(...lists) {
    const out = new Set()
    for (const list of lists) {
        for (const z of list || []) {
            const n = normalizeZoneInput(z)
            if (n) out.add(n)
        }
    }
    return [...out].sort()
}

// Zonen-Optionen eines Nicht-Admins aus /auth/me (zone_permissions = { zone: 'manage'|'read' })
// -> [{ name, readOnly }] sortiert
export function zoneOptionsFromPermissions(zonePermissions) {
    const map = new Map()
    for (const [zone, perm] of Object.entries(zonePermissions || {})) {
        const n = normalizeZoneInput(zone)
        if (!n) continue
        const readOnly = perm === 'read'
        // doppelt (Gross-/Kleinschreibung): Schreibrecht gewinnt
        map.set(n, map.has(n) ? map.get(n) && readOnly : readOnly)
    }
    return [...map.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, readOnly]) => ({ name, readOnly }))
}

// Optionen nach Freitext filtern; hoechstens `limit` rendern, `rest` = nicht gezeigte Treffer
export function filterZoneOptions(options, filter, limit = MAX_RENDERED_ZONES) {
    const f = String(filter || '').trim().toLowerCase()
    const hits = f ? (options || []).filter((o) => o.name.includes(f)) : (options || [])
    return { shown: hits.slice(0, limit), rest: Math.max(0, hits.length - limit) }
}

// Ausgangszustand des Formulars (create: restriktive UI-Defaults, F14 E15)
export function initialForm(mode, token) {
    if (mode === 'edit' && token) {
        const scoped = Array.isArray(token.scope_zones)
        return {
            name: token.name || '',
            scopeMode: scoped ? 'selected' : 'all',
            zones: scoped ? mergeZoneNames(token.scope_zones) : [],
            permission: token.permission === 'read' ? 'read' : 'manage',
            expiry: 'unchanged',
            allowAdmin: !!token.allow_admin,
        }
    }
    return {
        name: '',
        scopeMode: 'selected',
        zones: [],
        permission: 'manage',
        expiry: String(DEFAULT_EXPIRY_DAYS),
        allowAdmin: false,
    }
}

// i18n-Key des ersten Fehlers oder '' (Submit sonst gesperrt)
export function validateForm(form) {
    if (!String(form?.name ?? '').trim()) return 'panelTokens.nameRequired'
    if (form.scopeMode === 'selected' && !(form.zones || []).length) return 'panelTokens.zonesSelectAtLeastOne'
    return ''
}

function expiryDays(expiry) {
    if (expiry === 'never') return null
    const n = Number(expiry)
    return Number.isInteger(n) && n > 0 ? n : null
}

function scopeOf(form) {
    return form.scopeMode === 'all' ? null : mergeZoneNames(form.zones)
}

export function buildCreatePayload(form) {
    const scope = scopeOf(form)
    return {
        name: String(form.name ?? '').trim(),
        scope_zones: scope,
        permission: form.permission === 'read' ? 'read' : 'manage',
        expires_in_days: expiryDays(form.expiry),
        // Backend erzwingt dasselbe: Admin-Freigabe nur ohne Zonen-Scope
        allow_admin: scope === null && !!form.allowAdmin,
    }
}

// Nur Unterschiede zum Listeneintrag `initial`; {} bei keiner Aenderung (F14 §2.4)
export function buildUpdatePayload(initial, form) {
    const out = {}
    const name = String(form.name ?? '').trim()
    if (name !== (initial?.name ?? '')) out.name = name
    const scope = scopeOf(form)
    const initialScope = Array.isArray(initial?.scope_zones) ? mergeZoneNames(initial.scope_zones) : null
    if (JSON.stringify(scope) !== JSON.stringify(initialScope)) out.scope_zones = scope
    const permission = form.permission === 'read' ? 'read' : 'manage'
    if (permission !== (initial?.permission === 'read' ? 'read' : 'manage')) out.permission = permission
    const allowAdmin = scope === null && !!form.allowAdmin
    if (allowAdmin !== !!initial?.allow_admin) out.allow_admin = allowAdmin
    if (form.expiry !== 'unchanged') out.expires_in_days = expiryDays(form.expiry)
    return out
}
