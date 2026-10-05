// merge-locale-fragments.mjs (Plan B.15, Regel 4): merge + loeschen, --keep, --check, Konfliktregeln.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

import { applyFragmentOps, mergeLocaleFragments, planMerge } from '../scripts/merge-locale-fragments.mjs'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FIX = path.join(HERE, 'fixtures')
const FRONTEND = path.resolve(HERE, '..')

// Kopiert eine Fixture (locales/ + fragments/) in ein Temp-Verzeichnis
function copyFixture(name) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), `merge-${name}-`))
    fs.cpSync(path.join(FIX, name), dir, { recursive: true })
    return { dir, localesDir: path.join(dir, 'locales'), fragmentsDir: path.join(dir, 'fragments') }
}

const readJson = (p) => JSON.parse(fs.readFileSync(p, 'utf-8'))
const jsonFiles = (d) => fs.readdirSync(d).filter((f) => f.endsWith('.json')).sort()

function writeFragment(fragmentsDir, name, data) {
    fs.writeFileSync(path.join(fragmentsDir, name), typeof data === 'string' ? data : JSON.stringify(data))
}

test('applyFragmentOps: erst remove, dann set; leere Eltern verschwinden; Strukturkonflikt wird gemeldet', () => {
    const locale = { a: { b: 'B', c: { d: 'D' } }, z: 'Z' }
    const { result, missingRemovals, conflicts } = applyFragmentOps(locale, {
        remove: ['a.c.d', 'nix.da'],
        set: { 'a.e': 'E', 'n.m_one': 'eins', 'z.x': 'kaputt', a: 'kaputt' },
    })
    assert.deepEqual(result, { a: { b: 'B', e: 'E' }, z: 'Z', n: { m_one: 'eins' } })
    assert.deepEqual(missingRemovals, ['nix.da'])
    assert.deepEqual(conflicts.map((c) => c.key).sort(), ['a', 'z.x'])
    assert.deepEqual(locale, { a: { b: 'B', c: { d: 'D' } }, z: 'Z' }, 'Eingabe bleibt unveraendert')
})

test('Normalfall: merge schreibt verschachtelt, entfernt Keys und loescht die verbrauchten Fragmente', () => {
    const fx = copyFixture('fragments-ok')
    try {
        const res = mergeLocaleFragments({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir })
        assert.deepEqual(res.errors, [])
        assert.deepEqual(res.written.sort(), ['de.json', 'en.json', 'sr.json'])
        assert.equal(res.deleted.length, 6)
        // nur README bleibt
        assert.deepEqual(fs.readdirSync(fx.fragmentsDir), ['README.md'])

        const de = readJson(path.join(fx.localesDir, 'de.json'))
        assert.deepEqual(de, {
            common: { save: 'Speichern', prev: 'Zurück' },
            zones: { title: 'Zonen', count_one: '{{count}} Zone', count_other: '{{count}} Zonen' },
            webhooks: { title: 'Webhooks' },
        })
        const sr = readJson(path.join(fx.localesDir, 'sr.json'))
        assert.equal(sr.zones.count_few, '{{count}} zone')
        assert.equal(sr.common.old, undefined)
        // Format: 2 Leerzeichen, abschliessender Zeilenumbruch
        const raw = fs.readFileSync(path.join(fx.localesDir, 'en.json'), 'utf-8')
        assert.ok(raw.startsWith('{\n  "common": {\n    "save": "Save",'))
        assert.ok(raw.endsWith('}\n'))

        // zweiter Lauf: keine Fragmente mehr, nichts zu tun
        const again = mergeLocaleFragments({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir })
        assert.deepEqual(again.errors, [])
        assert.deepEqual(again.written, [])
        assert.deepEqual(again.fragments, [])
    } finally {
        fs.rmSync(fx.dir, { recursive: true, force: true })
    }
})

test('--keep behaelt die Fragmente, --check schreibt und loescht nichts', () => {
    const fx = copyFixture('fragments-ok')
    try {
        const before = fs.readFileSync(path.join(fx.localesDir, 'de.json'), 'utf-8')
        const checked = mergeLocaleFragments({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir, check: true })
        assert.deepEqual(checked.errors, [])
        assert.deepEqual(checked.written, [])
        assert.deepEqual(checked.deleted, [])
        assert.ok(checked.plans.every((p) => p.changed))
        assert.equal(fs.readFileSync(path.join(fx.localesDir, 'de.json'), 'utf-8'), before)
        assert.equal(jsonFiles(fx.fragmentsDir).length, 6)

        const kept = mergeLocaleFragments({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir, keep: true })
        assert.deepEqual(kept.errors, [])
        assert.equal(kept.written.length, 3)
        assert.deepEqual(kept.deleted, [])
        assert.equal(jsonFiles(fx.fragmentsDir).length, 6)
        assert.notEqual(fs.readFileSync(path.join(fx.localesDir, 'de.json'), 'utf-8'), before)

        // idempotent: erneuter Lauf mit denselben Fragmenten aendert nichts mehr (remove-Keys fehlen -> Warnung)
        const again = mergeLocaleFragments({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir, keep: true })
        assert.deepEqual(again.errors, [])
        assert.deepEqual(again.written, [])
        assert.ok(again.warnings.some((w) => /common\.old/.test(w.message)))
    } finally {
        fs.rmSync(fx.dir, { recursive: true, force: true })
    }
})

test('Konfliktfall (Fixture fragments-conflict): gleicher Key, anderer Wert -> Fehler, nichts geschrieben/geloescht', () => {
    const fx = copyFixture('fragments-conflict')
    try {
        const before = fs.readFileSync(path.join(fx.localesDir, 'de.json'), 'utf-8')
        const res = mergeLocaleFragments({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir })
        assert.equal(res.errors.length, 1)
        assert.equal(res.errors[0].key, 'common.prev')
        assert.match(res.errors[0].message, /Konflikt.*ws-a\.de\.json/)
        assert.deepEqual(res.written, [])
        assert.deepEqual(res.deleted, [])
        assert.equal(fs.readFileSync(path.join(fx.localesDir, 'de.json'), 'utf-8'), before)
        assert.equal(jsonFiles(fx.fragmentsDir).length, 4)
        // en hat denselben Wert in beiden Fragmenten -> kein Konflikt
        assert.ok(!res.errors.some((e) => e.file.endsWith('.en.json')))
    } finally {
        fs.rmSync(fx.dir, { recursive: true, force: true })
    }
})

test('set auf einen Key, den ein anderes Fragment entfernt -> Fehler', () => {
    const fx = copyFixture('fragments-ok')
    try {
        for (const lang of ['en', 'de', 'sr']) {
            writeFragment(fx.fragmentsDir, `ws-c.${lang}.json`, { set: {}, remove: ['zones.title'] })
            const a = readJson(path.join(fx.fragmentsDir, `ws-a.${lang}.json`))
            a.set['zones.title'] = `Titel ${lang}`
            writeFragment(fx.fragmentsDir, `ws-a.${lang}.json`, a)
        }
        const res = planMerge({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir })
        assert.equal(res.errors.length, 3)
        assert.ok(res.errors.every((e) => /gesetzt und in ws-c\.\w+\.json entfernt/.test(e.message)))
    } finally {
        fs.rmSync(fx.dir, { recursive: true, force: true })
    }
})

test('fehlende Sprache, unbekannte Sprache, falscher Dateiname, kaputtes JSON, falsche Struktur -> Fehler', () => {
    const fx = copyFixture('fragments-ok')
    try {
        fs.unlinkSync(path.join(fx.fragmentsDir, 'ws-b.sr.json'))
        writeFragment(fx.fragmentsDir, 'ws-a.xx.json', { set: { 'common.prev': 'X' } })
        writeFragment(fx.fragmentsDir, 'WS-D.de.json', { set: {} })
        writeFragment(fx.fragmentsDir, 'ws-e.de.json', '{ kaputt')
        writeFragment(fx.fragmentsDir, 'ws-f.de.json', { set: { 'a.b': 1, 'leer': '  ', 'bad key': 'x' }, remove: 'a', extra: true })
        const res = planMerge({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir })
        const msgs = res.errors.map((e) => `${e.file}: ${e.message}`)
        const has = (re) => assert.ok(msgs.some((m) => re.test(m)), `erwartet ${re}, bekommen:\n${msgs.join('\n')}`)
        has(/^ws-b\.\*\.json: fehlende Sprache\(n\): sr$/)
        has(/^ws-a\.xx\.json: Sprache "xx" hat keine Locale-Datei/)
        has(/^WS-D\.de\.json: Dateiname/)
        has(/^ws-e\.de\.json: kein gueltiges JSON/)
        has(/^ws-f\.de\.json: unbekanntes Feld "extra"/)
        has(/^ws-f\.de\.json: "remove" muss eine Liste sein/)
        has(/^ws-f\.de\.json: Wert von "a\.b" ist kein String/)
        has(/^ws-f\.de\.json: Wert von "leer" ist leer/)
        has(/^ws-f\.de\.json: ungueltiger Key "bad key"/)
        const run = mergeLocaleFragments({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir })
        assert.deepEqual(run.written, [])
        assert.deepEqual(run.deleted, [])
    } finally {
        fs.rmSync(fx.dir, { recursive: true, force: true })
    }
})

test('Strukturkonflikt zwischen Fragmenten (a.b und a.b.c) und gegen den Bestand', () => {
    const fx = copyFixture('fragments-ok')
    try {
        for (const lang of ['en', 'de', 'sr']) {
            writeFragment(fx.fragmentsDir, `ws-g.${lang}.json`, { set: { 'webhooks.title.sub': 'x', 'common.save.deep': 'y' } })
        }
        const res = planMerge({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir })
        const msgs = res.errors.map((e) => e.message)
        assert.equal(msgs.filter((m) => /Strukturkonflikt mit "webhooks\.title"/.test(m)).length, 3)
        assert.equal(msgs.filter((m) => /"common\.save" ist ein Text/.test(m)).length, 3)
    } finally {
        fs.rmSync(fx.dir, { recursive: true, force: true })
    }
})

test('Warnung bei abweichendem Key-Satz innerhalb eines Workstreams (Plural-Suffixe normalisiert)', () => {
    const fx = copyFixture('fragments-ok')
    try {
        const de = readJson(path.join(fx.fragmentsDir, 'ws-b.de.json'))
        de.set['webhooks.extra'] = 'Nur deutsch'
        writeFragment(fx.fragmentsDir, 'ws-b.de.json', de)
        const res = planMerge({ localesDir: fx.localesDir, fragmentsDir: fx.fragmentsDir })
        assert.deepEqual(res.errors, [])
        // ws-a.sr hat count_few zusaetzlich -> keine Warnung (normalisiert)
        assert.deepEqual(res.warnings.map((w) => w.file), ['ws-b.de.json'])
        assert.match(res.warnings[0].message, /webhooks\.extra/)
    } finally {
        fs.rmSync(fx.dir, { recursive: true, force: true })
    }
})

test('CLI: --check gegen den echten Stand meldet keine Fehler und aendert nichts', () => {
    const localesDir = path.join(FRONTEND, 'src', 'locales')
    const snapshot = Object.fromEntries(jsonFiles(localesDir).map((f) => [f, fs.readFileSync(path.join(localesDir, f), 'utf-8')]))
    const fragDir = path.join(localesDir, 'fragments')
    const frags = fs.existsSync(fragDir) ? jsonFiles(fragDir) : []
    const r = spawnSync(process.execPath, ['scripts/merge-locale-fragments.mjs', '--check'], { cwd: FRONTEND, encoding: 'utf-8' })
    assert.equal(r.status, 0, r.stdout + r.stderr)
    for (const [f, content] of Object.entries(snapshot)) assert.equal(fs.readFileSync(path.join(localesDir, f), 'utf-8'), content)
    assert.deepEqual(fs.existsSync(fragDir) ? jsonFiles(fragDir) : [], frags)

    const bad = spawnSync(process.execPath, ['scripts/merge-locale-fragments.mjs', '--nope'], { cwd: FRONTEND, encoding: 'utf-8' })
    assert.equal(bad.status, 2)
})
