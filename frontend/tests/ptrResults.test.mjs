// WS-F9F11-FE: Auswertung von details.ptr (lib/ptrResults.js) und gemerkte PTR-Auswahl (lib/ptrPreference.js).
import test from 'node:test'
import assert from 'node:assert/strict'
import {
    applyPtrMessages, extractPtrList, formatPtrLine, lookupCandidates, lookupLine, ptrMessages, ptrReasonKey, summarizePtr,
} from '../src/lib/ptrResults.js'
import {
    MAX_REMEMBERED_ZONES, PTR_PREF_KEY, clearManagePtr, effectiveManagePtr, getDefault, getManagePtr, getPtrConfig,
    isPtrType, loadPtrConfig, resetPtrConfigForTests, setDefaultInCache, setManagePtr, setPtrConfig, subscribePtrConfig,
    zoneKeyOf,
} from '../src/lib/ptrPreference.js'

// Test-"t": Key + Werte sichtbar machen, damit Regeln ohne echte Locale pruefbar sind
const t = (key, values = {}) => {
    const v = Object.entries(values).map(([k, x]) => `${k}=${x}`).join(',')
    return v ? `${key}[${v}]` : key
}

const R = (over) => ({
    ip: '192.0.2.10', ptr: '10.2.0.192.in-addr.arpa.', zone: '2.0.192.in-addr.arpa.', target: 'host.example.com.',
    op: 'set', action: 'set', reason: null, existing: null, classless_zone: null, fanout: { ns1: 'saved' }, detail: null,
    ...over,
})

test('summarizePtr: set/removed -> ok, unchanged und stille Gruende -> nichts', () => {
    const s = summarizePtr([
        R({}),
        R({ op: 'remove', action: 'removed', ip: '192.0.2.9', ptr: '9.2.0.192.in-addr.arpa.' }),
        R({ action: 'unchanged' }),
        R({ action: 'skipped', reason: 'no_reverse_zone', zone: null }),
        R({ action: 'skipped', reason: 'disabled' }),
        R({ action: 'skipped', reason: 'wildcard' }),
    ])
    assert.equal(s.ok.length, 2)
    assert.equal(s.warnings.length, 0)
    assert.equal(s.errors.length, 0)
    assert.equal(s.lines.length, 2)
    assert.equal(s.hasOutput, true)
    assert.equal(s.hasProblems, false)
    assert.equal(formatPtrLine(t, s.ok[0]), 'ptr.resultSet[ptr=10.2.0.192.in-addr.arpa,target=host.example.com,ip=192.0.2.10]')
    assert.equal(formatPtrLine(t, s.ok[1]), 'ptr.resultRemoved[ptr=9.2.0.192.in-addr.arpa,ip=192.0.2.9]')
})

test('summarizePtr: Warn-Gruende und Fehler', () => {
    const s = summarizePtr([
        R({ action: 'skipped', reason: 'conflict', existing: ['other.example.com.', 'x.example.com.'] }),
        R({ action: 'skipped', reason: 'classless', classless_zone: '0/26.2.0.192.in-addr.arpa.' }),
        R({ action: 'skipped', reason: 'forbidden' }),
        R({ action: 'skipped', reason: 'other_target', existing: ['old.example.com.'] }),
        R({ action: 'skipped', reason: 'limit' }),
        R({ action: 'error', reason: 'error', detail: 'PowerDNS nicht erreichbar' }),
    ])
    assert.equal(s.warnings.length, 5)
    assert.equal(s.errors.length, 1)
    assert.equal(s.hasProblems, true)
    const texts = s.lines.map((l) => formatPtrLine(t, l))
    assert.equal(texts[0], 'ptr.resultSkipped[ip=192.0.2.10,reason=ptr.reason.conflict[existing=other.example.com, x.example.com]]')
    assert.equal(texts[1], 'ptr.resultSkipped[ip=192.0.2.10,reason=ptr.reason.classless[zone=0/26.2.0.192.in-addr.arpa]]')
    assert.equal(texts[2], 'ptr.resultSkipped[ip=192.0.2.10,reason=ptr.reason.forbidden[zone=2.0.192.in-addr.arpa]]')
    assert.equal(texts[3], 'ptr.resultSkipped[ip=192.0.2.10,reason=ptr.reason.other_target[existing=old.example.com]]')
    assert.equal(texts[4], 'ptr.resultSkipped[ip=192.0.2.10,reason=ptr.reason.limit]')
    assert.equal(texts[5], 'ptr.resultError[ip=192.0.2.10,detail=PowerDNS nicht erreichbar]')
})

test('Fehlende Zonen-/Zielnamen ([S5]) -> _plain-Varianten, Fehler ohne detail -> ptr.reason.error', () => {
    assert.deepEqual(ptrReasonKey(R({ action: 'skipped', reason: 'forbidden', zone: null })), { key: 'ptr.reason.forbidden_plain', values: {} })
    assert.deepEqual(ptrReasonKey(R({ action: 'skipped', reason: 'classless', zone: null, classless_zone: null })), { key: 'ptr.reason.classless_plain', values: {} })
    assert.deepEqual(ptrReasonKey(R({ action: 'skipped', reason: 'conflict', existing: [] })), { key: 'ptr.reason.conflict_plain', values: {} })
    assert.deepEqual(ptrReasonKey(R({ action: 'skipped', reason: 'other_target', existing: null })), { key: 'ptr.reason.other_target_plain', values: {} })
    const s = summarizePtr([R({ action: 'error', reason: null, detail: null })])
    assert.equal(formatPtrLine(t, s.errors[0]), 'ptr.resultError[ip=192.0.2.10,detail=ptr.reason.error]')
})

test('Unbekannte Gruende/Aktionen werden nicht verschluckt', () => {
    const s = summarizePtr([R({ action: 'skipped', reason: 'quota' }), R({ action: 'teleported', reason: null })])
    assert.equal(s.warnings.length, 2)
    assert.equal(formatPtrLine(t, s.warnings[0]), 'ptr.resultSkipped[ip=192.0.2.10,reason=quota]')
    assert.equal(formatPtrLine(t, s.warnings[1]), 'ptr.resultSkipped[ip=192.0.2.10,reason=teleported]')
})

test('extractPtrList: details-Objekt, Liste, fehlend/kaputt', () => {
    const list = [R({})]
    assert.equal(extractPtrList({ ns1: 'saved', ptr: list }), list)
    assert.equal(extractPtrList(list), list)
    assert.deepEqual(extractPtrList({ ns1: 'saved' }), [])
    assert.deepEqual(extractPtrList(null), [])
    assert.deepEqual(extractPtrList({ ptr: 'kaputt' }), [])
    const s = summarizePtr({ ns1: 'saved', ptr: [null, 'x', R({})] })
    assert.equal(s.ok.length, 1)
    assert.equal(summarizePtr(undefined).hasOutput, false)
})

test('ptrMessages: Erfolgszusatz und Warnblock mit Titel', () => {
    const m = ptrMessages(t, summarizePtr([
        R({}),
        R({ action: 'skipped', reason: 'limit' }),
        R({ action: 'error', detail: 'x' }),
    ]))
    assert.equal(m.success, 'ptr.resultSet[ptr=10.2.0.192.in-addr.arpa,target=host.example.com,ip=192.0.2.10]')
    assert.equal(m.warning.split('\n')[0], 'ptr.warningTitle')
    assert.equal(m.warning.split('\n').length, 3)
    assert.ok(m.warning.split('\n')[1].startsWith('• ptr.resultSkipped'))
    assert.deepEqual(ptrMessages(t, summarizePtr([R({ action: 'unchanged' })])), { success: '', warning: '' })
})

test('applyPtrMessages: haengt an bestehende Banner an (Updater-Form), ohne Ergebnis nichts', () => {
    let success = 'Record geloescht'
    let warning = ''
    const setSuccess = (fn) => { success = typeof fn === 'function' ? fn(success) : fn }
    const setWarning = (fn) => { warning = typeof fn === 'function' ? fn(warning) : fn }
    applyPtrMessages(t, { ns1: 'saved', ptr: [R({ op: 'remove', action: 'removed' }), R({ action: 'skipped', reason: 'limit' })] }, { setSuccess, setWarning })
    assert.equal(success, 'Record geloescht ptr.resultRemoved[ptr=10.2.0.192.in-addr.arpa,ip=192.0.2.10]')
    assert.ok(warning.startsWith('ptr.warningTitle\n'))
    // vorhandene Warnung (z. B. Fan-out) bleibt, PTR-Block wird angehaengt
    warning = 'Fan-out-Warnung'
    applyPtrMessages(t, [R({ action: 'skipped', reason: 'forbidden' })], { setSuccess, setWarning })
    assert.ok(warning.startsWith('Fan-out-Warnung\nptr.warningTitle\n'))
    const before = { success, warning }
    applyPtrMessages(t, { ns1: 'saved' }, { setSuccess, setWarning })
    assert.deepEqual({ success, warning }, before)
})

test('lookupLine: alle Status der Live-Pruefung', () => {
    const base = { ip: '192.0.2.10', ptr: '10.2.0.192.in-addr.arpa.', zone: '2.0.192.in-addr.arpa.' }
    const tgt = { ip: '192.0.2.10', target: 'host.example.com.' }
    assert.deepEqual(lookupLine({ ...base, status: 'ok', would: 'set', current: [] }, tgt),
        { tone: 'ok', key: 'ptr.lookupWillSet', values: { ptr: '10.2.0.192.in-addr.arpa', target: 'host.example.com', zone: '2.0.192.in-addr.arpa' } })
    assert.equal(lookupLine({ ...base, status: 'ok', would: 'unchanged' }, tgt).key, 'ptr.lookupUnchanged')
    assert.deepEqual(lookupLine({ ...base, status: 'ok', would: 'conflict', current: ['a.example.com.'] }, tgt),
        { tone: 'warn', key: 'ptr.lookupConflict', values: { ptr: '10.2.0.192.in-addr.arpa', existing: 'a.example.com' } })
    assert.equal(lookupLine({ ...base, status: 'ok', would: 'conflict', current: null }, tgt).key, 'ptr.lookupConflictPlain')
    assert.equal(lookupLine({ ...base, status: 'no_reverse_zone', zone: null }, tgt).tone, 'muted')
    assert.deepEqual(lookupLine({ ...base, status: 'classless', classless_zone: '0/26.2.0.192.in-addr.arpa.' }, tgt),
        { tone: 'warn', key: 'ptr.lookupClassless', values: { zone: '0/26.2.0.192.in-addr.arpa' } })
    assert.equal(lookupLine({ ...base, status: 'classless', zone: null }, tgt).key, 'ptr.lookupClasslessPlain')
    assert.equal(lookupLine({ ...base, status: 'forbidden' }, tgt).key, 'ptr.lookupForbidden')
    assert.equal(lookupLine({ ...base, status: 'forbidden', zone: null }, tgt).key, 'ptr.lookupForbiddenPlain')
    assert.deepEqual(lookupLine({ ...base, status: 'error' }, tgt), { tone: 'muted', key: 'ptr.lookupError', values: { ip: '192.0.2.10' } })
    assert.equal(lookupLine(null, tgt).key, 'ptr.lookupError')
})

test('lookupCandidates: gueltige IPs, ohne Duplikate, hoechstens 3', () => {
    const valid = (s) => /^\d+\.\d+\.\d+\.\d+$/.test(s) || s.includes(':')
    assert.deepEqual(lookupCandidates(['', '192.0.2.1', 'x', '192.0.2.1', ' 192.0.2.2 ', '2001:db8::1', '192.0.2.3'], valid),
        ['192.0.2.1', '192.0.2.2', '2001:db8::1'])
    assert.deepEqual(lookupCandidates(null, valid), [])
})

/* ----- lib/ptrPreference.js --------------------------------------------------------------------- */

function memoryStorage() {
    const data = new Map()
    return {
        getItem: (k) => (data.has(k) ? data.get(k) : null),
        setItem: (k, v) => { data.set(k, String(v)) },
        removeItem: (k) => { data.delete(k) },
        _data: data,
    }
}

test('ptrPreference: Zone vor Browser-Auswahl vor Admin-Default', () => {
    const prev = globalThis.localStorage
    globalThis.localStorage = memoryStorage()
    resetPtrConfigForTests()
    try {
        assert.equal(getManagePtr('example.com'), null)
        assert.equal(effectiveManagePtr('example.com'), false)
        setPtrConfig({ auto_default: true, reverse_zones_available: 2 })
        assert.equal(getDefault(), true)
        assert.equal(effectiveManagePtr({ key: 'example.com.' }), true)

        setManagePtr('example.com', false)
        assert.equal(getManagePtr('EXAMPLE.com.'), false)
        // andere Zone: letzte Auswahl im Browser
        assert.equal(getManagePtr('other.org'), false)
        setManagePtr({ name: 'other.org' }, true)
        assert.equal(getManagePtr('example.com'), false)
        assert.equal(getManagePtr('third.net'), true)

        clearManagePtr('example.com')
        assert.equal(getManagePtr('example.com'), true) // faellt auf die letzte Auswahl zurueck
        clearManagePtr()
        assert.equal(getManagePtr('other.org'), null)
        assert.equal(effectiveManagePtr('other.org'), true) // Admin-Default
    } finally {
        globalThis.localStorage = prev
        resetPtrConfigForTests()
    }
})

test('ptrPreference: Spec-Format "1"/"0", kaputter Speicher, Speicher fehlt, Begrenzung', () => {
    const prev = globalThis.localStorage
    try {
        const mem = memoryStorage()
        globalThis.localStorage = mem
        mem.setItem(PTR_PREF_KEY, '1')
        assert.equal(getManagePtr('a.example'), true)
        mem.setItem(PTR_PREF_KEY, '{kaputt')
        assert.equal(getManagePtr('a.example'), null)

        for (let i = 0; i < MAX_REMEMBERED_ZONES + 5; i++) setManagePtr(`z${i}.example`, i % 2 === 0)
        const stored = JSON.parse(mem.getItem(PTR_PREF_KEY))
        assert.equal(Object.keys(stored.zones).length, MAX_REMEMBERED_ZONES)
        assert.ok(!('z0.example.' in stored.zones))
        assert.ok(`z${MAX_REMEMBERED_ZONES + 4}.example.` in stored.zones)

        globalThis.localStorage = {
            getItem() { throw new Error('gesperrt') },
            setItem() { throw new Error('gesperrt') },
            removeItem() { throw new Error('gesperrt') },
        }
        assert.equal(getManagePtr('a.example'), null)
        assert.equal(setManagePtr('a.example', true), false)
        delete globalThis.localStorage
        assert.equal(getManagePtr('a.example'), null)
    } finally {
        globalThis.localStorage = prev
    }
})

test('ptrPreference: Hilfen', () => {
    assert.equal(isPtrType('A'), true)
    assert.equal(isPtrType('aaaa'), true)
    assert.equal(isPtrType('MX'), false)
    assert.equal(zoneKeyOf('Example.COM'), 'example.com.')
    assert.equal(zoneKeyOf({ key: 'example.com.', name: 'x' }), 'example.com.')
    assert.equal(zoneKeyOf(null), '')
})

test('loadPtrConfig: Cache, ein Request gleichzeitig, Fehler -> Default, Abonnenten', async () => {
    resetPtrConfigForTests()
    let calls = 0
    const seen = []
    const unsub = subscribePtrConfig((cfg) => seen.push(cfg.auto_default))
    const fetcher = async () => { calls++; return { auto_default: true, reverse_zones_available: 0 } }
    const [a, b] = await Promise.all([loadPtrConfig(fetcher, { now: 1000 }), loadPtrConfig(fetcher, { now: 1000 })])
    assert.equal(calls, 1)
    assert.equal(a, b)
    assert.deepEqual(getPtrConfig(), { auto_default: true, reverse_zones_available: 0 })
    await loadPtrConfig(fetcher, { now: Date.now() })
    assert.equal(calls, 1) // innerhalb der Cache-Zeit kein neuer Request
    await loadPtrConfig(fetcher, { force: true })
    assert.equal(calls, 2)
    setDefaultInCache(false)
    assert.deepEqual(getPtrConfig(), { auto_default: false, reverse_zones_available: 0 })
    unsub()
    assert.deepEqual(seen, [true, true, false])

    resetPtrConfigForTests()
    const cfg = await loadPtrConfig(async () => { throw new Error('403') })
    assert.deepEqual(cfg, { auto_default: false, reverse_zones_available: null })
    assert.equal(getDefault(), false)
    resetPtrConfigForTests()
})
