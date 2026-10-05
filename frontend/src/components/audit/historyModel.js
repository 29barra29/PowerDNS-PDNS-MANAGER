// Reine Modell-Helfer fuer Zonenverlauf, Rollback-Dialog und Admin-Audit-Log (F7 §6, WS-F7-FE).
// Kein React, kein i18n, kein import.meta - per `node --test` ladbar (frontend/tests/integration/history.test.mjs).
// Datenvertrag: Audit-Details v2 (F7 §4.3, Plan B.7) und die Schemas aus F7 §3.1 (HistoryEntry, AuditLogEntry,
// RollbackPreviewResponse) inkl. der Plan-Ergaenzungen `before_recreate`, `actor_username`, `client_ip`,
// `primary_outcome` [D3, D12, S12].
import { relativeRecordName } from '../../lib/dnsName.js'
import { toIsoFromLocalInput, parseDateValue } from '../../lib/datetime.js'
import { fanoutMap } from '../../lib/fanout.js'

export const HISTORY_PAGE_SIZES = Object.freeze([25, 50, 100])
export const HISTORY_DEFAULT_LIMIT = 25
export const AUDIT_PAGE_SIZES = Object.freeze([25, 50, 100, 200])
export const AUDIT_DEFAULT_LIMIT = 50
export const SEARCH_MIN_LENGTH = 2
export const SEARCH_MAX_LENGTH = 100
export const SEARCH_DEBOUNCE_MS = 400
export const RETENTION_MIN_DAYS = 7
export const RETENTION_MAX_DAYS = 3650

// "Bereich" im Zonenverlauf -> resource_type (F7 §2.1)
export const HISTORY_AREAS = Object.freeze(['record', 'zone', 'dnssec_key', 'acme'])

// primary_outcome-Werte, die eine Kennzeichnung brauchen ("ok" bzw. fehlend = unauffaellig) [D3]
export const NOTABLE_PRIMARY_OUTCOMES = Object.freeze(['verified_after_timeout', 'unknown', 'failed'])

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

// -------------------------------------------------------------------------------------------------
// Snapshots und Diff (RrsetChangeDiff)

function recordList(snap) {
    if (!isObj(snap) || !Array.isArray(snap.records)) return []
    return snap.records
        .filter((r) => r && typeof r === 'object')
        .map((r) => ({ content: String(r.content ?? ''), disabled: Boolean(r.disabled) }))
}

function commentKey(c) {
    return `${String(c?.content ?? '')}\u0000${String(c?.account ?? '')}`
}

// Art einer Aenderung: before null -> created, after null -> deleted, sonst modified.
export function changeKind(change) {
    const before = isObj(change?.before) ? change.before : null
    const after = isObj(change?.after) ? change.after : null
    if (!before && after) return 'created'
    if (before && !after) return 'deleted'
    return 'modified'
}

// Mengen-Diff der Werte (Schluessel: content). Liefert Zeilen in stabiler Reihenfolge:
// [{ content, status: 'removed'|'added'|'unchanged', disabledBefore, disabledAfter }]
// - removed/added: Wert nur vorher/nachher vorhanden
// - unchanged: Wert in beiden; disabledBefore !== disabledAfter markiert einen Disabled-Wechsel
export function diffRecords(before, after) {
    const b = new Map(recordList(before).map((r) => [r.content, r]))
    const a = new Map(recordList(after).map((r) => [r.content, r]))
    const rows = []
    for (const [content, r] of b) {
        if (a.has(content)) {
            rows.push({ content, status: 'unchanged', disabledBefore: r.disabled, disabledAfter: a.get(content).disabled })
        } else {
            rows.push({ content, status: 'removed', disabledBefore: r.disabled, disabledAfter: null })
        }
    }
    for (const [content, r] of a) {
        if (!b.has(content)) rows.push({ content, status: 'added', disabledBefore: null, disabledAfter: r.disabled })
    }
    const order = { removed: 0, added: 1, unchanged: 2 }
    return rows.sort((x, y) => (order[x.status] - order[y.status]) || x.content.localeCompare(y.content))
}

// TTL-Angabe: { from, to, changed } (null-Werte bei Anlage/Loeschung)
export function ttlChange(change) {
    const from = isObj(change?.before) && change.before.ttl != null ? Number(change.before.ttl) : null
    const to = isObj(change?.after) && change.after.ttl != null ? Number(change.after.ttl) : null
    return { from, to, changed: from !== null && to !== null && from !== to }
}

// Kommentare geaendert? (nur wenn beide Seiten existieren; Vergleich content+account als Menge)
export function commentsChanged(change) {
    if (!isObj(change?.before) || !isObj(change?.after)) return false
    const b = (Array.isArray(change.before.comments) ? change.before.comments : []).map(commentKey).sort()
    const a = (Array.isArray(change.after.comments) ? change.after.comments : []).map(commentKey).sort()
    return b.length !== a.length || b.some((k, i) => k !== a[i])
}

// Hat der Diff eine Disabled-Aenderung?
export function hasDisabledFlip(rows) {
    return rows.some((r) => r.status === 'unchanged' && r.disabledBefore !== r.disabledAfter)
}

// Anzeigename relativ zur Zone ('@' fuer den Apex)
export function displayName(fqdn, zoneKey) {
    if (!zoneKey) return String(fqdn ?? '').replace(/\.$/, '')
    return relativeRecordName(fqdn, zoneKey)
}

// -------------------------------------------------------------------------------------------------
// Eintraege (HistoryEntry / AuditLogEntry)

export function isV2Details(details) {
    return isObj(details) && details.version === 2
}

// Aenderungen eines Eintrags: HistoryEntry.changes (Verlauf) oder details.changes (Audit-Log)
export function entryChanges(entry) {
    if (Array.isArray(entry?.changes)) return entry.changes
    if (isV2Details(entry?.details) && Array.isArray(entry.details.changes)) return entry.details.changes
    return []
}

// Gesamtzahl der Aenderungen (auch wenn die Liste gekuerzt ist)
export function entryChangeCount(entry) {
    if (Number.isInteger(entry?.change_count) && entry.change_count > 0) return entry.change_count
    const d = entry?.details
    if (isObj(d) && Number.isInteger(d.change_count) && d.change_count > 0) return d.change_count
    return entryChanges(entry).length
}

// Version eines Eintrags: HistoryEntry.version oder aus details abgeleitet
export function entryVersion(entry) {
    if (entry?.version === 1 || entry?.version === 2) return entry.version
    return isV2Details(entry?.details) ? 2 : 1
}

// Muessen die vollstaendigen Aenderungen nachgeladen werden?
export function needsFullLoad(entry) {
    return Boolean(entry?.changes_truncated || entry?.details_truncated)
}

// Kurzfassung fuer Listen: genau eine Aenderung -> { kind: 'single', name, type, change }, sonst
// { kind: 'count', count } (count 0 bei v1/Nicht-Record-Eintraegen -> kind 'none').
export function entrySummary(entry) {
    const changes = entryChanges(entry)
    const count = entryChangeCount(entry)
    if (count === 1 && changes.length === 1) {
        const c = changes[0]
        return { kind: 'single', name: c.name, type: c.type, change: changeKind(c) }
    }
    if (count > 0) return { kind: 'count', count }
    return { kind: 'none' }
}

// Handelnder Benutzer: { kind: 'user', name } | { kind: 'deleted', id, name? } | { kind: 'system' }
// username = aktueller Name (null = geloescht); actor_username = Name zum Zeitpunkt der Aktion (E-F7-1).
export function actorInfo(entry) {
    if (entry?.user_id === null || entry?.user_id === undefined) {
        if (entry?.actor_username) return { kind: 'user', name: entry.actor_username }
        return { kind: 'system' }
    }
    if (entry.username) return { kind: 'user', name: entry.username, id: entry.user_id }
    return { kind: 'deleted', id: entry.user_id, name: entry.actor_username || null }
}

// Panel-Token als Ausloeser (details.auth.via == "panel_token", F14) -> { name } | null
export function tokenAuth(details) {
    const auth = isObj(details) ? details.auth : null
    if (!isObj(auth) || auth.via !== 'panel_token') return null
    return { name: auth.token_name || null }
}

// primary_outcome mit Kennzeichnungsbedarf (verified_after_timeout|unknown|failed) oder null [D3]
export function notablePrimaryOutcome(details) {
    const v = isObj(details) ? details.primary_outcome : null
    return NOTABLE_PRIMARY_OUTCOMES.includes(v) ? v : null
}

// Sonderfaelle der v2-Details: { incomplete, truncated, changeKeys, computedAfter }
export function historyFlags(details) {
    if (!isObj(details)) return { incomplete: false, truncated: false, changeKeys: [], computedAfter: false }
    return {
        incomplete: Boolean(details.history_incomplete),
        truncated: Boolean(details.history_truncated),
        changeKeys: Array.isArray(details.change_keys) ? details.change_keys.filter(isObj) : [],
        computedAfter: details.after_source === 'computed',
    }
}

// Legacy-Felder (v1) zur Anzeige: [{ key, value }] in fester Reihenfolge, nur vorhandene
const LEGACY_KEYS = ['type', 'ttl', 'records', 'old', 'new', 'content', 'created', 'deleted']
export function legacyFields(details) {
    if (!isObj(details)) return []
    const out = []
    for (const key of LEGACY_KEYS) {
        if (!(key in details)) continue
        const v = details[key]
        if (v === undefined) continue
        out.push({ key, value: Array.isArray(v) ? v.map((x) => (isObj(x) ? JSON.stringify(x) : String(x))).join(', ') : (v === null ? '–' : (isObj(v) ? JSON.stringify(v) : String(v))) })
    }
    return out
}

// Fan-out-Tabelle: [{ server, status, level: 'ok'|'skipped'|'warning'|'error' }], Server alphabetisch
export function fanoutRows(details) {
    const map = isObj(details) && isObj(details.fanout) ? details.fanout : {}
    return Object.keys(map).sort().map((server) => {
        const status = String(map[server] ?? '')
        let level = 'ok'
        if (status.startsWith('error:')) level = 'error'
        else if (/^skipped \(not loaded/i.test(status)) level = 'warning'
        else if (status.startsWith('skipped')) level = 'skipped'
        return { server, status, level }
    })
}

// Server, deren Fan-out mit "error:" endete
export function fanoutErrorServers(details) {
    return Object.entries(fanoutMap(details))
        .filter(([, s]) => typeof s === 'string' && s.startsWith('error:'))
        .map(([server]) => server)
        .sort()
}

// Uebrige Detail-Felder fuer die Key/Value-Liste (ohne die gesondert dargestellten)
const SPECIAL_DETAIL_KEYS = new Set([
    'version', 'zone', 'changes', 'fanout', 'after_source', 'primary_outcome', 'history_incomplete',
    'history_truncated', 'change_count', 'change_keys', 'revert_of', 'forced', 'conflicts', 'skipped', 'auth',
    ...LEGACY_KEYS,
])
export function otherDetailFields(details) {
    if (!isObj(details)) return []
    return Object.keys(details)
        .filter((k) => !SPECIAL_DETAIL_KEYS.has(k))
        .sort()
        .map((key) => {
            const v = details[key]
            const value = v === null || v === undefined ? '–' : (typeof v === 'object' ? JSON.stringify(v) : String(v))
            return { key, value }
        })
}

// Zonenbezogener Eintrag mit Link in den Zonenverlauf (F7 §2.3)
export function zoneHistoryHref(entry) {
    if (!entry?.zone_name || !entry?.server_name || entry?.id == null) return null
    return `/zones/${encodeURIComponent(entry.server_name)}/${encodeURIComponent(entry.zone_name)}?tab=history&entry=${encodeURIComponent(entry.id)}`
}

// -------------------------------------------------------------------------------------------------
// Filter

// "Von" nach "Bis"? Werte: datetime-local-Strings (oder ISO). Leere Werte sind nie ungueltig.
export function isInvalidRange(from, to) {
    if (!from || !to) return false
    const a = parseLocal(from)
    const b = parseLocal(to)
    return Boolean(a && b && a.getTime() > b.getTime())
}

// datetime-local ('YYYY-MM-DDTHH:mm') ist Lokalzeit; ISO mit Zone wird direkt gelesen
function parseLocal(v) {
    const s = String(v).trim()
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(s)) {
        const d = new Date(s)
        return Number.isNaN(d.getTime()) ? null : d
    }
    return parseDateValue(s)
}

// Suchtext wird erst ab 2 Zeichen gesendet (Backend: 2..100)
export function effectiveSearch(q) {
    const s = String(q ?? '').trim()
    if (s.length < SEARCH_MIN_LENGTH) return ''
    return s.slice(0, SEARCH_MAX_LENGTH)
}

export const EMPTY_HISTORY_FILTERS = Object.freeze({
    resource_type: '', action: '', type: '', q: '', user_id: '', status: '', date_from: '', date_to: '',
})

export function hasActiveFilters(filters) {
    if (!isObj(filters)) return false
    return Object.values(filters).some((v) => v !== '' && v !== null && v !== undefined)
}

// Query-Parameter fuer GET .../history (F7 §3.2). recordFilter = { name, type } aus ?hname/&htype.
export function buildHistoryParams(filters = {}, { offset = 0, limit = HISTORY_DEFAULT_LIMIT, recordFilter = null } = {}) {
    const f = { ...EMPTY_HISTORY_FILTERS, ...(filters || {}) }
    const p = { limit, offset }
    if (f.resource_type) p.resource_type = f.resource_type
    if (f.action) p.action = String(f.action).toUpperCase()
    if (f.status === 'success' || f.status === 'error') p.status = f.status
    if (f.user_id !== '' && f.user_id !== null && Number.isInteger(Number(f.user_id))) p.user_id = Number(f.user_id)
    const q = effectiveSearch(f.q)
    if (q) p.q = q
    if (f.date_from) p.date_from = toIsoFromLocalInput(f.date_from) || undefined
    if (f.date_to) p.date_to = toIsoFromLocalInput(f.date_to) || undefined
    // Record-Filter (Zeilen-Aktion) hat Vorrang vor dem Typ-Select
    if (recordFilter?.name) p.name = recordFilter.name
    const type = recordFilter?.type || f.type
    if (type) p.type = String(type).toUpperCase()
    return p
}

// --- Admin-Audit-Log: Filter in den URL-Search-Params (F7 §2.5/§6.3)

export const AUDIT_FILTER_KEYS = Object.freeze([
    'action', 'resource_type', 'status', 'user_id', 'zone', 'server_name', 'date_from', 'date_to', 'q',
])

function toNonNegInt(v) {
    if (v === null || v === undefined || v === '') return null
    if (!/^\d+$/.test(String(v).trim())) return null
    const n = Number(v)
    return Number.isSafeInteger(n) && n >= 0 ? n : null
}

// URLSearchParams | Objekt -> { filters, offset, limit } (ungueltige Zahlen werden ignoriert)
export function parseAuditSearch(searchParams) {
    const get = (k) => {
        if (!searchParams) return ''
        if (typeof searchParams.get === 'function') return searchParams.get(k) ?? ''
        return searchParams[k] ?? ''
    }
    const filters = {}
    for (const k of AUDIT_FILTER_KEYS) filters[k] = String(get(k))
    if (filters.status !== 'success' && filters.status !== 'error') filters.status = ''
    if (filters.user_id && toNonNegInt(filters.user_id) === null) filters.user_id = ''
    const offset = toNonNegInt(get('offset')) ?? 0
    let limit = toNonNegInt(get('limit'))
    if (!AUDIT_PAGE_SIZES.includes(limit)) limit = AUDIT_DEFAULT_LIMIT
    return { filters, offset, limit }
}

// { filters, offset, limit } -> Objekt fuer setSearchParams (leere Werte und Defaults weglassen)
export function auditSearchObject({ filters = {}, offset = 0, limit = AUDIT_DEFAULT_LIMIT } = {}) {
    const out = {}
    for (const k of AUDIT_FILTER_KEYS) {
        const v = filters[k]
        if (v !== undefined && v !== null && String(v) !== '') out[k] = String(v)
    }
    if (offset > 0) out.offset = String(offset)
    if (limit !== AUDIT_DEFAULT_LIMIT) out.limit = String(limit)
    return out
}

// Query-Parameter fuer GET /audit-log bzw. /audit-log/export (F7 §3.6/§3.7)
export function buildAuditParams(filters = {}, { offset = 0, limit = AUDIT_DEFAULT_LIMIT } = {}) {
    const p = { limit, offset }
    if (filters.action) p.action = String(filters.action).toUpperCase()
    if (filters.resource_type) p.resource_type = filters.resource_type
    if (filters.status === 'success' || filters.status === 'error') p.status = filters.status
    if (toNonNegInt(filters.user_id) !== null) p.user_id = toNonNegInt(filters.user_id)
    if (filters.zone && String(filters.zone).trim()) p.zone = String(filters.zone).trim()
    if (filters.server_name) p.server_name = filters.server_name
    if (filters.date_from) p.date_from = toIsoFromLocalInput(filters.date_from) || undefined
    if (filters.date_to) p.date_to = toIsoFromLocalInput(filters.date_to) || undefined
    const q = effectiveSearch(filters.q)
    if (q) p.q = q
    return p
}

// -------------------------------------------------------------------------------------------------
// Aufbewahrung (AuditRetentionModal)

// Eingabe -> { ok, days } ; erlaubt: 0 (unbegrenzt) oder 7..3650, nur ganze Zahlen
export function validateRetention(input) {
    const s = String(input ?? '').trim()
    if (!/^\d+$/.test(s)) return { ok: false, days: null }
    const days = Number(s)
    if (days === 0) return { ok: true, days }
    if (days >= RETENTION_MIN_DAYS && days <= RETENTION_MAX_DAYS) return { ok: true, days }
    return { ok: false, days }
}

// Rueckfrage noetig? Neuer Wert > 0 und alter Wert 0 (unbegrenzt) oder groesser als der neue (F7 §2.5 Nr. 8)
export function isRetentionShortening(oldDays, newDays) {
    const o = Number(oldDays) || 0
    const n = Number(newDays) || 0
    return n > 0 && (o === 0 || o > n)
}

// -------------------------------------------------------------------------------------------------
// Rollback

// Plan-Eintraege mit Konflikt
export function conflictCount(preview) {
    return Array.isArray(preview?.plan) ? preview.plan.filter((i) => i?.conflict).length : 0
}

// Plan-Eintrag als Change fuer RrsetChangeDiff: before = aktueller Zustand, after = Ziel
export function planItemAsChange(item) {
    return { name: item?.name, type: item?.type, before: item?.current ?? null, after: item?.target ?? null }
}

// Zielserver: [{ server, status, write }] (write zuerst, dann alphabetisch)
export function rollbackTargets(preview) {
    const t = isObj(preview?.targets) ? preview.targets : {}
    return Object.keys(t)
        .map((server) => ({ server, status: String(t[server]), write: t[server] === 'write' }))
        .sort((a, b) => (Number(b.write) - Number(a.write)) || a.server.localeCompare(b.server))
}

// Kann der Bestaetigen-Button aktiv sein?
export function canConfirmRollback(preview, { force = false, busy = false } = {}) {
    if (!preview || busy || !preview.rollbackable) return false
    if (!Array.isArray(preview.plan) || preview.plan.length === 0) return false
    if (preview.plan.every((i) => i?.noop)) return true
    if (preview.has_conflicts && !force) return false
    return true
}

// Antwort von POST .../rollback -> { noop, revertId, failedServers, primaryOutcome }
export function rollbackOutcome(res) {
    const d = isObj(res?.details) ? res.details : {}
    return {
        noop: Boolean(d.noop),
        revertId: Number.isInteger(d.revert_audit_id) ? d.revert_audit_id : null,
        failedServers: fanoutErrorServers(d),
        primaryOutcome: notablePrimaryOutcome(d),
    }
}

// Ist ein API-Fehler ein Rollback-Konflikt (409)?
export function isRollbackConflict(err) {
    return err?.status === 409 || err?.code === 'rollback_conflict'
}
