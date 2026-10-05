#!/usr/bin/env node
// Locale-Pruefung (F8 §6.6, Plan B.15).
//
// Aufruf (cwd egal, Pfade relativ zu dieser Datei):
//   node scripts/check-locales.mjs [--strict] [--json] [--print-identical] [--with-fragments]
//   npm run check:locales -- --strict
//
// --with-fragments: prueft den Stand NACH einem Merge aller Fragmente aus src/locales/fragments/
// (in einer temporaeren Kopie, die echten Dateien bleiben unveraendert) - so kann jeder Workstream
// sein Fragment vor dem Wellenende gegenpruefen. Merge-Fehler erscheinen als Regel "M".
//
// Regeln (Referenz en.json, Plural-Suffixe _zero|_one|_two|_few|_many|_other):
//   E1 jede *.json in src/locales ist gueltiges JSON und ein Objekt
//   E2 Blatt-Werte sind Strings
//   E3 kein leerer / nur Whitespace-Wert
//   E4 Key-Paritaet nach Plural-Normalisierung (fehlend / ueberzaehlig getrennt)
//   E5 Plural-Vollstaendigkeit laut Intl.PluralRules(lang) (fehlend = Fehler, ueberzaehlig = Warnung);
//      ein Key darf nicht zugleich als `base` und `base_*` existieren; Plural nur, wenn en ihn auch als Plural fuehrt
//   E6 Interpolation {{var}} je Wert == en (Plural gegen en base_other; `count` darf in _one/_zero fehlen)
//   E7 statische Key-Referenzen in src/**/*.{js,jsx} existieren in en (Praefixe aus allowlist.dynamic ausgenommen)
//   W1 Wert identisch mit en (ausser Allowlist oder Wert ohne Buchstaben)       -> Warnung (--strict: Fehler)
//   W2 Allowlist-Eintrag trifft nicht (mehr) zu                                 -> Warnung (--strict: Fehler)
//   W3 Allowlist-Datei in locale-allowlist.d/ ohne "reason"                     -> Warnung (--strict: Fehler)
//
// Allowlist: scripts/locale-allowlist.json plus scripts/locale-allowlist.d/*.json, jeweils
//   { "global": [keys], "perLanguage": { "<lang>": [keys] }, "dynamic": [praefixe], "reason": "..." }
// Keys exakt oder mit `*` am Ende (Praefix). "global"/"perLanguage" gelten fuer W1, "dynamic" fuer E7
// (Keys, die im Code per Verkettung gebildet werden, z. B. t('lua.err.' + code)).

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { planMerge } from './merge-locale-fragments.mjs'

export const PLURAL_SUFFIXES = ['zero', 'one', 'two', 'few', 'many', 'other']
const PLURAL_RE = new RegExp(`^(.+)_(${PLURAL_SUFFIXES.join('|')})$`)
const REFERENCE_LANG = 'en'

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
export const DEFAULT_PATHS = Object.freeze({
    localesDir: path.resolve(SCRIPT_DIR, '..', 'src', 'locales'),
    srcDir: path.resolve(SCRIPT_DIR, '..', 'src'),
    allowlistFile: path.join(SCRIPT_DIR, 'locale-allowlist.json'),
    allowlistDir: path.join(SCRIPT_DIR, 'locale-allowlist.d'),
    fragmentsDir: path.resolve(SCRIPT_DIR, '..', 'src', 'locales', 'fragments'),
})

// ---------------------------------------------------------------------------------------------
// Hilfen

export function splitPlural(key) {
    const m = PLURAL_RE.exec(key)
    return m ? { base: m[1], category: m[2] } : null
}

// Verschachteltes Objekt -> Map(flacher Key -> Wert). Nicht-String-Blaetter landen in `bad`.
export function flattenLocale(obj, prefix = '', out = new Map(), bad = []) {
    for (const [k, v] of Object.entries(obj)) {
        const full = prefix ? `${prefix}.${k}` : k
        if (v !== null && typeof v === 'object' && !Array.isArray(v)) flattenLocale(v, full, out, bad)
        else if (typeof v === 'string') out.set(full, v)
        else bad.push({ key: full, type: v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v })
    }
    return { map: out, bad }
}

const VAR_RE = /\{\{\s*-?\s*([^{}\s,]+)\s*(?:,[^{}]*)?\}\}/g

export function interpolationVars(value) {
    const vars = new Set()
    for (const m of String(value).matchAll(VAR_RE)) vars.add(m[1])
    return vars
}

function hasLetters(value) {
    return /\p{L}/u.test(String(value).replace(/\{\{[^}]*\}\}/g, ''))
}

function pluralCategories(lang) {
    try {
        return new Intl.PluralRules(lang).resolvedOptions().pluralCategories
    } catch {
        return ['one', 'other']
    }
}

// Allowlist-Muster (exakt oder `praefix*`) gegen einen Key; Plural-Keys treffen auch ueber ihre Basis.
export function matchesPattern(pattern, key) {
    const keys = [key]
    const p = splitPlural(key)
    if (p) keys.push(p.base)
    if (pattern.endsWith('*')) {
        const prefix = pattern.slice(0, -1)
        return keys.some((k) => k.startsWith(prefix))
    }
    return keys.includes(pattern)
}

// ---------------------------------------------------------------------------------------------
// Allowlist laden (Basisdatei + .d-Verzeichnis)

function emptyAllowlist() {
    return { global: [], perLanguage: {}, dynamic: [], sources: [], problems: [] }
}

function addAllowlist(target, data, source, { requireReason }) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
        throw new Error(`${source}: Allowlist muss ein JSON-Objekt sein`)
    }
    const allowed = new Set(['global', 'perLanguage', 'dynamic', 'reason', '$comment'])
    for (const k of Object.keys(data)) {
        if (!allowed.has(k)) throw new Error(`${source}: unbekanntes Feld "${k}"`)
    }
    const strList = (v, what) => {
        if (v === undefined) return []
        if (!Array.isArray(v) || v.some((x) => typeof x !== 'string' || !x.trim())) {
            throw new Error(`${source}: "${what}" muss eine Liste nicht-leerer Strings sein`)
        }
        return v
    }
    for (const k of strList(data.global, 'global')) target.global.push({ key: k, source })
    for (const k of strList(data.dynamic, 'dynamic')) target.dynamic.push({ key: k, source })
    if (data.perLanguage !== undefined) {
        if (!data.perLanguage || typeof data.perLanguage !== 'object' || Array.isArray(data.perLanguage)) {
            throw new Error(`${source}: "perLanguage" muss ein Objekt sein`)
        }
        for (const [lang, list] of Object.entries(data.perLanguage)) {
            target.perLanguage[lang] ??= []
            for (const k of strList(list, `perLanguage.${lang}`)) target.perLanguage[lang].push({ key: k, source })
        }
    }
    if (data.reason !== undefined && typeof data.reason !== 'string') {
        throw new Error(`${source}: "reason" muss ein String sein`)
    }
    if (requireReason && !(typeof data.reason === 'string' && data.reason.trim())) {
        target.problems.push({ rule: 'W3', file: source, message: 'Allowlist-Datei ohne Begruendung ("reason")' })
    }
    target.sources.push(source)
}

// Liest die Basisdatei (optional) und alle *.json aus dem .d-Verzeichnis (optional), sortiert.
export function loadAllowlist({ file = DEFAULT_PATHS.allowlistFile, dir = DEFAULT_PATHS.allowlistDir } = {}) {
    const result = emptyAllowlist()
    if (file && fs.existsSync(file)) {
        addAllowlist(result, JSON.parse(fs.readFileSync(file, 'utf-8')), path.basename(file), { requireReason: false })
    }
    if (dir && fs.existsSync(dir)) {
        for (const name of fs.readdirSync(dir).filter((f) => f.endsWith('.json')).sort()) {
            const p = path.join(dir, name)
            let data
            try {
                data = JSON.parse(fs.readFileSync(p, 'utf-8'))
            } catch (err) {
                throw new Error(`${path.basename(dir)}/${name}: kein gueltiges JSON (${err.message})`, { cause: err })
            }
            addAllowlist(result, data, `${path.basename(dir)}/${name}`, { requireReason: true })
        }
    }
    return result
}

// Akzeptiert sowohl das Ergebnis von loadAllowlist als auch ein schlichtes { global: [string], ... }.
function normalizeAllowlist(al) {
    const out = emptyAllowlist()
    if (!al) return out
    const norm = (list, source) => (list || []).map((e) => (typeof e === 'string' ? { key: e, source } : e))
    out.global = norm(al.global, 'allowlist')
    out.dynamic = norm(al.dynamic, 'allowlist')
    for (const [lang, list] of Object.entries(al.perLanguage || {})) out.perLanguage[lang] = norm(list, 'allowlist')
    out.problems = al.problems || []
    out.sources = al.sources || []
    return out
}

// ---------------------------------------------------------------------------------------------
// Quelltext-Scan (E7)

const STATIC_CALL_RE = /\b(?:t|_t|i18n\.t)\(\s*['"]([A-Za-z0-9_.]+)['"]/g
const STATIC_PROP_RE = /(?:labelKey|placeholderKey|titleKey)\s*:\s*['"]([A-Za-z0-9_.]+)['"]/g

function listSourceFiles(dir) {
    const out = []
    if (!dir || !fs.existsSync(dir)) return out
    const walk = (d) => {
        for (const ent of fs.readdirSync(d, { withFileTypes: true })) {
            if (ent.name === 'node_modules' || ent.name.startsWith('.')) continue
            const p = path.join(d, ent.name)
            if (ent.isDirectory()) walk(p)
            else if (/\.(js|jsx)$/.test(ent.name)) out.push(p)
        }
    }
    walk(dir)
    return out.sort()
}

export function findStaticKeyRefs(srcDir) {
    const refs = []
    for (const file of listSourceFiles(srcDir)) {
        const text = fs.readFileSync(file, 'utf-8')
        for (const re of [STATIC_CALL_RE, STATIC_PROP_RE]) {
            for (const m of text.matchAll(re)) {
                const line = text.slice(0, m.index).split('\n').length
                refs.push({ key: m[1], file: path.relative(srcDir, file), line })
            }
        }
    }
    return refs
}

// ---------------------------------------------------------------------------------------------
// Kernpruefung

export function checkLocales({ localesDir = DEFAULT_PATHS.localesDir, srcDir = null, allowlist = null } = {}) {
    const errors = []
    const warnings = []
    const identical = {} // lang -> [{ key, value, allowed }]
    const al = normalizeAllowlist(allowlist)
    const err = (rule, file, key, message) => errors.push({ rule, file, key, message })
    const warn = (rule, file, key, message) => warnings.push({ rule, file, key, message })
    for (const p of al.problems) warn(p.rule, p.file, null, p.message)

    // --- E1/E2/E3: Dateien laden
    const files = fs.readdirSync(localesDir).filter((f) => f.endsWith('.json')).sort()
    const locales = new Map() // lang -> Map(key -> value)
    for (const file of files) {
        const lang = file.replace(/\.json$/, '')
        let data
        try {
            data = JSON.parse(fs.readFileSync(path.join(localesDir, file), 'utf-8'))
        } catch (e) {
            err('E1', file, null, `kein gueltiges JSON: ${e.message}`)
            continue
        }
        if (!data || typeof data !== 'object' || Array.isArray(data)) {
            err('E1', file, null, 'oberste Ebene ist kein Objekt')
            continue
        }
        const { map, bad } = flattenLocale(data)
        for (const b of bad) err('E2', file, b.key, `Wert ist kein String (${b.type})`)
        for (const [key, value] of map) {
            if (!value.trim()) err('E3', file, key, 'leerer Wert')
        }
        locales.set(lang, map)
    }

    const en = locales.get(REFERENCE_LANG)
    if (!en) {
        if (!files.includes(`${REFERENCE_LANG}.json`)) err('E1', `${REFERENCE_LANG}.json`, null, 'Referenzdatei fehlt')
        return { errors, warnings, identical, languages: [...locales.keys()] }
    }

    // Basis-Keys und Plural-Struktur je Sprache
    const structure = (map) => {
        const bases = new Set()
        const plurals = new Map() // base -> Set(category)
        const plain = new Set()
        for (const key of map.keys()) {
            const p = splitPlural(key)
            if (p) {
                bases.add(p.base)
                if (!plurals.has(p.base)) plurals.set(p.base, new Set())
                plurals.get(p.base).add(p.category)
            } else {
                bases.add(key)
                plain.add(key)
            }
        }
        return { bases, plurals, plain }
    }
    const enStruct = structure(en)
    // Plural-Basen laut en: base_one oder base_other vorhanden
    const enPluralBases = new Set(
        [...enStruct.plurals.entries()].filter(([, cats]) => cats.has('one') || cats.has('other')).map(([b]) => b),
    )

    // Referenzwert fuer einen Key: exakter en-Key, bei Plural sonst en base_other
    const referenceValue = (key) => {
        if (en.has(key)) return en.get(key)
        const p = splitPlural(key)
        if (p && en.has(`${p.base}_other`)) return en.get(`${p.base}_other`)
        return undefined
    }
    const pluralReference = (key) => {
        const p = splitPlural(key)
        if (p && enPluralBases.has(p.base) && en.has(`${p.base}_other`)) return en.get(`${p.base}_other`)
        return en.get(key)
    }

    for (const [lang, map] of locales) {
        const file = `${lang}.json`
        const st = lang === REFERENCE_LANG ? enStruct : structure(map)

        // --- E4: Paritaet der Basis-Keys
        if (lang !== REFERENCE_LANG) {
            for (const b of enStruct.bases) if (!st.bases.has(b)) err('E4', file, b, 'Key fehlt (in en vorhanden)')
            for (const b of st.bases) if (!enStruct.bases.has(b)) err('E4', file, b, 'Key ueberzaehlig (nicht in en)')
        }

        // --- E5: Pluralformen
        const required = pluralCategories(lang)
        for (const [base, cats] of st.plurals) {
            if (st.plain.has(base)) err('E5', file, base, `Key existiert zugleich als "${base}" und als Pluralform`)
            if (!enPluralBases.has(base) && enStruct.bases.has(base)) {
                err('E5', file, base, 'Pluralformen vorhanden, en fuehrt den Key aber nicht als Plural')
                continue
            }
            if (!enPluralBases.has(base)) continue
            for (const c of cats) {
                if (!required.includes(c)) warn('E5', file, `${base}_${c}`, `Pluralform "${c}" wird fuer ${lang} nicht benoetigt`)
            }
        }
        for (const base of enPluralBases) {
            if (!st.bases.has(base)) continue // schon E4
            const cats = st.plurals.get(base) || new Set()
            const missing = required.filter((c) => !cats.has(c))
            if (missing.length) {
                err('E5', file, base, `Pluralformen fehlen: ${missing.map((c) => `${base}_${c}`).join(', ')}`)
            }
        }

        // --- E6: Interpolation
        for (const [key, value] of map) {
            const ref = pluralReference(key) ?? referenceValue(key)
            if (ref === undefined) continue
            const want = interpolationVars(ref)
            const have = interpolationVars(value)
            const p = splitPlural(key)
            const countOptional = p && (p.category === 'one' || p.category === 'zero')
            const missing = [...want].filter((v) => !have.has(v) && !(countOptional && v === 'count'))
            const extra = [...have].filter((v) => !want.has(v))
            if (missing.length || extra.length) {
                const parts = []
                if (missing.length) parts.push(`fehlt: ${missing.map((v) => `{{${v}}}`).join(', ')}`)
                if (extra.length) parts.push(`zusaetzlich: ${extra.map((v) => `{{${v}}}`).join(', ')}`)
                err('E6', file, key, `Platzhalter weichen von en ab (${parts.join('; ')})`)
            }
        }

        // --- W1: identische Werte
        if (lang !== REFERENCE_LANG) {
            const patterns = [...al.global, ...(al.perLanguage[lang] || [])]
            identical[lang] = []
            for (const [key, value] of map) {
                const ref = referenceValue(key)
                if (ref === undefined || ref !== value) continue
                const allowed = patterns.some((p) => matchesPattern(p.key, key))
                const letterless = !hasLetters(value)
                identical[lang].push({ key, value, allowed: allowed || letterless })
                if (!allowed && !letterless) warn('W1', file, key, `Wert identisch mit en: "${value}"`)
            }
        }
    }

    // --- W2: Allowlist-Eintraege, die nicht (mehr) zutreffen
    const otherLangs = [...locales.keys()].filter((l) => l !== REFERENCE_LANG)
    const isIdenticalIn = (lang, pattern) =>
        (identical[lang] || []).some((e) => matchesPattern(pattern, e.key))
    const existsInEn = (pattern) => [...en.keys()].some((k) => matchesPattern(pattern, k))
    for (const entry of al.global) {
        if (!existsInEn(entry.key)) warn('W2', entry.source, entry.key, 'Allowlist (global): Key existiert nicht in en')
        else if (!otherLangs.some((l) => isIdenticalIn(l, entry.key))) {
            warn('W2', entry.source, entry.key, 'Allowlist (global): in keiner Sprache identisch mit en')
        }
    }
    for (const [lang, list] of Object.entries(al.perLanguage)) {
        for (const entry of list) {
            if (!locales.has(lang)) warn('W2', entry.source, entry.key, `Allowlist (${lang}): keine Locale-Datei ${lang}.json`)
            else if (!existsInEn(entry.key)) warn('W2', entry.source, entry.key, `Allowlist (${lang}): Key existiert nicht in en`)
            else if (!isIdenticalIn(lang, entry.key)) warn('W2', entry.source, entry.key, `Allowlist (${lang}): Wert ist nicht identisch mit en`)
        }
    }

    // --- E7: statische Key-Referenzen im Code
    if (srcDir) {
        const known = (key) => en.has(key) || enStruct.bases.has(key)
        for (const ref of findStaticKeyRefs(srcDir)) {
            if (known(ref.key)) continue
            if (al.dynamic.some((d) => matchesPattern(d.key, ref.key))) continue
            const hint = /[._]$/.test(ref.key) ? ' (dynamischer Praefix? -> "dynamic" in der Allowlist eintragen)' : ''
            err('E7', `src/${ref.file}:${ref.line}`, ref.key, `Key existiert nicht in en${hint}`)
        }
    }

    return { errors, warnings, identical, languages: [...locales.keys()] }
}

// ---------------------------------------------------------------------------------------------
// CLI

function printGrouped(list, label) {
    const byFile = new Map()
    for (const f of list) {
        if (!byFile.has(f.file)) byFile.set(f.file, [])
        byFile.get(f.file).push(f)
    }
    for (const [file, items] of byFile) {
        console.log(`\n${file} (${items.length} ${label})`)
        for (const f of items) console.log(`  [${f.rule}] ${f.key ? `${f.key}: ` : ''}${f.message}`)
    }
}

export function main(argv = process.argv.slice(2)) {
    const strict = argv.includes('--strict')
    const asJson = argv.includes('--json')
    const printIdentical = argv.includes('--print-identical')
    const withFragments = argv.includes('--with-fragments')
    const unknown = argv.filter((a) => !['--strict', '--json', '--print-identical', '--with-fragments'].includes(a))
    if (unknown.length) {
        console.error(`Unbekannte Argumente: ${unknown.join(' ')}`)
        console.error('Aufruf: node scripts/check-locales.mjs [--strict] [--json] [--print-identical] [--with-fragments]')
        return 2
    }

    let allowlist
    try {
        allowlist = loadAllowlist()
    } catch (e) {
        console.error(`Allowlist fehlerhaft: ${e.message}`)
        return 1
    }
    let localesDir = DEFAULT_PATHS.localesDir
    let tmpDir = null
    const mergeErrors = []
    if (withFragments) {
        tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'check-locales-'))
        for (const f of fs.readdirSync(localesDir).filter((n) => n.endsWith('.json'))) {
            fs.copyFileSync(path.join(localesDir, f), path.join(tmpDir, f))
        }
        const plan = planMerge({ localesDir: tmpDir, fragmentsDir: DEFAULT_PATHS.fragmentsDir })
        for (const e of plan.errors) mergeErrors.push({ rule: 'M', file: `fragments/${e.file}`, key: e.key || null, message: e.message })
        for (const p of plan.plans) fs.writeFileSync(p.localePath, p.content, 'utf-8')
        localesDir = tmpDir
    }
    let result
    try {
        result = checkLocales({ localesDir, srcDir: DEFAULT_PATHS.srcDir, allowlist })
    } finally {
        if (tmpDir) fs.rmSync(tmpDir, { recursive: true, force: true })
    }
    result.errors.unshift(...mergeErrors)
    const errors = strict ? [...result.errors, ...result.warnings] : result.errors
    const warnings = strict ? [] : result.warnings

    if (asJson) {
        console.log(JSON.stringify({ strict, errors, warnings }, null, 2))
    } else {
        if (printIdentical) {
            for (const [lang, list] of Object.entries(result.identical)) {
                console.log(`\n${lang}.json – identisch mit en (${list.length}):`)
                for (const e of list) console.log(`  ${e.allowed ? ' ' : '!'} ${e.key} = ${JSON.stringify(e.value)}`)
            }
        }
        printGrouped(errors, 'Fehler')
        printGrouped(warnings, 'Warnungen')
        if (!errors.length && !warnings.length) console.log('✓ Locales ok')
        else console.log(`\n${errors.length} Fehler, ${warnings.length} Warnungen${strict ? ' (--strict: Warnungen zaehlen als Fehler)' : ''}`)
    }
    return errors.length ? 1 : 0
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
    process.exitCode = main()
}
