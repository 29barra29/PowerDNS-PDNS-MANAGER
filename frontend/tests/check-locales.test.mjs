// check-locales.mjs (F8 §6.6/§9.2, Plan B.15) und sync-locales.mjs (Plural-Erhalt, F8-A02).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

import { checkLocales, interpolationVars, loadAllowlist, matchesPattern, splitPlural } from '../scripts/check-locales.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FIX = path.join(HERE, 'fixtures')
const FRONTEND = path.resolve(HERE, '..')
const REPO = path.resolve(FRONTEND, '..')

// Gemeinsame Allowlist fuer die Fixtures: Marke identisch, dynamischer Praefix im Mini-src
const ALLOW = { global: ['common.brand'], dynamic: ['dyn.err.*'] }

function run(name, allowlist = ALLOW) {
    const dir = path.join(FIX, name)
    const srcDir = path.join(dir, 'src')
    return checkLocales({ localesDir: dir, srcDir: fs.existsSync(srcDir) ? srcDir : null, allowlist })
}

const rules = (list) => [...new Set(list.map((f) => f.rule))].sort()

function tmpDir(prefix) {
    return fs.mkdtempSync(path.join(os.tmpdir(), prefix))
}

// ------------------------------------------------------------------ Hilfsfunktionen
test('splitPlural / matchesPattern / interpolationVars', () => {
    assert.deepEqual(splitPlural('zones.count_few'), { base: 'zones.count', category: 'few' })
    assert.equal(splitPlural('zones.count'), null)
    assert.equal(matchesPattern('zoneDetail.rdataPh*', 'zoneDetail.rdataPhA'), true)
    assert.equal(matchesPattern('zones.count', 'zones.count_one'), true)
    assert.equal(matchesPattern('zones.count', 'zones.countX'), false)
    assert.deepEqual([...interpolationVars('{{a}} {{ b }} {{- c}} {{d, number}} {{a}}')], ['a', 'b', 'c', 'd'])
})

// ------------------------------------------------------------------ Fixtures
test('locales-ok: keine Fehler, keine Warnungen', () => {
    const res = run('locales-ok')
    assert.deepEqual(res.errors, [])
    assert.deepEqual(res.warnings, [])
    assert.deepEqual(res.languages.sort(), ['de', 'en', 'sr'])
})

test('locales-missing-key: nur E4 (fehlender Key)', () => {
    const res = run('locales-missing-key')
    assert.deepEqual(rules(res.errors), ['E4'])
    assert.equal(res.errors.length, 1)
    assert.equal(res.errors[0].file, 'de.json')
    assert.equal(res.errors[0].key, 'common.greet')
})

test('locales-missing-few: nur E5 (sr ohne _few)', () => {
    const res = run('locales-missing-few')
    assert.deepEqual(rules(res.errors), ['E5'])
    assert.equal(res.errors[0].file, 'sr.json')
    assert.match(res.errors[0].message, /items\.count_few/)
})

test('locales-var-mismatch: nur E6', () => {
    const res = run('locales-var-mismatch')
    assert.deepEqual(rules(res.errors), ['E6'])
    assert.equal(res.errors[0].key, 'common.greet')
    assert.match(res.errors[0].message, /\{\{name\}\}/)
    assert.match(res.errors[0].message, /\{\{nme\}\}/)
})

test('locales-identical: W1 als Warnung; Allowlist (perLanguage) unterdrueckt sie; veralteter Eintrag -> W2', () => {
    const res = run('locales-identical')
    assert.deepEqual(res.errors, [])
    assert.deepEqual(rules(res.warnings), ['W1'])
    assert.equal(res.warnings[0].key, 'common.save')

    const allowed = run('locales-identical', { ...ALLOW, perLanguage: { de: ['common.save'] } })
    assert.deepEqual(allowed.warnings, [])

    const stale = run('locales-identical', { ...ALLOW, perLanguage: { de: ['common.save', 'common.greet', 'gibt.es.nicht'], hu: ['common.save'] } })
    assert.deepEqual(rules(stale.warnings), ['W2'])
    assert.deepEqual(stale.warnings.map((w) => w.key).sort(), ['common.greet', 'common.save', 'gibt.es.nicht'])
})

test('locales-identical: Werte ohne Buchstaben sind ausgenommen, globaler Eintrag ohne Treffer -> W2', () => {
    const res = run('locales-ok', { ...ALLOW, global: ['common.brand', 'common.greet', 'nix.*'] })
    assert.deepEqual(res.warnings.map((w) => `${w.rule}:${w.key}`).sort(), ['W2:common.greet', 'W2:nix.*'])
})

test('locales-empty-value: nur E3', () => {
    const res = run('locales-empty-value')
    assert.deepEqual(rules(res.errors), ['E3'])
    assert.equal(res.errors[0].key, 'common.save')
})

test('locales-unknown-static-key: nur E7, dynamische Praefixe und Template-Literale ignoriert', () => {
    const res = run('locales-unknown-static-key')
    assert.deepEqual(rules(res.errors), ['E7'])
    assert.equal(res.errors.length, 1)
    assert.equal(res.errors[0].key, 'common.missing')
    assert.match(res.errors[0].file, /^src\/demo\.js:\d+$/)

    // ohne dynamic-Eintrag wird der verkettete Praefix gemeldet
    const noDyn = run('locales-ok', { global: ['common.brand'] })
    assert.deepEqual(noDyn.errors.map((e) => `${e.rule}:${e.key}`), ['E7:dyn.err.'])
    assert.match(noDyn.errors[0].message, /dynamic/)
})

// ------------------------------------------------------------------ E1, E2, E5-Sonderfaelle (Temp-Verzeichnisse)
function writeLocales(dir, locales) {
    for (const [name, content] of Object.entries(locales)) {
        fs.writeFileSync(path.join(dir, name), typeof content === 'string' ? content : JSON.stringify(content))
    }
}

test('E1/E2: ungueltiges JSON, Nicht-Objekt, Nicht-String-Werte', () => {
    const dir = tmpDir('locales-e1-')
    try {
        writeLocales(dir, {
            'en.json': { a: 'A', b: 'B' },
            'de.json': '{ "a": "A1", ',
            'hu.json': '["x"]',
            'sr.json': { a: ['x'], b: null },
        })
        const res = checkLocales({ localesDir: dir })
        const got = res.errors.map((e) => `${e.rule}:${e.file}:${e.key ?? ''}`).sort()
        assert.deepEqual(got, ['E1:de.json:', 'E1:hu.json:', 'E2:sr.json:a', 'E2:sr.json:b', 'E4:sr.json:a', 'E4:sr.json:b'])
    } finally {
        fs.rmSync(dir, { recursive: true, force: true })
    }
})

test('E5: Key zugleich Basis und Plural, Plural ohne en-Plural, ueberzaehlige Form = Warnung', () => {
    const dir = tmpDir('locales-e5-')
    try {
        writeLocales(dir, {
            'en.json': { n_one: '{{count}} x', n_other: '{{count}} xs', plain: 'Plain {{v}}' },
            'de.json': { n: 'kaputt', n_one: '{{count}} X', n_other: '{{count}} Xe', plain_one: 'Eins {{v}}', plain_other: 'Viele {{v}}' },
            'hu.json': { n_one: '{{count}} y', n_few: '{{count}} yy', n_other: '{{count}} y', plain: 'Sima {{v}}' },
        })
        const res = checkLocales({ localesDir: dir })
        const errs = res.errors.map((e) => `${e.rule}:${e.file}:${e.key}`).sort()
        assert.deepEqual(errs, ['E5:de.json:n', 'E5:de.json:plain'])
        assert.deepEqual(res.warnings.map((w) => `${w.rule}:${w.file}:${w.key}`), ['E5:hu.json:n_few'])
    } finally {
        fs.rmSync(dir, { recursive: true, force: true })
    }
})

test('E6: count darf in _one fehlen, sonst nicht', () => {
    const dir = tmpDir('locales-e6-')
    try {
        writeLocales(dir, {
            'en.json': { n_one: 'one item in {{zone}}', n_other: '{{count}} items in {{zone}}' },
            'sr.json': { n_one: 'jedna stavka u {{zone}}', n_few: 'stavke u {{zone}}', n_other: '{{count}} stavki u {{zone}}' },
        })
        const res = checkLocales({ localesDir: dir })
        assert.deepEqual(res.errors.map((e) => `${e.rule}:${e.key}`), ['E6:n_few'])
    } finally {
        fs.rmSync(dir, { recursive: true, force: true })
    }
})

// ------------------------------------------------------------------ Allowlist + .d
test('loadAllowlist vereinigt Basisdatei und locale-allowlist.d/*.json; fehlender reason -> W3', () => {
    const dir = path.join(FIX, 'allowlist-d')
    const al = loadAllowlist({ file: path.join(dir, 'locale-allowlist.json'), dir: path.join(dir, 'locale-allowlist.d') })
    assert.deepEqual(al.global.map((e) => e.key), ['common.brand', 'items.count', 'zz.*'])
    assert.deepEqual(al.perLanguage.de.map((e) => e.key), ['common.save', 'common.greet'])
    assert.deepEqual(al.perLanguage.sr.map((e) => e.key), ['common.save'])
    assert.deepEqual(al.dynamic.map((e) => e.key), ['dyn.err.*', 'dyn.warn.*'])
    assert.deepEqual(al.sources, ['locale-allowlist.json', 'locale-allowlist.d/ws-a.json', 'locale-allowlist.d/ws-b.json'])
    assert.equal(al.global[1].source, 'locale-allowlist.d/ws-a.json')

    // gegen locales-identical: de common.save erlaubt; uebrige Eintraege treffen nicht -> W2, ws-b ohne reason -> W3
    const res = checkLocales({ localesDir: path.join(FIX, 'locales-identical'), srcDir: path.join(FIX, 'locales-identical', 'src'), allowlist: al })
    assert.deepEqual(res.errors, [])
    const w = res.warnings.map((x) => `${x.rule}:${x.file}:${x.key ?? ''}`).sort()
    assert.deepEqual(w, [
        'W2:locale-allowlist.d/ws-a.json:common.greet',
        'W2:locale-allowlist.d/ws-a.json:common.save',
        'W2:locale-allowlist.d/ws-a.json:items.count',
        'W2:locale-allowlist.d/ws-b.json:zz.*',
        'W3:locale-allowlist.d/ws-b.json:',
    ])
})

test('loadAllowlist bricht bei unbekannten Feldern und falschen Typen ab', () => {
    const dir = tmpDir('allowlist-bad-')
    try {
        fs.mkdirSync(path.join(dir, 'd'))
        fs.writeFileSync(path.join(dir, 'd', 'x.json'), JSON.stringify({ reason: 'r', globl: [] }))
        assert.throws(() => loadAllowlist({ file: null, dir: path.join(dir, 'd') }), /unbekanntes Feld "globl"/)
        fs.writeFileSync(path.join(dir, 'd', 'x.json'), JSON.stringify({ reason: 'r', global: 'a.b' }))
        assert.throws(() => loadAllowlist({ file: null, dir: path.join(dir, 'd') }), /global/)
        fs.writeFileSync(path.join(dir, 'd', 'x.json'), '{ kaputt')
        assert.throws(() => loadAllowlist({ file: null, dir: path.join(dir, 'd') }), /kein gueltiges JSON/)
    } finally {
        fs.rmSync(dir, { recursive: true, force: true })
    }
})

test('echte Allowlist (Basis + .d) ist gueltig und enthaelt die dynamischen Praefixe aus B.15', () => {
    const al = loadAllowlist()
    const dyn = al.dynamic.map((e) => e.key)
    for (const p of ['lua.err.*', 'propagation.note_*', 'webhooks.event.*', 'audit.actions.*', 'login.ssoError.*']) {
        assert.ok(dyn.includes(p), `dynamischer Praefix ${p} fehlt`)
    }
    assert.ok(al.global.some((e) => e.key === 'zoneDetail.rdataPh*'))
})

// ------------------------------------------------------------------ CLI gegen die echten Locales
test('CLI laeuft ohne --strict gegen die echten Locales durch (Befunde erlaubt, kein Absturz)', () => {
    const r = spawnSync(process.execPath, ['scripts/check-locales.mjs'], { cwd: FRONTEND, encoding: 'utf-8' })
    assert.ok([0, 1].includes(r.status), `Exit-Code ${r.status}: ${r.stderr}`)
    assert.equal(r.stderr, '')
    assert.match(r.stdout, /Locales ok|\d+ Fehler, \d+ Warnungen/)

    const j = spawnSync(process.execPath, ['scripts/check-locales.mjs', '--json', '--with-fragments'], { cwd: FRONTEND, encoding: 'utf-8' })
    assert.ok([0, 1].includes(j.status), j.stderr)
    const parsed = JSON.parse(j.stdout)
    assert.ok(Array.isArray(parsed.errors) && Array.isArray(parsed.warnings))
    // die Keys aus den Fragmenten dieses Stands sind nach virtuellem Merge bekannt
    assert.ok(!parsed.errors.some((e) => e.rule === 'M'), JSON.stringify(parsed.errors.filter((e) => e.rule === 'M')))

    const bad = spawnSync(process.execPath, ['scripts/check-locales.mjs', '--bogus'], { cwd: FRONTEND, encoding: 'utf-8' })
    assert.equal(bad.status, 2)
})

test('--strict macht Warnungen zu Fehlern (Exit 1)', () => {
    // im Repo-Stand gibt es W1-Warnungen bis W0-I18N; daher nur pruefen, dass strict nie "besser" ist als normal
    const normal = spawnSync(process.execPath, ['scripts/check-locales.mjs', '--json'], { cwd: FRONTEND, encoding: 'utf-8' })
    const strict = spawnSync(process.execPath, ['scripts/check-locales.mjs', '--json', '--strict'], { cwd: FRONTEND, encoding: 'utf-8' })
    const n = JSON.parse(normal.stdout)
    const s = JSON.parse(strict.stdout)
    assert.equal(s.errors.length, n.errors.length + n.warnings.length)
    assert.equal(s.warnings.length, 0)
    if (s.errors.length) assert.equal(strict.status, 1)
})

// ------------------------------------------------------------------ sync-locales.mjs (F8-A02)
test('sync-locales --dir: x_few bleibt erhalten, fehlende Pluralform wird ergaenzt, Rest wie bisher', () => {
    const dir = tmpDir('sync-locales-')
    try {
        for (const f of ['en.json', 'de.json', 'sr.json']) fs.copyFileSync(path.join(FIX, 'locales-ok', f), path.join(dir, f))
        // hr: _few fehlt, veralteter Key vorhanden, neuer en-Key fehlt
        fs.writeFileSync(path.join(dir, 'hr.json'), JSON.stringify({
            common: { save: 'Spremi', greet: 'Bok {{name}}', brand: 'PDNS Manager', stale: 'weg' },
            items: { count_one: '{{count}} stavka', count_other: '{{count}} stavki' },
        }))
        const r = spawnSync(process.execPath, [path.join(REPO, 'scripts', 'sync-locales.mjs'), '--dir', dir], { encoding: 'utf-8' })
        assert.equal(r.status, 0, r.stderr)

        const sr = JSON.parse(fs.readFileSync(path.join(dir, 'sr.json'), 'utf-8'))
        assert.equal(sr.items.count_few, '{{count}} stavke')
        assert.deepEqual(Object.keys(sr.items), ['count_one', 'count_few', 'count_other'])
        assert.match(r.stdout, /sr\.json\s+schon synchron/)

        const hr = JSON.parse(fs.readFileSync(path.join(dir, 'hr.json'), 'utf-8'))
        assert.deepEqual(Object.keys(hr.items), ['count_one', 'count_few', 'count_other'])
        assert.equal(hr.items.count_few, '{{count}} stavki') // Wert von base_other der Zielsprache
        assert.equal(hr.common.stale, undefined)
        assert.match(r.stdout, /items\.count_few/)
        assert.match(r.stdout, /common\.stale/)

        const de = JSON.parse(fs.readFileSync(path.join(dir, 'de.json'), 'utf-8'))
        assert.deepEqual(Object.keys(de.items), ['count_one', 'count_other'])

        // zweiter Lauf: nichts mehr zu tun
        const r2 = spawnSync(process.execPath, [path.join(REPO, 'scripts', 'sync-locales.mjs'), '--dir', dir], { encoding: 'utf-8' })
        assert.match(r2.stdout, /0 Datei\(en\) geaendert/)
    } finally {
        fs.rmSync(dir, { recursive: true, force: true })
    }
})
