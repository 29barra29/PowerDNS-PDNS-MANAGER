// WS-F7-FE: reine Modell-Helfer fuer Zonenverlauf, Rollback-Dialog und Admin-Audit-Log
// (src/components/audit/historyModel.js) sowie die Pfade/Parameter des API-Moduls src/api/f7-fe.js gegen den
// API-Vertrag F7 §3 (Backend WS-F7-BE, parallel gebaut -> Ordner tests/integration, Plan Regel 3).
// Laeuft ohne Browser/React: node --test tests/integration/history.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
    actorInfo, auditSearchObject, buildAuditParams, buildHistoryParams, canConfirmRollback, changeKind,
    commentsChanged, conflictCount, diffRecords, displayName, effectiveSearch, entryChangeCount, entryChanges,
    entrySummary, entryVersion, fanoutErrorServers, fanoutRows, hasActiveFilters, hasDisabledFlip, historyFlags,
    isInvalidRange, isRetentionShortening, isRollbackConflict, legacyFields, needsFullLoad, notablePrimaryOutcome,
    otherDetailFields, parseAuditSearch, planItemAsChange, rollbackOutcome, rollbackTargets, tokenAuth, ttlChange,
    validateRetention, zoneHistoryHref, AUDIT_DEFAULT_LIMIT, EMPTY_HISTORY_FILTERS, HISTORY_DEFAULT_LIMIT,
} from '../../src/components/audit/historyModel.js'
import f7api, { overrides } from '../../src/api/f7-fe.js'

const snap = (ttl, ...records) => ({
    ttl,
    records: records.map((r) => (typeof r === 'string' ? { content: r, disabled: false } : r)),
    comments: [],
})

// ------------------------------------------------------------------ Diff

test('changeKind: created/deleted/modified', () => {
    assert.equal(changeKind({ before: null, after: snap(300, 'a') }), 'created')
    assert.equal(changeKind({ before: snap(300, 'a'), after: null }), 'deleted')
    assert.equal(changeKind({ before: snap(300, 'a'), after: snap(600, 'a') }), 'modified')
    assert.equal(changeKind({}), 'modified')
})

test('diffRecords: Mengen-Diff nach content, Disabled-Wechsel, stabile Reihenfolge', () => {
    const before = snap(300, '192.0.2.1', { content: '192.0.2.3', disabled: false }, '192.0.2.9')
    const after = snap(300, '192.0.2.1', '192.0.2.2', { content: '192.0.2.3', disabled: true })
    const rows = diffRecords(before, after)
    assert.deepEqual(rows.map((r) => `${r.status}:${r.content}`), [
        'removed:192.0.2.9', 'added:192.0.2.2', 'unchanged:192.0.2.1', 'unchanged:192.0.2.3',
    ])
    const flipped = rows.find((r) => r.content === '192.0.2.3')
    assert.equal(flipped.disabledBefore, false)
    assert.equal(flipped.disabledAfter, true)
    assert.equal(hasDisabledFlip(rows), true)
    assert.deepEqual(diffRecords(null, null), [])
    assert.deepEqual(diffRecords(null, snap(60, 'x')).map((r) => r.status), ['added'])
})

test('ttlChange und commentsChanged', () => {
    assert.deepEqual(ttlChange({ before: snap(3600, 'a'), after: snap(300, 'a') }), { from: 3600, to: 300, changed: true })
    assert.deepEqual(ttlChange({ before: null, after: snap(300, 'a') }), { from: null, to: 300, changed: false })
    const b = { ...snap(300, 'a'), comments: [{ content: 'x', account: 'y' }] }
    const a1 = { ...snap(300, 'a'), comments: [{ content: 'x', account: 'y', modified_at: 5 }] }
    const a2 = { ...snap(300, 'a'), comments: [{ content: 'z', account: 'y' }] }
    assert.equal(commentsChanged({ before: b, after: a1 }), false)
    assert.equal(commentsChanged({ before: b, after: a2 }), true)
    assert.equal(commentsChanged({ before: null, after: a2 }), false)
})

test('displayName: relativ zur Zone, Apex = @', () => {
    assert.equal(displayName('www.example.com.', 'example.com.'), 'www')
    assert.equal(displayName('example.com.', 'example.com.'), '@')
    assert.equal(displayName('other.net.', 'example.com.'), 'other.net')
    assert.equal(displayName('www.example.com.', ''), 'www.example.com')
})

// ------------------------------------------------------------------ Eintraege

const v2Entry = {
    id: 7,
    action: 'UPDATE',
    resource_type: 'record',
    user_id: 3,
    username: 'alice',
    version: 2,
    changes: [{ name: 'www.example.com.', type: 'A', before: snap(300, '192.0.2.1'), after: snap(300, '192.0.2.2') }],
    change_count: 1,
    changes_truncated: false,
    details: { version: 2, zone: 'example.com.', fanout: { ns2: 'error: timeout', ns1: 'saved', ns3: 'skipped (not loaded: api key unreadable)', ns4: 'skipped (read-only)' } },
}

test('entrySummary: eine Aenderung -> single, mehrere -> count, v1 -> none', () => {
    assert.deepEqual(entrySummary(v2Entry), { kind: 'single', name: 'www.example.com.', type: 'A', change: 'modified' })
    const many = { ...v2Entry, change_count: 25, changes: v2Entry.changes.concat(v2Entry.changes), changes_truncated: true }
    assert.deepEqual(entrySummary(many), { kind: 'count', count: 25 })
    assert.deepEqual(entrySummary({ id: 1, version: 1, changes: null, change_count: 0, details: { old: 'a', new: 'b' } }), { kind: 'none' })
})

test('entryChanges/entryChangeCount/entryVersion/needsFullLoad: Verlauf und Audit-Log', () => {
    // Audit-Log-Eintrag: changes stehen in details, gekuerzt -> details_truncated
    const auditEntry = { id: 9, details: { version: 2, changes: [v2Entry.changes[0]], change_count: 40 }, details_truncated: true }
    assert.equal(entryChanges(auditEntry).length, 1)
    assert.equal(entryChangeCount(auditEntry), 40)
    assert.equal(entryVersion(auditEntry), 2)
    assert.equal(needsFullLoad(auditEntry), true)
    assert.equal(entryVersion({ details: { old: 'x' } }), 1)
    assert.equal(entryVersion({ version: 1, details: { version: 2 } }), 1)
    assert.equal(needsFullLoad(v2Entry), false)
    assert.deepEqual(entryChanges({ details: null }), [])
})

test('actorInfo: Benutzer, geloeschter Benutzer, System', () => {
    assert.deepEqual(actorInfo({ user_id: 3, username: 'alice' }), { kind: 'user', name: 'alice', id: 3 })
    assert.deepEqual(actorInfo({ user_id: 4, username: null, actor_username: 'bob' }), { kind: 'deleted', id: 4, name: 'bob' })
    assert.deepEqual(actorInfo({ user_id: null }), { kind: 'system' })
    assert.deepEqual(actorInfo({ user_id: null, actor_username: 'acme-client' }), { kind: 'user', name: 'acme-client' })
})

test('tokenAuth und notablePrimaryOutcome [D3]', () => {
    assert.deepEqual(tokenAuth({ auth: { via: 'panel_token', token_name: 'ci' } }), { name: 'ci' })
    assert.equal(tokenAuth({ auth: { via: 'session' } }), null)
    assert.equal(tokenAuth(null), null)
    assert.equal(notablePrimaryOutcome({ primary_outcome: 'ok' }), null)
    assert.equal(notablePrimaryOutcome({ primary_outcome: 'verified_after_timeout' }), 'verified_after_timeout')
    assert.equal(notablePrimaryOutcome({ primary_outcome: 'unknown' }), 'unknown')
    assert.equal(notablePrimaryOutcome({ primary_outcome: 'failed' }), 'failed')
    assert.equal(notablePrimaryOutcome({ primary_outcome: 'weird' }), null)
    assert.equal(notablePrimaryOutcome(undefined), null)
})

test('historyFlags: incomplete, truncated mit change_keys, computed', () => {
    assert.deepEqual(historyFlags({ history_incomplete: true, after_source: 'reread' }),
        { incomplete: true, truncated: false, changeKeys: [], computedAfter: false })
    const f = historyFlags({ history_truncated: true, change_keys: [{ name: 'a.', type: 'A' }, 'kaputt'], after_source: 'computed' })
    assert.equal(f.truncated, true)
    assert.equal(f.computedAfter, true)
    assert.deepEqual(f.changeKeys, [{ name: 'a.', type: 'A' }])
})

test('legacyFields: v1-Felder in fester Reihenfolge, Listen zusammengefasst', () => {
    assert.deepEqual(legacyFields({ type: 'A', ttl: 300, records: ['192.0.2.1', '192.0.2.2'], zone: 'x.' }), [
        { key: 'type', value: 'A' }, { key: 'ttl', value: '300' }, { key: 'records', value: '192.0.2.1, 192.0.2.2' },
    ])
    assert.deepEqual(legacyFields({ content: null }), [{ key: 'content', value: '–' }])
    assert.deepEqual(legacyFields(null), [])
})

test('fanoutRows/fanoutErrorServers: Stufen und Sortierung', () => {
    assert.deepEqual(fanoutRows(v2Entry.details).map((r) => `${r.server}:${r.level}`), [
        'ns1:ok', 'ns2:error', 'ns3:warning', 'ns4:skipped',
    ])
    assert.deepEqual(fanoutErrorServers(v2Entry.details), ['ns2'])
    assert.deepEqual(fanoutRows(null), [])
})

test('otherDetailFields: bekannte v2-/Legacy-Felder ausgeblendet', () => {
    const rest = otherDetailFields({ version: 2, zone: 'a.', changes: [], fanout: {}, ip: '198.51.100.1', method: 'password', extra: { a: 1 } })
    assert.deepEqual(rest, [
        { key: 'extra', value: '{"a":1}' }, { key: 'ip', value: '198.51.100.1' }, { key: 'method', value: 'password' },
    ])
})

test('zoneHistoryHref: nur mit Zone, Server und ID', () => {
    assert.equal(zoneHistoryHref({ id: 5, zone_name: 'example.com.', server_name: 'ns 1' }),
        '/zones/ns%201/example.com.?tab=history&entry=5')
    assert.equal(zoneHistoryHref({ id: 5, zone_name: null, server_name: 'ns1' }), null)
    assert.equal(zoneHistoryHref({ id: 5, zone_name: 'a.', server_name: '' }), null)
})

// ------------------------------------------------------------------ Filter

test('isInvalidRange und effectiveSearch', () => {
    assert.equal(isInvalidRange('2026-10-02T10:00', '2026-10-01T10:00'), true)
    assert.equal(isInvalidRange('2026-10-01T10:00', '2026-10-01T10:00'), false)
    assert.equal(isInvalidRange('', '2026-10-01T10:00'), false)
    assert.equal(effectiveSearch(' a '), '')
    assert.equal(effectiveSearch(' ab '), 'ab')
    assert.equal(effectiveSearch('x'.repeat(150)).length, 100)
})

test('buildHistoryParams: leere Filter weg, Record-Filter hat Vorrang, Zeitraum als ISO-UTC', () => {
    assert.deepEqual(buildHistoryParams(EMPTY_HISTORY_FILTERS), { limit: HISTORY_DEFAULT_LIMIT, offset: 0 })
    const p = buildHistoryParams(
        { ...EMPTY_HISTORY_FILTERS, action: 'update', type: 'mx', q: 'w', user_id: '3', status: 'error', date_from: '2026-10-01T08:30' },
        { offset: 50, limit: 25, recordFilter: { name: 'www.example.com.', type: 'a' } },
    )
    assert.deepEqual(p, {
        limit: 25, offset: 50, action: 'UPDATE', status: 'error', user_id: 3,
        date_from: new Date('2026-10-01T08:30').toISOString(), name: 'www.example.com.', type: 'A',
    })
    assert.equal(hasActiveFilters(EMPTY_HISTORY_FILTERS), false)
    assert.equal(hasActiveFilters({ ...EMPTY_HISTORY_FILTERS, status: 'success' }), true)
})

test('parseAuditSearch/auditSearchObject: Rundlauf, ungueltige Zahlen ignoriert', () => {
    const sp = new URLSearchParams('action=CREATE&status=bogus&user_id=x1&offset=-5&limit=33&zone=example.com&q=www')
    const parsed = parseAuditSearch(sp)
    assert.equal(parsed.filters.action, 'CREATE')
    assert.equal(parsed.filters.status, '')
    assert.equal(parsed.filters.user_id, '')
    assert.equal(parsed.offset, 0)
    assert.equal(parsed.limit, AUDIT_DEFAULT_LIMIT)
    assert.deepEqual(auditSearchObject(parsed), { action: 'CREATE', zone: 'example.com', q: 'www' })
    const again = parseAuditSearch(new URLSearchParams(auditSearchObject({ filters: { status: 'error' }, offset: 100, limit: 200 })))
    assert.deepEqual([again.filters.status, again.offset, again.limit], ['error', 100, 200])
    assert.equal(parseAuditSearch(null).limit, AUDIT_DEFAULT_LIMIT)
})

test('buildAuditParams: Vertrag F7 §3.6 (zone, server_name, status, user_id, q ab 2 Zeichen)', () => {
    const p = buildAuditParams(
        { action: 'record_rollback', resource_type: 'record', status: 'success', user_id: '12', zone: ' example.com ', server_name: 'ns1', q: 'a' },
        { offset: 0, limit: 50 },
    )
    assert.deepEqual(p, {
        limit: 50, offset: 0, action: 'RECORD_ROLLBACK', resource_type: 'record', status: 'success', user_id: 12,
        zone: 'example.com', server_name: 'ns1',
    })
})

// ------------------------------------------------------------------ Aufbewahrung

test('validateRetention und isRetentionShortening (F7 §2.5 Nr. 8)', () => {
    assert.deepEqual(validateRetention('0'), { ok: true, days: 0 })
    assert.deepEqual(validateRetention('7'), { ok: true, days: 7 })
    assert.deepEqual(validateRetention('3650'), { ok: true, days: 3650 })
    assert.equal(validateRetention('6').ok, false)
    assert.equal(validateRetention('3651').ok, false)
    assert.equal(validateRetention('1.5').ok, false)
    assert.equal(validateRetention('').ok, false)
    assert.equal(isRetentionShortening(0, 30), true)
    assert.equal(isRetentionShortening(90, 30), true)
    assert.equal(isRetentionShortening(30, 90), false)
    assert.equal(isRetentionShortening(30, 0), false)
})

// ------------------------------------------------------------------ Rollback

const preview = {
    audit_id: 7,
    rollbackable: true,
    has_conflicts: true,
    plan: [
        { name: 'www.example.com.', type: 'A', changetype: 'REPLACE', current: snap(300, '192.0.2.5'), expected: snap(300, '192.0.2.2'), target: snap(300, '192.0.2.1'), conflict: true, noop: false },
        { name: 'mail.example.com.', type: 'A', changetype: 'DELETE', current: snap(300, '192.0.2.7'), expected: snap(300, '192.0.2.7'), target: null, conflict: false, noop: false },
    ],
    skipped: [{ name: 'example.com.', type: 'SOA', reason: 'soa' }],
    targets: { ns2: 'skipped (read-only)', ns1: 'write', ns0: 'write' },
}

test('conflictCount, planItemAsChange, rollbackTargets', () => {
    assert.equal(conflictCount(preview), 1)
    assert.equal(conflictCount(null), 0)
    const c = planItemAsChange(preview.plan[1])
    assert.equal(changeKind(c), 'deleted')
    assert.deepEqual(rollbackTargets(preview).map((x) => `${x.server}:${x.write}`), ['ns0:true', 'ns1:true', 'ns2:false'])
})

test('canConfirmRollback: Konflikt nur mit force, blockiert/busy nie', () => {
    assert.equal(canConfirmRollback(preview, { force: false }), false)
    assert.equal(canConfirmRollback(preview, { force: true }), true)
    assert.equal(canConfirmRollback(preview, { force: true, busy: true }), false)
    assert.equal(canConfirmRollback({ ...preview, has_conflicts: false }, {}), true)
    assert.equal(canConfirmRollback({ ...preview, rollbackable: false }, { force: true }), false)
    assert.equal(canConfirmRollback({ ...preview, plan: [] }, { force: true }), false)
    // nur Noops: kein Konflikt-Gate (Backend antwortet mit noop)
    assert.equal(canConfirmRollback({ ...preview, plan: [{ ...preview.plan[0], noop: true }] }, {}), true)
})

test('rollbackOutcome und isRollbackConflict (F7 §3.5)', () => {
    assert.deepEqual(rollbackOutcome({ message: 'x', details: { noop: true, skipped: [] } }),
        { noop: true, revertId: null, failedServers: [], primaryOutcome: null })
    assert.deepEqual(
        rollbackOutcome({ details: { revert_audit_id: 42, fanout: { ns1: 'saved', ns2: 'error: boom' }, primary_outcome: 'verified_after_timeout' } }),
        { noop: false, revertId: 42, failedServers: ['ns2'], primaryOutcome: 'verified_after_timeout' },
    )
    assert.equal(isRollbackConflict({ status: 409 }), true)
    assert.equal(isRollbackConflict({ status: 422, code: 'rollback_conflict' }), true)
    assert.equal(isRollbackConflict({ status: 422, code: 'legacy_format' }), false)
})

// ------------------------------------------------------------------ API-Modul gegen den Vertrag F7 §3

function fakeClient() {
    const calls = []
    return {
        calls,
        request(method, path, data = null, opts = {}) {
            calls.push({ method, path, data, signal: opts.signal })
            return Promise.resolve({ ok: true })
        },
    }
}

test('api/f7-fe.js: nur getAuditLog als Ueberschreibung, Pfade und Query wie F7 §3', async () => {
    assert.deepEqual(overrides, ['getAuditLog'])
    const c = fakeClient()
    const call = (name, ...args) => f7api[name].apply(c, args)
    const ctrl = new AbortController()

    await call('getAuditLog', 200)
    await call('getAuditLog', { limit: 50, offset: 100, zone: 'example.com', status: 'error', q: '', signal: ctrl.signal })
    await call('getAuditLogEntry', 12)
    await call('getAuditSettings')
    await call('updateAuditSettings', { retention_days: 30 })
    await call('getZoneHistory', 'ns1', 'example.com.', { limit: 25, offset: 0, name: 'www.example.com.', type: 'A' })
    await call('getZoneHistoryEntry', 'ns1', 'example.com.', 7)
    await call('getRollbackPreview', 'ns 1', 'example.com.', 7)
    await call('rollbackZoneChange', 'ns1', 'example.com.', 7, { force: true })
    await call('rollbackZoneChange', 'ns1', 'example.com.', 8)

    assert.deepEqual(c.calls.map((x) => `${x.method} ${x.path}`), [
        'GET /audit-log?limit=200',
        'GET /audit-log?limit=50&offset=100&zone=example.com&status=error',
        'GET /audit-log/12',
        'GET /audit-log/settings',
        'PUT /audit-log/settings',
        'GET /zones/ns1/example.com./history?limit=25&offset=0&name=www.example.com.&type=A',
        'GET /zones/ns1/example.com./history/7',
        'GET /zones/ns%201/example.com./history/7/rollback-preview',
        'POST /zones/ns1/example.com./history/7/rollback',
        'POST /zones/ns1/example.com./history/8/rollback',
    ])
    assert.equal(c.calls[1].signal, ctrl.signal)
    assert.deepEqual(c.calls[4].data, { retention_days: 30 })
    assert.deepEqual(c.calls[8].data, { force: true })
    assert.deepEqual(c.calls[9].data, { force: false })
    assert.equal(typeof f7api.exportAuditLogCsv, 'function')
    assert.equal('downloadAuditLogCsv' in f7api, false, 'Kernmethode downloadAuditLogCsv darf nicht ueberschrieben werden')
})
