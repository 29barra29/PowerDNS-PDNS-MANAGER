#!/usr/bin/env node
// Synchronisiert alle Locale-Dateien gegen en.json (= source of truth). Werkzeug fuer Uebersetzer, nicht im Build.
// - Fehlende Keys werden mit dem englischen Wert eingefuegt (Reihenfolge wie in en.json).
// - Keys, die nicht mehr in en.json existieren, werden entfernt.
// - Bestehende Uebersetzungen bleiben unangetastet.
// - Pluralformen (F8-A02): Hat en `base_one` oder `base_other`, bleiben in der Zielsprache die Geschwister
//   `base_zero|_two|_few|_many` erhalten (direkt nach `base_one`, sonst am Ende des Objekts) und zaehlen nicht
//   als "entfernt". Fehlt eine von Intl.PluralRules(<sprache>) geforderte Form, wird sie mit dem Wert von
//   `base_other` der Zielsprache angelegt und als NEU (bitte uebersetzen) gelistet.
// - Am Ende wird pro Sprache geloggt, was sich geaendert hat.
//
// Aufruf:  node scripts/sync-locales.mjs [--dir <pfad-zu-locales>]
//          (Default: frontend/src/locales relativ zum Repo)
// Im Anschluss `git diff frontend/src/locales/` ansehen, dann committen.
// Hinweis 3.0: Locale-Dateien werden von Workstreams nicht direkt geaendert (Fragmente, siehe
// frontend/src/locales/fragments/README.md); dieses Skript ist fuer Pflege-Commits des Integrators gedacht.

import fs from 'node:fs'
import path from 'node:path'
import url from 'node:url'

const __dirname = path.dirname(url.fileURLToPath(import.meta.url))

function parseArgs(argv) {
    const args = { dir: null }
    for (let i = 0; i < argv.length; i++) {
        const a = argv[i]
        if (a === '--dir') {
            args.dir = argv[++i]
            if (!args.dir) throw new Error('--dir braucht einen Pfad')
        } else if (a.startsWith('--dir=')) {
            args.dir = a.slice('--dir='.length)
        } else {
            throw new Error(`Unbekanntes Argument: ${a}`)
        }
    }
    return args
}

let args
try {
    args = parseArgs(process.argv.slice(2))
} catch (e) {
    console.error(e.message)
    console.error('Aufruf: node scripts/sync-locales.mjs [--dir <pfad-zu-locales>]')
    process.exit(2)
}

const localesDir = args.dir
    ? path.resolve(process.cwd(), args.dir)
    : path.resolve(__dirname, '..', 'frontend', 'src', 'locales')
const sourceFile = path.join(localesDir, 'en.json')

const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf-8'))
const writeJson = (file, obj) => fs.writeFileSync(file, JSON.stringify(obj, null, 2) + '\n', 'utf-8')

const EXTRA_PLURAL_SUFFIXES = ['zero', 'two', 'few', 'many']
const CATEGORY_ORDER = ['zero', 'one', 'two', 'few', 'many', 'other']

function pluralCategories(lang) {
    try {
        return new Intl.PluralRules(lang).resolvedOptions().pluralCategories
    } catch {
        return ['one', 'other']
    }
}

// Basen, die in diesem en-Objekt (eine Ebene) als Plural vorkommen
function pluralBases(source) {
    const bases = new Set()
    for (const key of Object.keys(source)) {
        const m = /^(.+)_(one|other)$/.exec(key)
        if (m && typeof source[key] === 'string') bases.add(m[1])
    }
    return bases
}

// Geht en.json Schluessel fuer Schluessel durch und baut ein neues Objekt:
// - Existiert der Key in der Zielsprache: dortigen Wert uebernehmen
// - Sonst: englischen Wert als Fallback einsetzen (UI zeigt damit wenigstens englisch)
// - Plural-Geschwister der Zielsprache bleiben erhalten, geforderte Formen werden ergaenzt
// Liefert zusaetzlich added/removed-Keys zurueck, damit man sieht, was passiert ist.
function syncObject(source, target, lang, prefix = '') {
    const merged = {}
    const added = []
    const removed = []
    const required = pluralCategories(lang)
    const bases = pluralBases(source)
    const kept = new Set() // Keys der Zielsprache, die als Plural-Geschwister uebernommen wurden

    // Plural-Geschwister (zero/two/few/many) fuer base einfuegen: vorhandene behalten, geforderte ergaenzen
    const insertSiblings = (base) => {
        for (const cat of CATEGORY_ORDER) {
            if (!EXTRA_PLURAL_SUFFIXES.includes(cat)) continue
            const key = `${base}_${cat}`
            if (key in source || key in merged) continue
            const fullKey = prefix ? `${prefix}.${key}` : key
            if (target && Object.prototype.hasOwnProperty.call(target, key) && typeof target[key] === 'string') {
                merged[key] = target[key]
                kept.add(key)
            } else if (required.includes(cat)) {
                const otherKey = `${base}_other`
                const fallback = (target && typeof target[otherKey] === 'string') ? target[otherKey] : source[otherKey]
                if (typeof fallback === 'string') {
                    merged[key] = fallback
                    added.push(fullKey)
                }
            }
        }
    }

    for (const key of Object.keys(source)) {
        const fullKey = prefix ? `${prefix}.${key}` : key
        const sVal = source[key]
        const tVal = target?.[key]

        if (sVal !== null && typeof sVal === 'object' && !Array.isArray(sVal)) {
            const child = syncObject(sVal, (tVal && typeof tVal === 'object') ? tVal : {}, lang, fullKey)
            merged[key] = child.merged
            added.push(...child.added)
            removed.push(...child.removed)
        } else if (target && Object.prototype.hasOwnProperty.call(target, key)) {
            merged[key] = tVal
        } else {
            merged[key] = sVal
            added.push(fullKey)
        }

        // Geschwister direkt nach base_one einsortieren
        const m = /^(.+)_one$/.exec(key)
        if (m && bases.has(m[1])) insertSiblings(m[1])
    }
    // Plural-Basen ohne base_one in en: Geschwister ans Ende
    for (const base of bases) {
        if (!(`${base}_one` in source)) insertSiblings(base)
    }

    if (target) {
        for (const key of Object.keys(target)) {
            if (!(key in source) && !kept.has(key)) {
                const fullKey = prefix ? `${prefix}.${key}` : key
                removed.push(fullKey)
            }
        }
    }

    return { merged, added, removed }
}

const en = readJson(sourceFile)
const targets = fs.readdirSync(localesDir)
    .filter((f) => f.endsWith('.json') && f !== 'en.json')
    .sort()

let changedFiles = 0
for (const file of targets) {
    const filePath = path.join(localesDir, file)
    const lang = file.replace(/\.json$/, '')
    const target = readJson(filePath)
    const { merged, added, removed } = syncObject(en, target, lang)

    const before = JSON.stringify(target)
    const after = JSON.stringify(merged)

    if (before === after) {
        console.log(`✓ ${file.padEnd(10)}  schon synchron`)
        continue
    }

    writeJson(filePath, merged)
    changedFiles++
    console.log(`↻ ${file.padEnd(10)}  +${added.length} hinzugefuegt, -${removed.length} entfernt`)
    if (added.length) {
        console.log(`    NEU (Fallback eingesetzt, bitte uebersetzen):`)
        for (const k of added.slice(0, 12)) console.log(`      • ${k}`)
        if (added.length > 12) console.log(`      … und ${added.length - 12} weitere`)
    }
    if (removed.length) {
        console.log(`    ENTFERNT (waren nicht mehr in en.json):`)
        for (const k of removed.slice(0, 12)) console.log(`      • ${k}`)
        if (removed.length > 12) console.log(`      … und ${removed.length - 12} weitere`)
    }
}

const shownDir = path.relative(process.cwd(), localesDir) || '.'
console.log(`\nFertig. ${changedFiles} Datei(en) geaendert. Jetzt: git diff ${shownDir}`)
