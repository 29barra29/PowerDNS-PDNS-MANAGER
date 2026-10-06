// WS-F1: reine Helfer des Bulk-Editors (src/zoneDetail/bulkModel.js) und Vertrag mit dem Backend
// (Problem-Codes aus backend/app/services/bind_fragment.py haben i18n-Texte). Laeuft ohne Browser/React.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
    EMPTY_SELECTION, NON_SELECTABLE_TYPES, applyErrorIssues, applyOutcome, buildDeleteOps, buildDisabledOps,
    buildTextRequest, buildTtlOps, canApply, collapseKept, fanoutErrorList, filterExistingSelection, firstSelectedTtl,
    hasRemovals, headerState, isConflictError, needsLinePrefix, isSelectable, isTextEmpty, isValidBulkTtl, issueI18n, lineIssuesOf,
    lineOffsets, loadAllPrefill, nextModalId, opKey, recordKey, refreshRequest, relativeOwner, rrsetKeyOf,
    rrsetsToBindText, selectionStats, semanticsBanner, semanticsKey, textEditorPrefill, toggleSelection,
    touchesPtrTypes, ttlLabel, valueRows,
} from '../src/zoneDetail/bulkModel.js'
import bulkApi from '../src/api/bulk.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const Z = 'example.com.'
const rec = (name, type, content, extra = {}) => ({ name, type, content, ttl: 3600, disabled: false, ...extra })
const RECORDS = [
    rec(Z, 'SOA', 'ns1.example.com. hostmaster.example.com. 1 10800 3600 604800 3600'),
    rec(Z, 'NS', 'ns1.example.com.'),
    rec(Z, 'NS', 'ns2.example.com.'),
    rec('www.example.com.', 'A', '192.0.2.1', { ttl: 300 }),
    rec('www.example.com.', 'A', '192.0.2.2', { ttl: 300, disabled: true }),
    rec('mail.example.com.', 'MX', '10 mx.example.com.'),
    rec(Z, 'RRSIG', 'A 13 2 300 ...'),
    rec('a-very-long-hostname-that-exceeds-the-padding-limit-by-far.example.com.', 'TXT', '"x"'),
]
const key = (i) => recordKey(RECORDS[i])

test('Auswahl: SOA/DNSSEC nicht waehlbar, Statistik je RRset', () => {
    assert.equal(isSelectable(RECORDS[0]), false)
    assert.equal(isSelectable(RECORDS[6]), false)
    assert.equal(isSelectable(RECORDS[3]), true)
    assert.ok(NON_SELECTABLE_TYPES.has('TYPE65534'))
    const sel = new Set([key(3), key(0)])
    const s = selectionStats(RECORDS, sel)
    assert.deepEqual(s, { count: 1, rrsets: 1, values: 2, unselected: 1, rrsetKeys: [{ name: 'www.example.com.', type: 'A' }] })
    assert.equal(rrsetKeyOf('WWW.example.com.', 'a'), 'www.example.com.|A')
    assert.equal(firstSelectedTtl(RECORDS, sel), 300)
    assert.equal(firstSelectedTtl(RECORDS, EMPTY_SELECTION), 3600)
})

test('Auswahl: filterExistingSelection, toggleSelection, headerState', () => {
    const sel = new Set([key(3), JSON.stringify(['gone.example.com.', 'A', '192.0.2.9'])])
    const f = filterExistingSelection(sel, RECORDS)
    assert.deepEqual([...f], [key(3)])
    const same = new Set([key(3)])
    assert.equal(filterExistingSelection(same, RECORDS), same) // unveraendert -> gleiches Objekt
    const www = RECORDS.slice(3, 5)
    assert.equal(headerState(www, EMPTY_SELECTION), 'none')
    assert.equal(headerState(www, same), 'some')
    assert.equal(headerState(www, toggleSelection(same, [key(4)], true)), 'all')
    assert.deepEqual([...toggleSelection(same, [key(3)])], [])
    assert.equal(headerState([RECORDS[0]], EMPTY_SELECTION), 'none')
})

test('Sammel-Ops: Loeschen je Wert, TTL je RRset, Aktivieren/Deaktivieren je Wert', () => {
    const sel = new Set([key(3), key(4), key(5)])
    assert.deepEqual(buildDeleteOps(RECORDS, sel), {
        source: 'selection',
        delete: [
            { name: 'www.example.com.', type: 'A', content: '192.0.2.1' },
            { name: 'www.example.com.', type: 'A', content: '192.0.2.2' },
            { name: 'mail.example.com.', type: 'MX', content: '10 mx.example.com.' },
        ],
    })
    assert.deepEqual(buildTtlOps(RECORDS, sel, '600').set_ttl, [
        { name: 'www.example.com.', type: 'A', ttl: 600 },
        { name: 'mail.example.com.', type: 'MX', ttl: 600 },
    ])
    const dis = buildDisabledOps(RECORDS, new Set([key(3)]), true)
    assert.deepEqual(dis, { source: 'selection', set_disabled: [{ name: 'www.example.com.', type: 'A', content: '192.0.2.1', disabled: true }] })
    assert.ok(isValidBulkTtl('60') && isValidBulkTtl(604800) && !isValidBulkTtl('59') && !isValidBulkTtl('1e3') && !isValidBulkTtl(''))
})

test('BIND-Text: $ORIGIN, relative Owner, Typ-Reihenfolge, ;@disabled, ohne SOA/RRSIG', () => {
    assert.equal(relativeOwner('example.com.', Z), '@')
    assert.equal(relativeOwner('WWW.example.com', Z), 'www')
    assert.equal(relativeOwner('x.other.org.', Z), 'x.other.org.')
    const text = rrsetsToBindText(RECORDS, Z)
    const lines = text.trimEnd().split('\n')
    assert.equal(lines[0], '$ORIGIN example.com.')
    assert.ok(!text.includes('SOA') && !text.includes('RRSIG'))
    assert.equal(lines.length, 1 + 6)
    // NS vor A vor MX vor TXT (ALL_RECORD_TYPE_KEYS)
    assert.deepEqual(lines.slice(1).map((l) => l.replace(/^;@disabled /, '').split('\t')[3]), ['NS', 'NS', 'A', 'A', 'MX', 'TXT'])
    const disabled = lines.find((l) => l.startsWith(';@disabled '))
    assert.match(disabled, /^;@disabled www\s+\t300\tIN\tA\t192\.0\.2\.2$/)
    // Owner auf hoechstens 40 Zeichen gepolstert
    assert.equal(lines[1].split('\t')[0].length, 40)
    const pre = textEditorPrefill(RECORDS, Z, new Set([key(3)]))
    assert.equal(pre.rrsets, 1)
    assert.equal(pre.values, 2) // komplettes RRset, auch der nicht gewaehlte Wert
    assert.deepEqual(pre.scope, [{ name: 'www.example.com.', type: 'A' }])
    const all = loadAllPrefill(RECORDS, Z)
    assert.equal(all.rrsets, 4)
    assert.equal(all.values, 6)
})

test('Textfluss: leerer Text, Request, Zeilen-Offsets', () => {
    assert.equal(isTextEmpty('  \n; nur Kommentar\n'), true)
    assert.equal(isTextEmpty(';@disabled www A 192.0.2.1'), false)
    assert.equal(isTextEmpty('www A 192.0.2.1'), false)
    assert.deepEqual(buildTextRequest({ text: 'x', mode: 'merge', scope: [{ name: 'a.', type: 'A' }], defaultTtl: '600' }),
        { text: { content: 'x', mode: 'merge', scope: [], default_ttl: 600 } })
    assert.deepEqual(buildTextRequest({ text: 'x', mode: 'sync_scope', scope: [{ name: 'a.', type: 'A', extra: 1 }] }).text.scope,
        [{ name: 'a.', type: 'A' }])
    assert.equal(buildTextRequest({ text: 'x', mode: 'kaputt' }).text.mode, 'merge')
    const text = 'eins\r\nzwei\ndrei'
    assert.deepEqual(lineOffsets(text, 1), [0, 4])
    assert.deepEqual(lineOffsets(text, 2), [6, 10])
    assert.deepEqual(lineOffsets(text, 3), [11, 15])
    assert.deepEqual(lineOffsets(text, 9), [15, 15])
})

const change = (over = {}) => ({
    name: 'www.example.com.', type: 'A', op: 'update', semantics: ['replace'],
    before: { ttl: 300, records: [{ content: '192.0.2.1', disabled: false }, { content: '192.0.2.2', disabled: false }], comments: [] },
    after: { ttl: 60, records: [{ content: '192.0.2.2', disabled: true }, { content: '192.0.2.3', disabled: false }], comments: [] },
    added: ['192.0.2.3'], removed: ['192.0.2.1'], kept: ['192.0.2.2'], disabled_changed: ['192.0.2.2'],
    ttl_before: 300, ttl_after: 60, ...over,
})

test('Vorschau: Werte-Zeilen, Zusammenklappen, TTL, Badges', () => {
    const rows = valueRows(change())
    assert.deepEqual(rows.map((r) => [r.status, r.content, r.flip]), [
        ['removed', '192.0.2.1', null], ['added', '192.0.2.3', null], ['kept', '192.0.2.2', 'disabled'],
    ])
    assert.equal(ttlLabel(change()), '300 → 60')
    assert.equal(ttlLabel(change({ ttl_before: null, ttl_after: 60 })), '60')
    const many = Array.from({ length: 15 }, (_, i) => ({ content: `v${i}`, status: 'kept', disabled: false, flip: null }))
    const { rows: shown, hidden } = collapseKept([{ content: 'n', status: 'added' }, ...many])
    assert.equal(hidden, 5)
    assert.equal(shown.length, 11)
    assert.equal(semanticsKey('delete_value'), 'bulk.semDeleteValue')
    assert.equal(semanticsKey('x'), null)
    assert.equal(opKey('delete'), 'bulk.opDelete')
})

test('Vorschau: Banner, Loeschungen, Anwenden erlaubt, PTR-Typen', () => {
    const base = { source: 'text', mode: 'replace', blocking: false, ops: {}, changes: [change()], summary: { values_removed: 1, rrsets_deleted: 0 } }
    assert.equal(semanticsBanner(base), 'replace')
    assert.equal(semanticsBanner({ ...base, mode: 'sync_scope' }), 'sync')
    assert.equal(semanticsBanner({ ...base, mode: 'merge' }), 'merge')
    assert.equal(semanticsBanner({ ...base, source: 'selection' }), null)
    assert.equal(hasRemovals(base), true)
    assert.equal(canApply(base), false) // Pflicht-Checkbox fehlt
    assert.equal(canApply(base, { confirmRemoval: true }), true)
    assert.equal(canApply(base, { confirmRemoval: true, busy: true }), false)
    assert.equal(canApply({ ...base, blocking: true }, { confirmRemoval: true }), false)
    assert.equal(canApply({ ...base, ops: null }, { confirmRemoval: true }), false)
    assert.equal(canApply({ ...base, changes: [] }, { confirmRemoval: true }), false)
    assert.equal(canApply(base, { confirmRemoval: true, applyIssues: [{ code: 'cname_conflict' }] }), false)
    const noRemoval = { ...base, summary: { values_removed: 0, rrsets_deleted: 0 } }
    assert.equal(canApply(noRemoval), true)
    assert.equal(touchesPtrTypes(base), true)
    assert.equal(touchesPtrTypes({ changes: [change({ type: 'MX' })] }), false)
})

test('Probleme: i18n-Key, Zeilenfehler, LUA-Policy disabled', () => {
    const lines = lineIssuesOf({ blocking: true, issues: [
        { code: 'parse_error', severity: 'error', line: 2, params: { message: 'x' } },
        { code: 'ttl_conflict', severity: 'warning', line: 4, params: {} },
        { code: 'cname_conflict', severity: 'error', name: 'a.example.com.' },
    ] })
    assert.deepEqual(lines.map((i) => i.line), [2, 4])
    assert.deepEqual(lineIssuesOf({ blocking: true, issues: [{ code: 'cname_conflict', severity: 'error' }] }), [])
    assert.deepEqual(lineIssuesOf({ blocking: false, issues: [{ code: 'dnssec_skipped', severity: 'warning', line: 1 }] }), [])
    assert.deepEqual(issueI18n({ code: 'outside_zone', line: 3, params: { name: 'x.org.', zone: Z } }),
        { key: 'bulk.issue.outside_zone', values: { name: 'x.org', zone: Z, line: 3 } })
    assert.equal(issueI18n({ code: 'lua_forbidden', params: { policy: 'disabled' } }).key, 'bulk.issue.lua_forbidden_disabled')
    assert.equal(issueI18n({ code: 'lua_forbidden', params: { policy: 'admin' } }).key, 'bulk.issue.lua_forbidden')
    assert.equal(needsLinePrefix({ code: 'parse_error', line: 2 }), false)
    assert.equal(needsLinePrefix({ code: 'ttl_range', line: 2 }), true)
    assert.equal(needsLinePrefix({ code: 'ttl_range' }), false)
})

test('Anwenden: Ergebnis-Auswertung, 409/422, Vorschau neu laden ohne expected', () => {
    const details = { changed_rrsets: 3, audit_id: 7, fanout: { ns1: 'saved', ns2: 'error: weg', ns3: 'skipped (read-only)' }, peer_drift: { ns2: 1 } }
    assert.deepEqual(fanoutErrorList(details), [['ns2', 'error: weg']])
    const o = applyOutcome(details)
    assert.equal(o.count, 3)
    assert.equal(o.nothing, false)
    assert.equal(o.errorList, 'ns2: weg')
    assert.equal(o.driftServers, 'ns2')
    assert.equal(applyOutcome({ changed_rrsets: 0, audit_id: null, fanout: {} }).nothing, true)
    assert.equal(isConflictError({ status: 409, payload: { detail: { conflicts: [] } } }), true)
    assert.equal(isConflictError({ status: 409, payload: { detail: 'x' } }), false)
    assert.deepEqual(applyErrorIssues({ status: 422, payload: { detail: { issues: [{ code: 'a' }] } } }), [{ code: 'a' }])
    assert.deepEqual(applyErrorIssues({ status: 422, payload: { detail: [{ loc: [] }] } }), [])
    assert.deepEqual(refreshRequest({ ops: { source: 'selection', delete: [1], expected: [2], force: false } }),
        { ops: { source: 'selection', delete: [1] } })
    const textReq = { text: { content: 'x' } }
    assert.equal(refreshRequest(textReq), textReq)
    assert.ok(nextModalId() < nextModalId())
})

test('API-Modul: Pfade und Methoden', async () => {
    const calls = []
    const client = { request: (...args) => { calls.push(args); return Promise.resolve({}) } }
    await bulkApi.previewBulkRecords.call(client, 'ns 1', 'example.com.', { ops: {} })
    await bulkApi.bulkRecords.call(client, 'ns1', 'example.com.', { delete: [] })
    assert.deepEqual(calls[0].slice(0, 3), ['POST', '/records/ns%201/example.com./bulk/preview', { ops: {} }])
    assert.deepEqual(calls[1], ['POST', '/records/ns1/example.com./bulk', { delete: [] }])
})

test('Vertrag: jeder Problem-Code des Backends hat einen i18n-Text (de/en)', () => {
    const py = fs.readFileSync(path.join(HERE, '../../backend/app/services/bind_fragment.py'), 'utf-8')
    const block = py.slice(py.indexOf('ISSUE_MESSAGES'), py.indexOf('def issue('))
    const codes = [...block.matchAll(/^\s+"([a-z_]+)":/gm)].map((m) => m[1])
    assert.ok(codes.length >= 20, `Codes nicht gefunden: ${codes}`)
    for (const lang of ['de', 'en']) {
        const keys = new Set()
        const base = JSON.parse(fs.readFileSync(path.join(HERE, `../src/locales/${lang}.json`), 'utf-8'))
        for (const [k, v] of Object.entries(base.bulk?.issue || {})) if (typeof v === 'string') keys.add(`bulk.issue.${k}`)
        const frag = path.join(HERE, `../src/locales/fragments/f1.${lang}.json`)
        if (fs.existsSync(frag)) for (const k of Object.keys(JSON.parse(fs.readFileSync(frag, 'utf-8')).set || {})) keys.add(k)
        for (const code of [...codes, 'lua_forbidden_disabled']) {
            assert.ok(keys.has(`bulk.issue.${code}`), `${lang}: bulk.issue.${code} fehlt`)
        }
    }
})
