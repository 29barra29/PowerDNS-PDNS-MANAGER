// Tests fuer DNSSEC Teil B (Plan WS-F4-C [D10]): Freigabe der Rollover-Schritte ueber die DNSKEY-Pruefung,
// Auswertung der Elternzonen-Pruefung, API-Methoden und benutzte i18n-Keys der Teil-B-Komponenten.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
    ROLLOVER_STEPS, dnskeyCheckOutcome, dnskeyStepPassed, effectiveRolloverChecks, parentDsKeySummary, parentDsRows,
    resolverDisplay, rolloverActionAllowed, rolloverStepIndex,
} from '../src/zoneDetail/dnssecModel.js'
import f4b from '../src/api/f4-b.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')

const NEW = 4711
const OLD = 1234

function check(nameservers, extra = {}) {
    return { zone: 'example.com.', enabled: true, expected_tags: [NEW], nameservers, all_ok: false, truncated: false, ...extra }
}
const ns = (ok, serves, error = null) => ({ ok, serves_key_tags: serves, missing_tags: ok ? [] : [NEW], addresses: [], error })

test('dnskeyCheckOutcome: aus, ungeprueft, ok, fehlgeschlagen, Anfragefehler', () => {
    assert.equal(dnskeyCheckOutcome(null, { capability: false, tag: NEW }).state, 'disabled')
    assert.equal(dnskeyCheckOutcome(check({}, { enabled: false }), { capability: true, tag: NEW }).state, 'disabled')
    assert.equal(dnskeyCheckOutcome(null, { capability: true, tag: NEW }).state, 'unchecked')
    const ok = dnskeyCheckOutcome(check({ 'ns1.': ns(true, [OLD, NEW]), 'ns2.': ns(true, [NEW]) }), { capability: true, tag: NEW })
    assert.equal(ok.state, 'ok')
    assert.deepEqual(ok.rows.map((r) => [r.ns, r.ok]), [['ns1.', true], ['ns2.', true]])
    const bad = dnskeyCheckOutcome(check({ 'ns1.': ns(true, [OLD, NEW]), 'ns2.': ns(false, [OLD]) }), { capability: true, tag: NEW })
    assert.equal(bad.state, 'failed')
    assert.deepEqual(bad.rows[1], { ns: 'ns2.', ok: false, missing: [NEW], serves: [OLD], error: null })
    // Server meldet ok, liefert aber den erwarteten Tag nicht (anderer Erwartungswert) -> nicht ok
    assert.equal(dnskeyCheckOutcome(check({ 'ns1.': ns(true, [OLD]) }), { capability: true, tag: NEW }).state, 'failed')
    // keine Nameserver bzw. abgeschnitten -> nie ok
    assert.equal(dnskeyCheckOutcome(check({}), { capability: true, tag: NEW }).state, 'failed')
    assert.equal(dnskeyCheckOutcome(check({ 'ns1.': ns(true, [NEW]) }, { truncated: true }), { capability: true, tag: NEW }).state, 'failed')
    const err = dnskeyCheckOutcome(null, { capability: true, tag: NEW, requestError: 'Zu viele' })
    assert.equal(err.state, 'error')
    assert.equal(err.error, 'Zu viele')
    // ohne Tag zaehlt das ok des Servers (erwartet: alle veroeffentlichten Schluessel)
    assert.equal(dnskeyCheckOutcome(check({ 'ns1.': ns(true, [OLD]) }), { capability: true, tag: null }).state, 'ok')
})

test('dnskeyStepPassed: Checkbox nur bei ausgeschalteter Pruefung, sonst Ergebnis bzw. force', () => {
    assert.equal(dnskeyStepPassed({ state: 'disabled' }, {}), false)
    assert.equal(dnskeyStepPassed({ state: 'disabled' }, { confirmed: true }), true)
    assert.equal(dnskeyStepPassed({ state: 'unchecked' }, { confirmed: true, forced: true }), false)
    assert.equal(dnskeyStepPassed({ state: 'ok' }, {}), true)
    assert.equal(dnskeyStepPassed({ state: 'failed' }, { confirmed: true }), false)
    assert.equal(dnskeyStepPassed({ state: 'failed' }, { forced: true }), true)
    assert.equal(dnskeyStepPassed({ state: 'error' }, { forced: true }), true)
    assert.equal(dnskeyStepPassed(null, { confirmed: true }), false)
})

test('Rollover KSK/CSK: DS tauschen und Loeschen nur frei, wenn alle NS den neuen Schluessel liefern', () => {
    const steps = ROLLOVER_STEPS.sep
    const pre = { phase: 'new_prepublished', old_key_id: 1, new_key_id: 2 }
    const retired = { phase: 'old_retired', old_key_id: 1, new_key_id: 2 }
    const outcome = (state) => ({ dnskey: { state }, dnskeyDelete: { state } })

    // Pruefung an, noch nicht gelaufen: Checkbox "ich habe geprueft" zaehlt NICHT
    let eff = effectiveRolloverChecks({ dnskey: true, ds: true }, outcome('unchecked'))
    assert.equal(rolloverStepIndex('sep', pre, eff), steps.indexOf('dnskeyCheck'))
    assert.equal(rolloverActionAllowed('sep', pre, eff), false)
    // alle NS ok -> DS-Schritt, mit DS-Haken Umschalten frei
    eff = effectiveRolloverChecks({ ds: false }, outcome('ok'))
    assert.equal(rolloverStepIndex('sep', pre, eff), steps.indexOf('dsAdd'))
    eff = effectiveRolloverChecks({ ds: true }, outcome('ok'))
    assert.equal(rolloverActionAllowed('sep', pre, eff), true)
    // fehlgeschlagen -> nur mit force
    eff = effectiveRolloverChecks({ ds: true }, outcome('failed'))
    assert.equal(rolloverActionAllowed('sep', pre, eff), false)
    eff = effectiveRolloverChecks({ ds: true, dnskeyForce: true }, outcome('failed'))
    assert.equal(rolloverActionAllowed('sep', pre, eff), true)
    // Pruefung aus -> Checkbox
    eff = effectiveRolloverChecks({ ds: true, dnskey: true }, outcome('disabled'))
    assert.equal(rolloverActionAllowed('sep', pre, eff), true)
    eff = effectiveRolloverChecks({ ds: true }, outcome('disabled'))
    assert.equal(rolloverActionAllowed('sep', pre, eff), false)

    // Loeschen des alten Schluessels
    eff = effectiveRolloverChecks({ dsRemoved: true }, outcome('ok'))
    assert.equal(rolloverStepIndex('sep', retired, eff), steps.indexOf('delete'))
    assert.equal(rolloverActionAllowed('sep', retired, eff), true)
    eff = effectiveRolloverChecks({ dsRemoved: true, dnskeyDelete: true }, outcome('failed'))
    assert.equal(rolloverActionAllowed('sep', retired, eff), false)
    eff = effectiveRolloverChecks({ dsRemoved: true, dnskeyDeleteForce: true }, outcome('failed'))
    assert.equal(rolloverActionAllowed('sep', retired, eff), true)
    // force des einen Schritts gilt nicht fuer den anderen
    eff = effectiveRolloverChecks({ dsRemoved: true, dnskeyForce: true }, outcome('failed'))
    assert.equal(rolloverActionAllowed('sep', retired, eff), false)
})

test('Rollover ZSK: Warten + DNSKEY-Pruefung', () => {
    const pre = { phase: 'new_prepublished' }
    const eff = effectiveRolloverChecks({ waited: true }, { dnskey: { state: 'ok' }, dnskeyDelete: { state: 'unchecked' } })
    assert.equal(rolloverActionAllowed('zsk', pre, eff), true)
    assert.equal(rolloverActionAllowed('zsk', { phase: 'old_retired' }, eff), false)
})

test('effectiveRolloverChecks ohne Ergebnis: Altverhalten (Checkbox)', () => {
    assert.deepEqual(effectiveRolloverChecks({ dnskey: true, ds: true }), { dnskey: true, ds: true, dnskeyDelete: false })
})

test('Elternzone: Zeilen, Anzeigename und Sichtbarkeit je Schluessel', () => {
    const result = {
        zone: 'example.com.', enabled: true, any_visible: true, unknown_tags: [99],
        resolvers: [
            { resolver: '1.1.1.1', label: 'Cloudflare', status: 'ok', ds: ['x'], key_tags: [OLD], error: null },
            { resolver: '8.8.8.8', label: 'Google', status: 'nodata', ds: [], key_tags: [], error: null },
            { resolver: '192.0.2.1', label: null, status: 'timeout', ds: [], key_tags: [], error: 'Zeitüberschreitung' },
        ],
        keys: {
            1: { key_tag: OLD, visible_on: ['1.1.1.1'], missing_on: ['8.8.8.8'] },
            2: { key_tag: NEW, visible_on: [], missing_on: ['1.1.1.1', '8.8.8.8'] },
        },
    }
    assert.equal(resolverDisplay({ resolver: '9.9.9.9', label: 'Quad9' }), 'Quad9 (9.9.9.9)')
    assert.equal(resolverDisplay({ resolver: '192.0.2.1' }), '192.0.2.1')
    assert.deepEqual(parentDsRows(result), [
        { resolver: 'Cloudflare (1.1.1.1)', kind: 'ds', tags: String(OLD), error: null },
        { resolver: 'Google (8.8.8.8)', kind: 'none', tags: '', error: null },
        { resolver: '192.0.2.1', kind: 'error', tags: '', error: 'Zeitüberschreitung' },
    ])
    const old = parentDsKeySummary(result, 1)
    assert.deepEqual(old, { tag: OLD, visibleOn: ['Cloudflare (1.1.1.1)'], missingOn: ['Google (8.8.8.8)'], visibleAll: false, answered: true })
    const fresh = parentDsKeySummary(result, 2)
    assert.equal(fresh.visibleAll, false)
    assert.equal(fresh.visibleOn.length, 0)
    assert.equal(parentDsKeySummary({ ...result, keys: { 2: { key_tag: NEW, visible_on: ['1.1.1.1'], missing_on: [] } } }, 2).visibleAll, true)
    assert.equal(parentDsKeySummary({ enabled: false }, 1), null)
    assert.equal(parentDsKeySummary(result, null), null)
    assert.deepEqual(parentDsRows(null), [])
})

test('API: getParentDs/getDnskeyCheck bauen die Pfade (Zone kodiert, key_tag wiederholt)', async () => {
    const calls = []
    const client = { ...f4b, request: (...args) => { calls.push(args); return Promise.resolve({}) } }
    await client.getParentDs('ns 1', 'example.com.')
    await client.getDnskeyCheck('ns1', 'example.com.', { keyTags: [NEW, OLD] })
    await client.getDnskeyCheck('ns1', 'example.com.')
    await client.getDnskeyCheck('ns1', 'example.com.', { keyTags: [null] })
    assert.deepEqual(calls.map((c) => [c[0], c[1]]), [
        ['GET', '/dnssec/ns%201/example.com./parent-ds'],
        ['GET', `/dnssec/ns1/example.com./dnskey-check?key_tag=${NEW}&key_tag=${OLD}`],
        ['GET', '/dnssec/ns1/example.com./dnskey-check'],
        ['GET', '/dnssec/ns1/example.com./dnskey-check'],
    ])
})

function flatten(obj, prefix = '', out = new Set()) {
    for (const [k, v] of Object.entries(obj)) {
        if (v && typeof v === 'object') flatten(v, `${prefix}${k}.`, out)
        else out.add(`${prefix}${k}`)
    }
    return out
}

test('Teil-B-Komponenten: alle benutzten dnssec.*-Keys existieren (en + Fragment f4-c), entfernte Keys sind ungenutzt', () => {
    const en = flatten(JSON.parse(fs.readFileSync(path.join(SRC, 'locales', 'en.json'), 'utf-8')))
    const fragment = path.join(SRC, 'locales', 'fragments', 'f4-c.en.json')
    let removed = []
    if (fs.existsSync(fragment)) {
        const frag = JSON.parse(fs.readFileSync(fragment, 'utf-8'))
        for (const k of Object.keys(frag.set)) en.add(k)
        removed = frag.remove || []
        for (const k of removed) en.delete(k)
    }
    const files = ['DnssecRolloverModal.jsx', 'DnssecDisableModal.jsx', 'DnssecParentDsCheck.jsx']
        .map((f) => path.join(SRC, 'components', 'dnssec', f))
    const used = new Set()
    for (const file of files) {
        for (const m of fs.readFileSync(file, 'utf-8').matchAll(/t\(\s*'((?:dnssec|zoneDetail|common|secretModal)\.[\w.]+)'/g)) used.add(m[1])
    }
    assert.ok(used.has('dnssec.dnskeyCheckForceConfirm') && used.has('dnssec.parentDsDisabled'))
    assert.deepEqual([...used].filter((k) => !en.has(k)), [])
    // entfernte Keys kommen im Quelltext nicht mehr vor
    const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory()
        ? (e.name === 'locales' ? [] : walk(path.join(dir, e.name)))
        : /\.(jsx?|mjs)$/.test(e.name) ? [path.join(dir, e.name)] : []))
    const sources = walk(SRC).map((f) => fs.readFileSync(f, 'utf-8')).join('\n')
    for (const k of removed) assert.equal(sources.includes(`'${k}'`), false, `${k} wird noch benutzt`)
})
