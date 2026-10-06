// Tests fuer zoneDetail/dnssecModel.js (F4 §6.2, Plan WS-F4-B inkl. [D10]). Reine Funktionen; `t` ist ein Fake,
// der Key und Parameter sichtbar macht.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
    DEFAULT_DNSSEC_OPTIONS, DS_STATUS_KEYS, HINT_KEYS, PEER_STATE_KEYS, PHASE_KEYS, PROTECTION_KEYS, ROLLOVER_STEPS,
    ROLLOVER_STEP_KEYS, activeAlgorithms, algorithmLabel, algorithmOptions, buildDnssecPayload, buildNsecPayload,
    bumpSerialValue, canManageDnssec, cardState, currentSepTags, dnssecServerOf, dsGroups, followUpMessages,
    forceInfo, formatDuration, formatNsec, hasDnssecWarning, hintText, isPrimaryKind, keyModelOf, keyTypeLabel,
    maxRecordTtl, mergeStatusKeepPeers, optionsFromStatus, parseCreateResult, parseDnskey, recommendedDs,
    rolloverActionAllowed, rolloverNewKeyBody, rolloverRunning, rolloverStepIndex, sinceSeconds, soaTimings,
    sortedHints, validateDnssecOptions, visiblePeers, withForce,
} from '../src/zoneDetail/dnssecModel.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')

const t = (key, params = {}) => (Object.keys(params).length ? `${key}|${JSON.stringify(params)}` : key)

function key(id, extra = {}) {
    return {
        id, keytype: 'csk', role: 'sep', flags: 257, active: true, published: true, published_effective: true,
        algorithm: 'ECDSAP256SHA256', algorithm_number: 13, bits: 256, key_tag: 1000 + id, dnskey: `257 3 13 KEY${id}==`,
        ds: [
            { ds: `${1000 + id} 13 1 aa`, digest_type: 1, parsed: { key_tag: 1000 + id, digest_type: 1, digest_type_name: 'SHA-1' } },
            { ds: `${1000 + id} 13 2 bb`, digest_type: 2, parsed: { key_tag: 1000 + id, digest_type: 2, digest_type_name: 'SHA-256' } },
            { ds: `${1000 + id} 13 4 cc`, digest_type: 4, parsed: { key_tag: 1000 + id, digest_type: 4, digest_type_name: 'SHA-384' } },
        ],
        ds_status: 'current', rollover_role: null,
        ...extra,
    }
}

// ---------------------------------------------------------------------------------------------
// Optionen

test('validateDnssecOptions: Iterationen 0..50 als ganze Zahl, Salt hex in gerader Anzahl', () => {
    assert.deepEqual(validateDnssecOptions(DEFAULT_DNSSEC_OPTIONS), {})
    for (const bad of ['', '-1', '51', 'abc', '1.5']) {
        assert.equal(validateDnssecOptions({ nsec3_iterations: bad }).nsec3_iterations, 'dnssec.errIterations', bad)
    }
    assert.deepEqual(validateDnssecOptions({ nsec3_iterations: '50' }), {})
    assert.equal(validateDnssecOptions({ nsec3_iterations: 0, nsec3_salt: 'abc' }).nsec3_salt, 'dnssec.errSalt')
    assert.equal(validateDnssecOptions({ nsec3_iterations: 0, nsec3_salt: 'zz' }).nsec3_salt, 'dnssec.errSalt')
    assert.equal(validateDnssecOptions({ nsec3_iterations: 0, nsec3_salt: 'a'.repeat(512) }).nsec3_salt, 'dnssec.errSalt')
    assert.deepEqual(validateDnssecOptions({ nsec3_iterations: 0, nsec3_salt: 'AB' }), {})
    assert.deepEqual(validateDnssecOptions({ nsec3_iterations: 0, nsec3_salt: '-' }), {})
    // NSEC: NSEC3-Felder werden nicht geprueft
    assert.deepEqual(validateDnssecOptions({ nsec_mode: 'nsec', nsec3_iterations: 'x', nsec3_salt: 'q' }), {})
})

test('buildDnssecPayload: Standard, RSA-Bits, NSEC, Salt', () => {
    assert.deepEqual(buildDnssecPayload(DEFAULT_DNSSEC_OPTIONS), {
        key_model: 'csk', algorithm: 'ECDSAP256SHA256', bits: null, nsec_mode: 'nsec3',
        nsec3_iterations: 0, nsec3_salt: '-', nsec3_optout: false, nsec3narrow: false,
    })
    assert.equal(buildDnssecPayload({ algorithm: 'RSASHA256' }).bits, 2048)
    assert.equal(buildDnssecPayload({ algorithm: 'rsasha512', bits: 4096 }).bits, 4096)
    assert.equal(buildDnssecPayload({ algorithm: 'RSASHA256', bits: 1024 }).bits, 2048)
    assert.equal(buildDnssecPayload({ algorithm: 'ED25519', bits: 4096 }).bits, null)
    assert.equal(buildDnssecPayload({ key_model: 'ksk_zsk' }).key_model, 'ksk_zsk')
    const nsec = buildDnssecPayload({ nsec_mode: 'nsec', nsec3_iterations: 5, nsec3_salt: 'ab', nsec3narrow: true })
    assert.deepEqual([nsec.nsec_mode, nsec.nsec3_iterations, nsec.nsec3_salt, nsec.nsec3narrow], ['nsec', 0, '-', false])
    const n3 = buildNsecPayload({ nsec3_iterations: '3', nsec3_salt: 'AB', nsec3_optout: true, nsec3narrow: true })
    assert.deepEqual(n3, { nsec_mode: 'nsec3', nsec3_iterations: 3, nsec3_salt: 'ab', nsec3_optout: true, nsec3narrow: true })
    assert.equal('bits' in n3, false)
})

test('optionsFromStatus und formatNsec', () => {
    const o = optionsFromStatus({ nsec: { mode: 'nsec3', nsec3param: '1 1 5 ab', iterations: 5, salt: 'ab', opt_out: true, narrow: true } })
    assert.deepEqual([o.nsec_mode, o.nsec3_iterations, o.nsec3_salt, o.nsec3_optout, o.nsec3narrow], ['nsec3', 5, 'ab', true, true])
    assert.equal(optionsFromStatus({ nsec: { mode: 'nsec3', iterations: 0, salt: '-' } }).nsec3_salt, '')
    assert.equal(optionsFromStatus({ nsec: { mode: 'nsec' } }).nsec_mode, 'nsec')
    assert.equal(formatNsec({ mode: 'nsec' }, t), 'dnssec.denialNsec')
    assert.equal(formatNsec({ mode: 'nsec3', nsec3param: '1 0 0 -' }, t), 'dnssec.denialNsec3|{"params":"1 0 0 -"}')
    assert.equal(formatNsec({ mode: null }, t), '—')
})

test('algorithmOptions/algorithmLabel: Status-Liste oder Konstante', () => {
    assert.equal(algorithmOptions(null).length, 6)
    const opts = algorithmOptions({ algorithms: [{ name: 'ED448', number: 16, rsa: false, recommended: false, build_dependent: true }] })
    assert.deepEqual(opts.map((a) => [a.name, a.label, a.buildDependent]), [['ED448', 'Ed448', true]])
    assert.equal(algorithmLabel('ecdsap256sha256', 13), 'ECDSAP256SHA256 (13)')
    assert.equal(algorithmLabel('RSASHA256'), 'RSASHA256 (8)')
    assert.equal(algorithmLabel(null, 5), '5')
})

// ---------------------------------------------------------------------------------------------
// Status, Hinweise, Peers

test('cardState, canManageDnssec, keyModelOf, activeAlgorithms, keyTypeLabel', () => {
    assert.equal(cardState(null), null)
    assert.equal(cardState({ presigned: true, keys: [key(1)] }), 'presigned')
    assert.equal(cardState({ keys: [] }), 'off')
    assert.equal(cardState({ keys: [key(1, { active: false })], signed: false }), 'keysInactive')
    assert.equal(cardState({ keys: [key(1)], signed: true }), 'signed')
    assert.equal(canManageDnssec(true, { can_write: true }), true)
    assert.equal(canManageDnssec(true, { can_write: false }), false)
    assert.equal(canManageDnssec(false, { can_write: true }), false)
    assert.equal(keyModelOf([key(1)]), 'csk')
    assert.equal(keyModelOf([key(1, { keytype: 'ksk' }), key(2, { keytype: 'zsk', role: 'zsk', flags: 256 })]), 'ksk_zsk')
    assert.equal(keyModelOf([key(1, { active: false })]), null)
    assert.deepEqual(activeAlgorithms([key(1), key(2), key(3, { active: false, algorithm: 'RSASHA256', algorithm_number: 8 })]),
        ['ECDSAP256SHA256 (13)'])
    assert.equal(keyTypeLabel({ keytype: 'zsk' }), 'ZSK')
    assert.equal(keyTypeLabel({ keytype: 'x', role: 'sep' }), 'KSK')
    assert.deepEqual(currentSepTags([key(1), key(2, { ds_status: 'new' })]), [1001])
})

test('sortedHints/hintText: Reihenfolge danger -> warning -> info, sha1_ds nur im Dialog, Phase uebersetzt', () => {
    const hints = [
        { code: 'secondaries_serial', level: 'info', params: {} },
        { code: 'sha1_ds', level: 'info', params: {} },
        { code: 'api_rectify_off', level: 'warning', params: {} },
        { code: 'peers_divergent', level: 'danger', params: { servers: 'ns2' } },
    ]
    assert.deepEqual(sortedHints(hints).map((h) => h.code), ['peers_divergent', 'api_rectify_off', 'secondaries_serial'])
    assert.equal(sortedHints(hints, { exclude: [] }).length, 4)
    assert.equal(hintText({ code: 'rollover_in_progress', params: { track: 'sep', phase: 'both_active' } }, t),
        'dnssec.hint.rolloverInProgress|{"track":"sep","phase":"dnssec.phase.bothActive"}')
    assert.equal(hintText({ code: 'peers_divergent', params: { servers: 'ns2' } }, t), 'dnssec.hint.peersDivergent|{"servers":"ns2"}')
    assert.equal(hintText({ code: 'unbekannt_neu' }, t), 'unbekannt_neu')
    assert.equal(Object.keys(HINT_KEYS).length, 22)
})

test('visiblePeers, mergeStatusKeepPeers, rolloverRunning', () => {
    const peers = [{ server: 'ns2', state: 'same_keys' }, { server: 'ns3', state: 'zone_missing' }]
    assert.deepEqual(visiblePeers(peers).map((p) => p.server), ['ns2'])
    const prev = { peers, hints: [{ code: 'peers_unreachable', level: 'warning' }, { code: 'version_unknown', level: 'info' }] }
    const next = { peers: null, hints: [{ code: 'version_unknown', level: 'info' }] }
    const merged = mergeStatusKeepPeers(next, prev)
    assert.equal(merged.peers, peers)
    assert.deepEqual(merged.hints.map((h) => h.code), ['version_unknown', 'peers_unreachable'])
    const fresh = { peers: [], hints: [] }
    assert.equal(mergeStatusKeepPeers(fresh, prev), fresh)
    assert.equal(rolloverRunning({ rollover: { sep: { phase: 'idle' }, zsk: { phase: 'old_retired' } } }), true)
    assert.equal(rolloverRunning({ rollover: { sep: { phase: 'idle' }, zsk: null } }), false)
})

// ---------------------------------------------------------------------------------------------
// DS-Assistent

test('dsGroups/recommendedDs: SHA-256 als Empfehlung nur bei current, SHA-1 eingeklappt', () => {
    const keys = [key(2, { ds_status: 'new', active: false }), key(1), key(3, { role: 'zsk', flags: 256, ds: [] })]
    const groups = dsGroups(keys)
    assert.deepEqual(groups.map((g) => g.key.id), [1, 2])
    assert.deepEqual(groups[0].lines.map((d) => d.digest_type), [2])
    assert.equal(groups[0].hasSha1, true)
    assert.equal(groups[0].recommended.ds, '1001 13 2 bb')
    assert.equal(groups[1].recommended, null)
    assert.equal(dsGroups(keys, { showAll: true })[0].lines.length, 3)
    assert.equal(recommendedDs({ ds: [{ ds: 'x 13 1 aa', digest_type: 1, parsed: { digest_type: 1 } }] }).ds, 'x 13 1 aa')
    assert.deepEqual(parseDnskey('257 3 13 AbC dEf=='), { flags: 257, protocol: 3, algorithm: 13, publicKey: 'AbCdEf==' })
    assert.equal(parseDnskey('kaputt'), null)
})

// ---------------------------------------------------------------------------------------------
// Zeiten

test('soaTimings, maxRecordTtl, formatDuration, sinceSeconds', () => {
    const records = [
        { name: 'example.com.', type: 'SOA', ttl: 3600, content: 'ns1. hostmaster. 1 10800 3600 604800 300' },
        { name: 'www.example.com.', type: 'A', ttl: 86400, content: '192.0.2.1' },
        { name: 'example.com.', type: 'RRSIG', ttl: 999999, content: 'x' },
    ]
    assert.deepEqual(soaTimings(records), { minimum: 300, ttl: 3600 })
    assert.equal(soaTimings([]), null)
    assert.equal(maxRecordTtl(records), 86400)
    assert.equal(maxRecordTtl([]), null)
    assert.equal(formatDuration(90061, t), 'dnssec.durationDays|{"d":1,"h":1}')
    assert.equal(formatDuration(3720, t), 'dnssec.durationHours|{"h":1,"m":2}')
    assert.equal(formatDuration(300, t), 'dnssec.durationMinutes|{"m":5}')
    assert.equal(formatDuration(42, t), 'dnssec.durationSeconds|{"s":42}')
    assert.equal(sinceSeconds('2026-10-06T10:00:00Z', Date.parse('2026-10-06T10:01:00Z')), 60)
    assert.equal(sinceSeconds('kein Datum'), null)
    assert.equal(sinceSeconds(null), null)
})

// ---------------------------------------------------------------------------------------------
// Schutzregeln und Folgeaktionen

function protectionError(code = 'last_active_key') {
    const err = new Error('Schutzregel')
    err.status = 409
    err.payload = { detail: { message: 'Text vom Backend', code, force_possible: true } }
    return err
}

test('forceInfo/withForce: 409 mit force_possible -> Rueckfrage -> Wiederholung mit force', async () => {
    assert.equal(forceInfo(protectionError()).code, 'last_active_key')
    const plain = Object.assign(new Error('x'), { status: 409, payload: { detail: 'Text' } })
    assert.equal(forceInfo(plain), null)

    const calls = []
    const op = async (force) => {
        calls.push(force)
        if (!force) throw protectionError()
        return { ok: true }
    }
    const asked = []
    const res = await withForce(op, t, (msg) => { asked.push(msg); return true })
    assert.deepEqual(res, { ok: true })
    assert.deepEqual(calls, [false, true])
    assert.equal(asked[0], 'dnssec.forceConfirm|{"reason":"dnssec.protect.lastActiveKey"}')

    calls.length = 0
    assert.equal(await withForce(op, t, () => false), null)
    assert.deepEqual(calls, [false])

    const unknownCode = await withForce(async (force) => { if (!force) throw protectionError('neu'); return 1 }, t,
        (msg) => { asked.push(msg); return true })
    assert.equal(unknownCode, 1)
    assert.equal(asked.at(-1), 'dnssec.forceConfirm|{"reason":"Text vom Backend"}')

    await assert.rejects(withForce(async () => { throw plain }, t, () => true), /x/)
})

test('followUpMessages/bumpSerialValue: Serial und NOTIFY [D10]', () => {
    assert.deepEqual(followUpMessages({ serial_bumped: true, serial: 2026100502, notified: true }, t), {
        info: ['dnssec.followSerialBumped|{"serial":2026100502}', 'dnssec.followNotified'], warnings: [],
    })
    assert.deepEqual(followUpMessages({ serial_bumped: true, serial: 5, notify_error: 'PowerDNS (ns1): kaputt' }, t).warnings,
        ['dnssec.followNotifyError|{"error":"PowerDNS (ns1): kaputt"}'])
    assert.deepEqual(followUpMessages({ serial_error: 'x' }, t).warnings, ['dnssec.followSerialError|{"error":"x"}'])
    assert.deepEqual(followUpMessages(null, t), { info: [], warnings: [] })
    assert.equal(isPrimaryKind('Master') && isPrimaryKind('producer') && isPrimaryKind('primary'), true)
    assert.equal(isPrimaryKind('Native'), false)
    assert.equal(bumpSerialValue('Master', false), false)
    assert.equal(bumpSerialValue('Master', true), true)
    assert.equal(bumpSerialValue('Native', true), undefined)
})

// ---------------------------------------------------------------------------------------------
// Rollover-Assistent

test('rolloverStepIndex/rolloverActionAllowed KSK/CSK: DNSKEY-Pruefung vor DS-Tausch und vor dem Loeschen', () => {
    const steps = ROLLOVER_STEPS.sep
    const idx = (name) => steps.indexOf(name)
    assert.equal(rolloverStepIndex('sep', { phase: 'idle' }), idx('prepublish'))
    assert.equal(rolloverActionAllowed('sep', { phase: 'idle' }), true)
    const pre = { phase: 'new_prepublished' }
    assert.equal(rolloverStepIndex('sep', pre, {}), idx('dnskeyCheck'))
    assert.equal(rolloverStepIndex('sep', pre, { dnskey: true }), idx('dsAdd'))
    assert.equal(rolloverStepIndex('sep', pre, { dnskey: true, ds: true }), idx('switch'))
    assert.ok(idx('dnskeyCheck') < idx('dsAdd'))
    assert.equal(rolloverActionAllowed('sep', pre, { ds: true }), false)
    assert.equal(rolloverActionAllowed('sep', pre, { dnskey: true }), false)
    assert.equal(rolloverActionAllowed('sep', pre, { dnskey: true, ds: true }), true)
    // both_active (W3-L6, WS-W3-NACHARBEIT): DNSKEY-Pruefung und DS vor dem Abschalten des alten Schluessels
    const both = { phase: 'both_active' }
    assert.equal(rolloverActionAllowed('sep', both, {}), false)
    assert.equal(rolloverActionAllowed('sep', both, { ds: true }), false)
    assert.equal(rolloverActionAllowed('sep', both, { dnskey: true }), false)
    assert.equal(rolloverActionAllowed('sep', both, { dnskey: true, ds: true }), true)
    assert.equal(rolloverStepIndex('sep', both, {}), idx('dnskeyCheck'))
    assert.equal(rolloverStepIndex('sep', both, { dnskey: true }), idx('dsAdd'))
    assert.equal(rolloverStepIndex('sep', both, { dnskey: true, ds: true }), idx('switch'))
    const retired = { phase: 'old_retired' }
    assert.equal(rolloverStepIndex('sep', retired, {}), idx('dsRemove'))
    assert.equal(rolloverStepIndex('sep', retired, { dsRemoved: true }), idx('dnskeyCheckDelete'))
    assert.equal(rolloverStepIndex('sep', retired, { dsRemoved: true, dnskeyDelete: true }), idx('delete'))
    assert.ok(idx('dnskeyCheckDelete') < idx('delete'))
    assert.equal(rolloverActionAllowed('sep', retired, { dsRemoved: true }), false)
    assert.equal(rolloverActionAllowed('sep', retired, { dsRemoved: true, dnskeyDelete: true }), true)
    for (const phase of ['no_active', 'complex']) {
        assert.equal(rolloverStepIndex('sep', { phase }), -1)
        assert.equal(rolloverActionAllowed('sep', { phase }, { dnskey: true, ds: true }), false)
    }
    assert.equal(rolloverStepIndex('sep', null), -1)
})

test('rolloverStepIndex/rolloverActionAllowed ZSK: Wartezeit + DNSKEY-Pruefung, kein Registrar', () => {
    const steps = ROLLOVER_STEPS.zsk
    const idx = (name) => steps.indexOf(name)
    assert.equal(steps.includes('dsAdd') || steps.includes('dsRemove'), false)
    const pre = { phase: 'new_prepublished' }
    assert.equal(rolloverStepIndex('zsk', pre, {}), idx('waitDnskey'))
    assert.equal(rolloverStepIndex('zsk', pre, { waited: true }), idx('dnskeyCheck'))
    assert.equal(rolloverStepIndex('zsk', pre, { waited: true, dnskey: true }), idx('switch'))
    assert.equal(rolloverActionAllowed('zsk', pre, { waited: true }), false)
    assert.equal(rolloverActionAllowed('zsk', pre, { waited: true, dnskey: true }), true)
    // both_active (W3-L6): nicht mehr ohne Bedingung – DNSKEY-Pruefung (bzw. Bestaetigung) noetig
    assert.equal(rolloverActionAllowed('zsk', { phase: 'both_active' }, {}), false)
    assert.equal(rolloverActionAllowed('zsk', { phase: 'both_active' }, { dnskey: true }), true)
    assert.equal(rolloverStepIndex('zsk', { phase: 'both_active' }, {}), idx('dnskeyCheck'))
    assert.equal(rolloverStepIndex('zsk', { phase: 'both_active' }, { dnskey: true }), idx('switch'))
    const retired = { phase: 'old_retired' }
    assert.equal(rolloverStepIndex('zsk', retired, {}), idx('waitMaxTtl'))
    assert.equal(rolloverActionAllowed('zsk', retired, { waited: true, dnskeyDelete: true }), true)
    assert.equal(rolloverActionAllowed('zsk', retired, { waited: true }), false)
})

test('rolloverNewKeyBody: gleicher Typ/Algorithmus, veroeffentlicht + inaktiv, Bits nur bei RSA', () => {
    assert.deepEqual(rolloverNewKeyBody(key(1)), { keytype: 'csk', algorithm: 'ECDSAP256SHA256', active: false, published: true })
    assert.deepEqual(rolloverNewKeyBody(key(1, { keytype: 'ksk', algorithm: 'RSASHA256', algorithm_number: 8, bits: 3072 })),
        { keytype: 'ksk', algorithm: 'RSASHA256', active: false, published: true, bits: 3072 })
    assert.equal(rolloverNewKeyBody(key(1, { keytype: 'zsk', algorithm: 'rsasha512', bits: 999 })).bits, 2048)
})

// ---------------------------------------------------------------------------------------------
// Zone anlegen

test('parseCreateResult/dnssecServerOf/hasDnssecWarning (F4 §2.10)', () => {
    assert.deepEqual(parseCreateResult('created'), { kind: 'created', detail: '' })
    assert.deepEqual(parseCreateResult('synced'), { kind: 'synced', detail: '' })
    assert.deepEqual(parseCreateResult('created; dnssec-error: Schlüssel anlegen (CSK): kaputt'),
        { kind: 'dnssecError', detail: 'Schlüssel anlegen (CSK): kaputt' })
    assert.deepEqual(parseCreateResult('created; dnssec-skipped'), { kind: 'dnssecSkipped', detail: '' })
    assert.deepEqual(parseCreateResult('error: 422'), { kind: 'error', detail: '422' })
    assert.equal(parseCreateResult('skipped (read-only)').kind, 'other')
    const details = { ro: 'skipped (read-only)', ns1: 'synced', ns2: 'created; dnssec-error: x', ns3: 'created; dnssec-skipped' }
    assert.equal(dnssecServerOf(details), 'ns2')
    assert.equal(hasDnssecWarning(details), true)
    assert.equal(hasDnssecWarning({ ns1: 'created', ns2: 'synced' }), false)
})

// ---------------------------------------------------------------------------------------------
// i18n: alle Keys des Modells existieren (en.json oder – vor dem Wellen-Merge – Fragment f4-b.en.json)

function flatten(obj, prefix = '', out = new Set()) {
    for (const [k, v] of Object.entries(obj)) {
        const full = prefix ? `${prefix}.${k}` : k
        if (v && typeof v === 'object') flatten(v, full, out)
        else out.add(full)
    }
    return out
}

test('Modell-Keys sind in en vorhanden', () => {
    const en = flatten(JSON.parse(fs.readFileSync(path.join(SRC, 'locales', 'en.json'), 'utf-8')))
    const fragment = path.join(SRC, 'locales', 'fragments', 'f4-b.en.json')
    if (fs.existsSync(fragment)) {
        for (const k of Object.keys(JSON.parse(fs.readFileSync(fragment, 'utf-8')).set)) en.add(k)
    }
    const keys = [
        ...Object.values(HINT_KEYS), ...Object.values(PROTECTION_KEYS), ...Object.values(PHASE_KEYS),
        ...Object.values(PEER_STATE_KEYS), ...Object.values(DS_STATUS_KEYS), ...Object.values(ROLLOVER_STEP_KEYS),
        'dnssec.errIterations', 'dnssec.errSalt', 'dnssec.forceConfirm', 'dnssec.followSerialBumped',
        'dnssec.followSerialError', 'dnssec.followNotified', 'dnssec.followNotifyError', 'dnssec.denialNsec',
        'dnssec.denialNsec3', 'dnssec.durationDays', 'dnssec.durationHours', 'dnssec.durationMinutes',
        'dnssec.durationSeconds',
    ]
    const missing = keys.filter((k) => !en.has(k))
    assert.deepEqual(missing, [])
})
