// Bulk-Editor (F1 6.2): reine Funktionen fuer Auswahl, Sammel-Ops, BIND-Text und Vorschau-Auswertung.
// Ohne React/i18n-Import, damit per `node --test` pruefbar (tests/bulkModel.test.mjs).
//
// Vertrag mit dem Backend (POST /records/{server}/{zone}/bulk/preview und /bulk, Spec F1 3.1-3.3):
//   ops = { source, create[], delete[], merge[], set_ttl[], set_disabled[], default_ttl, expected[], force, mode,
//           manage_ptr }
//   Vorschau = { zone, server, source, mode, blocking, issues[], changes[], summary, ops|null, peers[], skipped_servers }
import { ALL_RECORD_TYPE_KEYS } from '../constants/dnsRecordTypes.js'
import { fanoutWarnings, formatFanoutWarnings } from '../lib/fanout.js'
import { ptrMessages, summarizePtr } from '../lib/ptrResults.js'

// Nicht auswaehlbar: SOA (nur Einzeleditor) und die von PowerDNS erzeugten DNSSEC-Typen.
export const NON_SELECTABLE_TYPES = new Set(['SOA', 'RRSIG', 'NSEC', 'NSEC3', 'NSEC3PARAM', 'TYPE65534'])
// PTR-Pflege betrifft nur Vorwaerts-Records dieser Typen (F11).
export const PTR_FORWARD_TYPES = new Set(['A', 'AAAA'])
export const TTL_MIN = 60
export const TTL_MAX = 604800
export const PREVIEW_ROW_LIMIT = 200
export const KEPT_COLLAPSE_LIMIT = 10
export const TEXT_MODES = ['merge', 'replace', 'sync_scope']
const OWNER_PAD_MAX = 40

// Leere Auswahl (geteilter Slot-Zustand 'bulk.selected'); nie veraendern – Auswahl wird immer neu erzeugt.
export const EMPTY_SELECTION = new Set()

// Fortlaufende id je Oeffnen des Bulk-Modals (key -> frischer Zustand ohne Reset-Effekt)
let modalSeq = 0
export function nextModalId() {
    modalSeq += 1
    return modalSeq
}

const lower = (s) => String(s ?? '').toLowerCase()
const upper = (s) => String(s ?? '').toUpperCase()

export function recordKey(r) {
    return JSON.stringify([r?.name, r?.type, r?.content])
}

export function rrsetKeyOf(name, type) {
    return `${lower(name)}|${upper(type)}`
}

export function isSelectable(r) {
    return !!r && !NON_SELECTABLE_TYPES.has(upper(r.type))
}

// Auswahl umschalten (neues Set). on=true/false erzwingt den Zustand.
export function toggleSelection(selectedSet, keys, on) {
    const out = new Set(selectedSet instanceof Set ? selectedSet : [])
    for (const k of keys) {
        const want = on === undefined ? !out.has(k) : !!on
        if (want) out.add(k)
        else out.delete(k)
    }
    return out
}

// Kopf-Checkbox einer Typ-Tabelle: 'all' | 'some' | 'none' (nur auswaehlbare Zeilen zaehlen)
export function headerState(rows, selectedSet) {
    const keys = (rows || []).filter(isSelectable).map(recordKey)
    if (keys.length === 0) return 'none'
    const n = keys.filter((k) => selectedSet instanceof Set && selectedSet.has(k)).length
    if (n === 0) return 'none'
    return n === keys.length ? 'all' : 'some'
}

// Auswahl auf noch vorhandene Records reduzieren. Unveraendert -> dasselbe Set (kein neuer Zustand).
export function filterExistingSelection(selectedSet, records) {
    const sel = selectedSet instanceof Set ? selectedSet : EMPTY_SELECTION
    if (sel.size === 0) return sel
    const existing = new Set((records || []).filter(isSelectable).map(recordKey))
    let changed = false
    const out = new Set()
    for (const k of sel) {
        if (existing.has(k)) out.add(k)
        else changed = true
    }
    return changed ? out : sel
}

export function selectedRecords(records, selectedSet) {
    const sel = selectedSet instanceof Set ? selectedSet : new Set()
    return (records || []).filter((r) => isSelectable(r) && sel.has(recordKey(r)))
}

// Betroffene RRsets der Auswahl: { count, rrsets, values, unselected, rrsetKeys: [{ name, type }] }
// values = alle Werte dieser RRsets, unselected = davon nicht gewaehlt (TTL gilt fuer das ganze RRset).
export function selectionStats(records, selectedSet) {
    const chosen = selectedRecords(records, selectedSet)
    const keys = new Map()
    for (const r of chosen) {
        const k = rrsetKeyOf(r.name, r.type)
        if (!keys.has(k)) keys.set(k, { name: r.name, type: upper(r.type) })
    }
    let values = 0
    for (const r of records || []) if (keys.has(rrsetKeyOf(r.name, r.type))) values += 1
    return {
        count: chosen.length,
        rrsets: keys.size,
        values,
        unselected: Math.max(0, values - chosen.length),
        rrsetKeys: Array.from(keys.values()),
    }
}

export function buildDeleteOps(records, selectedSet) {
    return {
        source: 'selection',
        delete: selectedRecords(records, selectedSet).map((r) => ({ name: r.name, type: upper(r.type), content: r.content })),
    }
}

export function buildTtlOps(records, selectedSet, ttl) {
    const n = Number(ttl)
    return {
        source: 'selection',
        set_ttl: selectionStats(records, selectedSet).rrsetKeys.map((k) => ({ name: k.name, type: k.type, ttl: n })),
    }
}

export function buildDisabledOps(records, selectedSet, disabled) {
    return {
        source: 'selection',
        set_disabled: selectedRecords(records, selectedSet).map((r) => ({
            name: r.name, type: upper(r.type), content: r.content, disabled: !!disabled,
        })),
    }
}

// Erste ausgewaehlte TTL (Vorgabe des TTL-Dialogs), sonst 3600.
export function firstSelectedTtl(records, selectedSet) {
    const r = selectedRecords(records, selectedSet)[0]
    const n = Number(r?.ttl)
    return Number.isFinite(n) && n > 0 ? n : 3600
}

export function isValidBulkTtl(v) {
    const s = String(v ?? '').trim()
    if (!/^\d+$/.test(s)) return false
    const n = parseInt(s, 10)
    return n >= TTL_MIN && n <= TTL_MAX
}

// '@' am Apex, sonst Name ohne Zonen-Suffix; ausserhalb der Zone absolut (mit Punkt).
export function relativeOwner(name, zoneKey) {
    const n = lower(name)
    const z = lower(zoneKey)
    const fq = n.endsWith('.') ? n : `${n}.`
    if (!z) return fq
    if (fq === z) return '@'
    if (fq.endsWith(`.${z}`)) return fq.slice(0, fq.length - z.length - 1)
    return fq
}

function typeRank(type) {
    const i = ALL_RECORD_TYPE_KEYS.indexOf(upper(type))
    return i === -1 ? ALL_RECORD_TYPE_KEYS.length : i
}

// Records -> BIND-Text (F1 2.4 Punkt 2). rrsetKeys: Set von rrsetKeyOf-Schluesseln oder null (= alle auswaehlbaren).
// Erste Zeile $ORIGIN, dann je Wert "<owner>\t<ttl>\tIN\t<TYPE>\t<content>"; deaktivierte Werte mit ";@disabled ".
export function rrsetsToBindText(records, zoneKey, rrsetKeys = null) {
    const z = lower(zoneKey)
    const rows = (records || [])
        .filter(isSelectable)
        .filter((r) => !rrsetKeys || rrsetKeys.has(rrsetKeyOf(r.name, r.type)))
        .map((r) => ({ owner: relativeOwner(r.name, z), name: lower(r.name), type: upper(r.type), r }))
    rows.sort((a, b) => (typeRank(a.type) - typeRank(b.type))
        || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)
        || (a.type < b.type ? -1 : a.type > b.type ? 1 : 0))
    const width = Math.min(OWNER_PAD_MAX, rows.reduce((m, row) => Math.max(m, row.owner.length), 0))
    const lines = [`$ORIGIN ${z || '.'}`]
    for (const row of rows) {
        const line = `${row.owner.padEnd(width)}\t${row.r.ttl}\tIN\t${row.type}\t${row.r.content}`
        lines.push(row.r.disabled ? `;@disabled ${line}` : line)
    }
    return `${lines.join('\n')}\n`
}

// RRset-Schluessel (Set) und Scope-Liste fuer den Modus sync_scope
export function scopeFromRecords(records, rrsetKeys = null) {
    const seen = new Map()
    for (const r of records || []) {
        if (!isSelectable(r)) continue
        const k = rrsetKeyOf(r.name, r.type)
        if (rrsetKeys && !rrsetKeys.has(k)) continue
        if (!seen.has(k)) seen.set(k, { name: r.name, type: upper(r.type) })
    }
    return Array.from(seen.values())
}

export function countValues(records, rrsetKeys = null) {
    return (records || []).filter((r) => isSelectable(r) && (!rrsetKeys || rrsetKeys.has(rrsetKeyOf(r.name, r.type)))).length
}

// Vorbefuellung "Als Text bearbeiten": alle RRsets mit mindestens einem gewaehlten Wert (komplette RRsets).
export function textEditorPrefill(records, zoneKey, selectedSet) {
    const keys = new Set(selectionStats(records, selectedSet).rrsetKeys.map((k) => rrsetKeyOf(k.name, k.type)))
    return {
        text: rrsetsToBindText(records, zoneKey, keys),
        scope: scopeFromRecords(records, keys),
        rrsets: keys.size,
        values: countValues(records, keys),
    }
}

// "Alle Records laden": alle auswaehlbaren RRsets der Zone
export function loadAllPrefill(records, zoneKey) {
    const scope = scopeFromRecords(records, null)
    return { text: rrsetsToBindText(records, zoneKey, null), scope, rrsets: scope.length, values: countValues(records, null) }
}

// Text ohne Record-Zeilen (nur Leerraum und Kommentare, keine ;@disabled-Zeilen)?
export function isTextEmpty(text) {
    for (const raw of String(text ?? '').split(/\r?\n/)) {
        const line = raw.trim()
        if (!line) continue
        if (/^;@disabled\s+\S/i.test(line)) return false
        if (line.startsWith(';')) continue
        return false
    }
    return true
}

export function buildTextRequest({ text, mode, scope, defaultTtl }) {
    const m = TEXT_MODES.includes(mode) ? mode : 'merge'
    return {
        text: {
            content: String(text ?? ''),
            mode: m,
            scope: m === 'sync_scope' ? (scope || []).map((k) => ({ name: k.name, type: k.type })) : [],
            default_ttl: Number(defaultTtl) || 3600,
        },
    }
}

// Zeichen-Offsets [start, end] der 1-basierten Zeile `line` (fuer setSelectionRange).
export function lineOffsets(text, line) {
    const s = String(text ?? '')
    const target = Math.max(1, Number(line) || 1)
    let start = 0
    for (let i = 1; i < target; i += 1) {
        const nl = s.indexOf('\n', start)
        if (nl === -1) return [s.length, s.length]
        start = nl + 1
    }
    let end = s.indexOf('\n', start)
    if (end === -1) end = s.length
    if (end > start && s[end - 1] === '\r') end -= 1
    return [start, end]
}

/* ----- Vorschau ---------------------------------------------------------------------------------- */

export function previewIssues(preview) {
    return Array.isArray(preview?.issues) ? preview.issues : []
}

export function blockingIssues(preview) {
    return previewIssues(preview).filter((i) => i?.severity === 'error')
}

export function warningIssues(preview) {
    return previewIssues(preview).filter((i) => i?.severity !== 'error')
}

// Codes, deren Text die Zeilennummer schon enthaelt (sonst stellt die Fehlerliste "Zeile N:" voran)
export const LINE_TEXT_CODES = new Set(['parse_error', 'directive_unsupported', 'class_unsupported', 'dnssec_skipped', 'owner_missing'])

export function needsLinePrefix(issue) {
    return Number.isInteger(issue?.line) && !LINE_TEXT_CODES.has(String(issue?.code || ''))
}

// Zeilenfehler des Textflusses (Modal bleibt im Schritt "text")
export function lineIssuesOf(preview) {
    if (!preview?.blocking) return []
    const withLine = previewIssues(preview).filter((i) => Number.isInteger(i?.line))
    return withLine.some((i) => i.severity === 'error') ? withLine : []
}

export function previewChanges(preview) {
    return Array.isArray(preview?.changes) ? preview.changes : []
}

export function hasRemovals(preview) {
    const s = preview?.summary || {}
    return Number(s.values_removed) > 0 || Number(s.rrsets_deleted) > 0
}

export function canApply(preview, { confirmRemoval = false, busy = false, applyIssues = [] } = {}) {
    if (!preview || busy || preview.blocking || !preview.ops) return false
    if (previewChanges(preview).length === 0) return false
    if ((applyIssues || []).length > 0) return false
    if (hasRemovals(preview) && !confirmRemoval) return false
    return true
}

// Semantik-Banner je Quelle/Modus: 'replace' | 'sync' | 'merge' | null (Auswahl)
export function semanticsBanner(preview) {
    if (!preview || preview.source === 'selection') return null
    if (preview.mode === 'replace') return 'replace'
    if (preview.mode === 'sync_scope') return 'sync'
    if (preview.mode === 'merge') return 'merge'
    return null
}

const SEMANTICS_KEYS = {
    replace: 'bulk.semReplace',
    merge: 'bulk.semMerge',
    delete_rrset: 'bulk.semDeleteRrset',
    delete_value: 'bulk.semDeleteValue',
    ttl: 'bulk.semTtl',
    disabled: 'bulk.semDisabled',
}
const OP_KEYS = { create: 'bulk.opCreate', update: 'bulk.opUpdate', delete: 'bulk.opDelete' }

export function semanticsKey(s) {
    return SEMANTICS_KEYS[s] || null
}

export function opKey(op) {
    return OP_KEYS[op] || 'bulk.opUpdate'
}

// i18n eines Problems: { key, values } – t(key, { ...values, defaultValue: issue.message })
export function issueI18n(issue) {
    const params = issue && typeof issue.params === 'object' && issue.params ? issue.params : {}
    let code = String(issue?.code || '')
    if (code === 'lua_forbidden' && params.policy === 'disabled') code = 'lua_forbidden_disabled'
    const values = { ...params }
    if (issue?.line != null) values.line = issue.line
    if (issue?.name && values.name == null) values.name = issue.name
    if (issue?.type && values.type == null) values.type = issue.type
    if (typeof values.name === 'string') values.name = values.name.replace(/\.$/, '')
    return { key: `bulk.issue.${code}`, values }
}

// Werte-Zeilen einer Aenderung: [{ content, status: 'removed'|'added'|'kept', disabled, flip: 'disabled'|'enabled'|null }]
export function valueRows(change) {
    const before = Array.isArray(change?.before?.records) ? change.before.records : []
    const after = Array.isArray(change?.after?.records) ? change.after.records : []
    const removed = new Set(change?.removed || [])
    const added = new Set(change?.added || [])
    const flipped = new Set(change?.disabled_changed || [])
    const rows = []
    for (const r of before) if (removed.has(r.content)) rows.push({ content: r.content, status: 'removed', disabled: !!r.disabled, flip: null })
    for (const r of after) {
        if (added.has(r.content)) rows.push({ content: r.content, status: 'added', disabled: !!r.disabled, flip: null })
    }
    for (const r of after) {
        if (added.has(r.content)) continue
        rows.push({
            content: r.content,
            status: 'kept',
            disabled: !!r.disabled,
            flip: flipped.has(r.content) ? (r.disabled ? 'disabled' : 'enabled') : null,
        })
    }
    return rows
}

// Kept-Zeilen ab KEPT_COLLAPSE_LIMIT zusammenklappen (geaenderte/Flip-Zeilen bleiben sichtbar).
export function collapseKept(rows, limit = KEPT_COLLAPSE_LIMIT) {
    const plainKept = rows.filter((r) => r.status === 'kept' && !r.flip)
    if (plainKept.length <= limit) return { rows, hidden: 0 }
    const hide = new Set(plainKept.slice(limit))
    return { rows: rows.filter((r) => !hide.has(r)), hidden: hide.size }
}

export function ttlLabel(change) {
    const b = change?.ttl_before
    const a = change?.ttl_after
    if (b != null && a != null && b !== a) return `${b} → ${a}`
    return String(a ?? b ?? '')
}

// Vorschau-Request mit derselben Bedeutung erneut senden (409): Auswahl ohne expected/force, Text unveraendert.
export function refreshRequest(lastRequest) {
    if (!lastRequest) return null
    if (lastRequest.ops) {
        const { expected, force, ...rest } = lastRequest.ops // eslint-disable-line no-unused-vars
        return { ops: rest }
    }
    return lastRequest
}

// Antwort von POST /bulk -> Banner-Daten der Zonenansicht
export function fanoutErrorList(details) {
    return Object.entries(details?.fanout || {}).filter(([, v]) => String(v).startsWith('error:'))
}

export function applyOutcome(details) {
    const errors = fanoutErrorList(details)
    const drift = Object.keys(details?.peer_drift || {})
    return {
        count: Number(details?.changed_rrsets) || 0,
        nothing: !details?.audit_id && (Number(details?.changed_rrsets) || 0) === 0,
        errorList: errors.map(([srv, v]) => `${srv}: ${String(v).slice('error:'.length).trim()}`).join('; '),
        hasErrors: errors.length > 0,
        driftServers: drift.join(', '),
        hasDrift: drift.length > 0,
    }
}

// Betrifft die Vorschau A/AAAA-RRsets? (PTR-Option nur dann anzeigen)
export function touchesPtrTypes(preview) {
    return previewChanges(preview).some((c) => PTR_FORWARD_TYPES.has(upper(c?.type)))
}

// Wirksamer Wert der PTR-Checkbox: gemerkte Auswahl (Zone/Browser) oder Admin-Default aus dem /ptr/config-Cache.
// Anzeige und gesendeter Wert kommen beide hieraus, damit die Checkbox nie vom angewendeten Wert abweicht.
export function effectivePtrChoice(choice, ptrConfig) {
    return typeof choice === 'boolean' ? choice : !!ptrConfig?.auto_default
}

// Body fuer POST /bulk: Body der Vorschau unveraendert plus immer ein explizites `manage_ptr` (Spec F11 §12 Nr. 14).
// Ohne sichtbare PTR-Option (keine A/AAAA-Aenderung) -> false; das Backend loest dann nie den Admin-Default auf.
export function bulkApplyBody(preview, { ptrVisible = false, managePtr = false } = {}) {
    return { ...(preview?.ops || {}), manage_ptr: ptrVisible ? !!managePtr : false }
}

// Banner-Texte nach 200 von POST /bulk (F1 2.5, F11 §2.6 "Ergebnisanzeige wie 2.5/4"):
//   success: Erfolgsmeldung + gesetzte/entfernte PTRs (ptr.resultSet/ptr.resultRemoved)
//   error:   Fan-out-Fehler einzelner Peers
//   warning: Peer-Drift, nicht geladene Server und PTR-Probleme je IP mit Begruendung (Titel ptr.warningTitle)
export function appliedBanners(t, details) {
    const d = details || {}
    const outcome = applyOutcome(d)
    const ptr = ptrMessages(t, summarizePtr(d.ptr))
    let success = outcome.nothing ? t('bulk.nothingApplied') : t('bulk.applied', { count: outcome.count })
    if (ptr.success) success = `${success} ${ptr.success}`
    const warnings = []
    if (outcome.hasDrift) warnings.push(t('bulk.peerDrift', { servers: outcome.driftServers }))
    const notLoaded = fanoutWarnings(d)
    if (notLoaded.length) warnings.push(t('zoneDetail.fanoutNotLoaded', { servers: formatFanoutWarnings(notLoaded) }))
    if (ptr.warning) warnings.push(ptr.warning)
    return {
        success,
        error: outcome.hasErrors ? t('bulk.fanoutErrors', { list: outcome.errorList }) : '',
        warning: warnings.join('\n'),
    }
}

// Konflikt-Antwort (409) von POST /bulk
export function isConflictError(err) {
    return err?.status === 409 && Array.isArray(err?.payload?.detail?.conflicts)
}

// 422 mit Problemliste von POST /bulk
export function applyErrorIssues(err) {
    const issues = err?.payload?.detail?.issues
    return err?.status === 422 && Array.isArray(issues) ? issues : []
}
