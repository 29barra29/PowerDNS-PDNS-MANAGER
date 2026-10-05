#!/usr/bin/env node
// Locale-Fragmente in die Locale-Dateien mergen (Plan B.15, Regel 4).
//
// Aufruf (cwd egal, Pfade relativ zu dieser Datei):
//   node scripts/merge-locale-fragments.mjs            # mergen, schreiben, gemergte Fragmente LOESCHEN
//   node scripts/merge-locale-fragments.mjs --keep     # mergen und schreiben, Fragmente behalten (lokaler Probelauf)
//   node scripts/merge-locale-fragments.mjs --check    # nur pruefen und melden, nichts schreiben/loeschen
//   Zusatz: --json (Ergebnis als JSON)
//
// Fragment: src/locales/fragments/<ws>.<lang>.json =
//   { "set": { "a.b.c": "Text", "x_one": "...", "x_few": "..." }, "remove": ["alter.key"] }
// Flache Punkt-Keys, Plural-Suffixe explizit. Ablauf je Sprache: erst alle remove, dann alle set
// (verschachtelt zurueckgeschrieben, neue Keys am Ende ihres Elternobjekts, 2 Leerzeichen Einrueckung).
//
// Fehler (dann wird NICHTS geschrieben oder geloescht):
//   - Dateiname nicht <ws>.<lang>.json, ungueltiges JSON, falsche Struktur, leere Werte
//   - Sprache ohne Locale-Datei; Workstream liefert nicht alle Sprachen (fehlende Sprache)
//   - gleicher Key mit abweichendem Wert in zwei Fragmenten
//   - set auf einen Key, den ein Fragment (auch dasselbe) per remove entfernt
//   - Strukturkonflikt (Key waere zugleich Text und Objekt)
// Warnungen: remove auf nicht vorhandenen Key; Key-Satz eines Workstreams weicht zwischen Sprachen ab.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
export const DEFAULT_PATHS = Object.freeze({
    localesDir: path.resolve(SCRIPT_DIR, '..', 'src', 'locales'),
    fragmentsDir: path.resolve(SCRIPT_DIR, '..', 'src', 'locales', 'fragments'),
})

const FRAGMENT_RE = /^([a-z0-9][a-z0-9_-]*(?:\.[a-z0-9_-]+)*)\.([a-z]{2,3}(?:-[a-zA-Z0-9]+)?)\.json$/
const KEY_RE = /^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$/
const PLURAL_RE = /_(zero|one|two|few|many|other)$/

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

// ---------------------------------------------------------------------------------------------
// Objekt-Helfer (verschachtelt <-> Punkt-Keys)

function getPath(obj, key) {
    let cur = obj
    for (const part of key.split('.')) {
        if (!isPlainObject(cur) || !Object.prototype.hasOwnProperty.call(cur, part)) return undefined
        cur = cur[part]
    }
    return cur
}

// Entfernt key; leere Elternobjekte werden mit entfernt. Rueckgabe: true, wenn etwas entfernt wurde.
function removePath(obj, key) {
    const parts = key.split('.')
    const stack = []
    let cur = obj
    for (let i = 0; i < parts.length - 1; i++) {
        if (!isPlainObject(cur[parts[i]])) return false
        stack.push([cur, parts[i]])
        cur = cur[parts[i]]
    }
    const last = parts[parts.length - 1]
    if (!Object.prototype.hasOwnProperty.call(cur, last)) return false
    delete cur[last]
    for (let i = stack.length - 1; i >= 0; i--) {
        const [parent, name] = stack[i]
        if (Object.keys(parent[name]).length === 0) delete parent[name]
        else break
    }
    return true
}

// Setzt key; wirft bei Strukturkonflikt.
function setPath(obj, key, value) {
    const parts = key.split('.')
    let cur = obj
    for (let i = 0; i < parts.length - 1; i++) {
        const p = parts[i]
        if (cur[p] === undefined) cur[p] = {}
        else if (!isPlainObject(cur[p])) {
            throw new Error(`"${parts.slice(0, i + 1).join('.')}" ist ein Text, "${key}" braucht dort ein Objekt`)
        }
        cur = cur[p]
    }
    const last = parts[parts.length - 1]
    if (isPlainObject(cur[last])) throw new Error(`"${key}" ist ein Objekt und kann nicht durch einen Text ersetzt werden`)
    cur[last] = value
}

const clone = (v) => JSON.parse(JSON.stringify(v))

// Reine Funktion: wendet remove (zuerst) und set auf eine Kopie von locale an.
// ops: { remove: [key], set: { key: value } }. Rueckgabe { result, missingRemovals, conflicts }.
export function applyFragmentOps(locale, ops) {
    const result = clone(locale)
    const missingRemovals = []
    const conflicts = []
    for (const key of ops.remove || []) {
        if (!removePath(result, key)) missingRemovals.push(key)
    }
    for (const [key, value] of Object.entries(ops.set || {})) {
        try {
            setPath(result, key, value)
        } catch (e) {
            conflicts.push({ key, message: e.message })
        }
    }
    return { result, missingRemovals, conflicts }
}

// ---------------------------------------------------------------------------------------------
// Fragmente lesen und pruefen

export function readFragments(fragmentsDir) {
    const fragments = []
    const errors = []
    if (!fs.existsSync(fragmentsDir)) return { fragments, errors }
    const names = fs.readdirSync(fragmentsDir).filter((f) => f.endsWith('.json')).sort()
    for (const name of names) {
        const m = FRAGMENT_RE.exec(name)
        if (!m) {
            errors.push({ file: name, message: 'Dateiname muss <ws>.<lang>.json lauten (klein geschrieben)' })
            continue
        }
        const [, ws, lang] = m
        let data
        try {
            data = JSON.parse(fs.readFileSync(path.join(fragmentsDir, name), 'utf-8'))
        } catch (e) {
            errors.push({ file: name, message: `kein gueltiges JSON: ${e.message}` })
            continue
        }
        const problems = []
        if (!isPlainObject(data)) problems.push('oberste Ebene muss ein Objekt sein')
        else {
            for (const k of Object.keys(data)) if (!['set', 'remove', '$comment'].includes(k)) problems.push(`unbekanntes Feld "${k}"`)
            if (data.set !== undefined && !isPlainObject(data.set)) problems.push('"set" muss ein Objekt sein')
            if (data.remove !== undefined && !Array.isArray(data.remove)) problems.push('"remove" muss eine Liste sein')
            for (const [k, v] of Object.entries(isPlainObject(data.set) ? data.set : {})) {
                if (!KEY_RE.test(k)) problems.push(`ungueltiger Key "${k}"`)
                if (typeof v !== 'string') problems.push(`Wert von "${k}" ist kein String`)
                else if (!v.trim()) problems.push(`Wert von "${k}" ist leer`)
            }
            for (const k of Array.isArray(data.remove) ? data.remove : []) {
                if (typeof k !== 'string' || !KEY_RE.test(k)) problems.push(`ungueltiger remove-Eintrag ${JSON.stringify(k)}`)
            }
        }
        if (problems.length) {
            for (const p of problems) errors.push({ file: name, message: p })
            continue
        }
        fragments.push({ file: name, ws, lang, set: data.set || {}, remove: data.remove || [] })
    }
    return { fragments, errors }
}

function listLanguages(localesDir) {
    return fs.readdirSync(localesDir).filter((f) => f.endsWith('.json')).map((f) => f.replace(/\.json$/, '')).sort()
}

// Prueft Fragmente gegeneinander und gegen die Locales; berechnet die neuen Locale-Inhalte.
export function planMerge({ localesDir = DEFAULT_PATHS.localesDir, fragmentsDir = DEFAULT_PATHS.fragmentsDir } = {}) {
    const languages = listLanguages(localesDir)
    const { fragments, errors: readErrors } = readFragments(fragmentsDir)
    const errors = readErrors.map((e) => ({ ...e }))
    const warnings = []

    // Sprachen: unbekannte Sprache / fehlende Sprache je Workstream
    const byWs = new Map()
    for (const f of fragments) {
        if (!languages.includes(f.lang)) errors.push({ file: f.file, message: `Sprache "${f.lang}" hat keine Locale-Datei` })
        if (!byWs.has(f.ws)) byWs.set(f.ws, [])
        byWs.get(f.ws).push(f)
    }
    for (const [ws, list] of byWs) {
        const have = new Set(list.map((f) => f.lang))
        const missing = languages.filter((l) => !have.has(l))
        if (missing.length) errors.push({ file: `${ws}.*.json`, message: `fehlende Sprache(n): ${missing.join(', ')}` })
        // Key-Satz je Sprache vergleichen (Plural-Suffixe normalisiert)
        const norm = (f) => new Set([...Object.keys(f.set)].map((k) => k.replace(PLURAL_RE, '')))
        const ref = list.find((f) => f.lang === 'en') || list[0]
        const refKeys = norm(ref)
        for (const f of list) {
            if (f === ref) continue
            const keys = norm(f)
            const diff = [...refKeys].filter((k) => !keys.has(k)).concat([...keys].filter((k) => !refKeys.has(k)))
            if (diff.length) warnings.push({ file: f.file, message: `Key-Satz weicht von ${ref.file} ab: ${diff.join(', ')}` })
            const removeA = [...ref.remove].sort().join('\n')
            const removeB = [...f.remove].sort().join('\n')
            if (removeA !== removeB) warnings.push({ file: f.file, message: `remove-Liste weicht von ${ref.file} ab` })
        }
    }

    // Konflikte je Sprache
    const plans = []
    for (const lang of languages) {
        const frs = fragments.filter((f) => f.lang === lang)
        if (!frs.length) continue
        const setBy = new Map() // key -> { value, file }
        const removeBy = new Map() // key -> file
        for (const f of frs) for (const k of f.remove) if (!removeBy.has(k)) removeBy.set(k, f.file)
        for (const f of frs) {
            for (const [k, v] of Object.entries(f.set)) {
                const prev = setBy.get(k)
                if (prev && prev.value !== v) {
                    errors.push({ file: f.file, key: k, message: `Konflikt: "${k}" hat in ${prev.file} einen anderen Wert` })
                } else if (!prev) setBy.set(k, { value: v, file: f.file })
                if (removeBy.has(k)) {
                    errors.push({ file: f.file, key: k, message: `Konflikt: "${k}" wird gesetzt und in ${removeBy.get(k)} entfernt` })
                }
            }
        }
        // Strukturkonflikte zwischen gesetzten Keys (a.b und a.b.c)
        const setKeys = [...setBy.keys()].sort()
        for (let i = 0; i < setKeys.length; i++) {
            for (let j = i + 1; j < setKeys.length && setKeys[j].startsWith(setKeys[i]); j++) {
                if (setKeys[j].startsWith(`${setKeys[i]}.`)) {
                    errors.push({
                        file: setBy.get(setKeys[j]).file,
                        key: setKeys[j],
                        message: `Strukturkonflikt mit "${setKeys[i]}" (${setBy.get(setKeys[i]).file})`,
                    })
                }
            }
        }
        const localePath = path.join(localesDir, `${lang}.json`)
        let locale
        try {
            locale = JSON.parse(fs.readFileSync(localePath, 'utf-8'))
        } catch (e) {
            errors.push({ file: `${lang}.json`, message: `Locale-Datei nicht lesbar: ${e.message}` })
            continue
        }
        if (!isPlainObject(locale)) {
            errors.push({ file: `${lang}.json`, message: 'Locale-Datei ist kein Objekt' })
            continue
        }
        const ops = {
            remove: [...new Set(frs.flatMap((f) => f.remove))],
            set: Object.fromEntries([...setBy.entries()].map(([k, e]) => [k, e.value])),
        }
        const { result, missingRemovals, conflicts } = applyFragmentOps(locale, ops)
        for (const k of missingRemovals) warnings.push({ file: removeBy.get(k), key: k, message: `remove: "${k}" existiert nicht in ${lang}.json` })
        for (const c of conflicts) errors.push({ file: setBy.get(c.key)?.file || `${lang}.json`, key: c.key, message: c.message })
        const before = `${JSON.stringify(locale, null, 2)}\n`
        const after = `${JSON.stringify(result, null, 2)}\n`
        plans.push({
            lang,
            localePath,
            content: after,
            changed: before !== after,
            setCount: Object.keys(ops.set).length,
            removeCount: ops.remove.length - missingRemovals.length,
        })
    }

    return { errors, warnings, plans, fragments, languages }
}

// Fuehrt den Merge aus. Bei Fehlern wird nichts geschrieben und nichts geloescht.
export function mergeLocaleFragments({
    localesDir = DEFAULT_PATHS.localesDir,
    fragmentsDir = DEFAULT_PATHS.fragmentsDir,
    keep = false,
    check = false,
} = {}) {
    const plan = planMerge({ localesDir, fragmentsDir })
    const written = []
    const deleted = []
    if (!plan.errors.length && !check) {
        for (const p of plan.plans) {
            if (!p.changed) continue
            const tmp = `${p.localePath}.tmp-${process.pid}`
            fs.writeFileSync(tmp, p.content, 'utf-8')
            fs.renameSync(tmp, p.localePath)
            written.push(path.basename(p.localePath))
        }
        if (!keep) {
            for (const f of plan.fragments) {
                fs.unlinkSync(path.join(fragmentsDir, f.file))
                deleted.push(f.file)
            }
        }
    }
    return { ...plan, written, deleted, check, keep }
}

// ---------------------------------------------------------------------------------------------
// CLI

export function main(argv = process.argv.slice(2)) {
    const known = ['--keep', '--check', '--json']
    const unknown = argv.filter((a) => !known.includes(a))
    if (unknown.length) {
        console.error(`Unbekannte Argumente: ${unknown.join(' ')}`)
        console.error('Aufruf: node scripts/merge-locale-fragments.mjs [--keep] [--check] [--json]')
        return 2
    }
    const keep = argv.includes('--keep')
    const check = argv.includes('--check')
    const res = mergeLocaleFragments({ keep, check })

    if (argv.includes('--json')) {
        console.log(JSON.stringify({
            errors: res.errors,
            warnings: res.warnings,
            fragments: res.fragments.map((f) => f.file),
            written: res.written,
            deleted: res.deleted,
        }, null, 2))
        return res.errors.length ? 1 : 0
    }

    if (!res.fragments.length && !res.errors.length) {
        console.log('Keine Locale-Fragmente vorhanden – nichts zu tun.')
        return 0
    }
    console.log(`Fragmente: ${res.fragments.length} (${[...new Set(res.fragments.map((f) => f.ws))].join(', ') || '–'})`)
    for (const w of res.warnings) console.log(`  Warnung  ${w.file}: ${w.message}`)
    for (const e of res.errors) console.log(`  FEHLER   ${e.file}: ${e.message}`)
    if (res.errors.length) {
        console.log(`\n${res.errors.length} Fehler – nichts geschrieben, keine Fragmente geloescht.`)
        return 1
    }
    for (const p of res.plans) {
        console.log(`  ${p.changed ? '↻' : '✓'} ${p.lang}.json  ${p.setCount} gesetzt, ${p.removeCount} entfernt${p.changed ? '' : ' (unveraendert)'}`)
    }
    if (check) console.log('\n--check: nichts geschrieben.')
    else {
        console.log(`\n${res.written.length} Locale-Datei(en) geschrieben.`)
        console.log(keep ? '--keep: Fragmente behalten.' : `${res.deleted.length} Fragment(e) geloescht.`)
        console.log('Weiter: npm run check:locales -- --strict')
    }
    return 0
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
    process.exitCode = main()
}
