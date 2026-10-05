// API-Module (Plan Regel 10, B.14 [F21]):
// src/api.js liefert die Kernmethoden (Klasse APIClient), src/api/<ws>.js ergaenzt per
// `export default { methodName() {...} }` (wird in den Prototyp gemischt) und deklariert absichtliche
// Ueberschreibungen von Kernmethoden mit `export const overrides = [...]`.
// Fehler: Methodenname in zwei Modulen; Kernmethode ohne Deklaration ueberschrieben; Ueberschreibung, die der
// Plan diesem Workstream nicht erlaubt; deklarierte Ueberschreibung, die das Modul gar nicht definiert.
//
// Die Module werden NICHT importiert (sie duerfen i18n/JSON/Vite-Features nutzen), sondern statisch gelesen.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')
const FIX = path.join(HERE, 'fixtures')

// Erlaubte Ueberschreibungen je Modul (Dateiname ohne .js, klein) laut Plan B.14.
export const ALLOWED_OVERRIDES = Object.freeze({
    'f10-app-fe': ['completeLogin2fa', 'totpDisable'],
    'f7-fe': ['getAuditLog'],
    'f4-b': ['enableDNSSEC', 'disableDNSSEC'],
    'f14-app': ['listPanelTokens', 'createPanelToken', 'deletePanelToken'],
    'f6-fe': ['listWebhooks', 'createWebhook', 'deleteWebhook'],
})

// ---------------------------------------------------------------------------------------------
// Minimaler JS-Scanner: Kommentare -> Leerzeichen; in Strings bleiben Zeichen erhalten, aber Klammern und
// Kommas werden neutralisiert; Template-Literale (inkl. ${...}) werden komplett geleert. Danach lassen sich
// Klammertiefen zaehlen, ohne dass Inhalte von Strings/Kommentaren stoeren.

const NEUTRAL = new Set(['{', '}', '(', ')', '[', ']', ','])
const REGEX_PREV = new Set(['(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}', ';', '+', '-', '*', '%', '<', '>', '~', '^', ''])

export function cleanSource(src) {
    const out = src.split('')
    let i = 0
    const n = src.length
    const blank = (a, b) => { for (let k = a; k < b; k++) if (out[k] !== '\n') out[k] = ' ' }
    const lastSignificant = (pos) => {
        for (let k = pos - 1; k >= 0; k--) if (!/\s/.test(out[k])) return out[k]
        return ''
    }
    // ueberspringt ein Template-Literal ab dem oeffnenden Backtick, liefert Index nach dem schliessenden
    const skipTemplate = (start) => {
        let k = start + 1
        while (k < n) {
            const c = src[k]
            if (c === '\\') { k += 2; continue }
            if (c === '`') return k + 1
            if (c === '$' && src[k + 1] === '{') {
                k = skipCode(k + 2, '}')
                continue
            }
            k++
        }
        return n
    }
    const skipString = (start) => {
        const q = src[start]
        let k = start + 1
        while (k < n && src[k] !== q) {
            if (src[k] === '\\') k++
            else if (src[k] === '\n') break
            k++
        }
        return Math.min(k + 1, n)
    }
    // Code innerhalb von ${...} bis zur passenden schliessenden Klammer
    function skipCode(start, closer) {
        let depth = 0
        let k = start
        while (k < n) {
            const c = src[k]
            if (c === '`') { k = skipTemplate(k); continue }
            if (c === '"' || c === "'") { k = skipString(k); continue }
            if (c === '{') depth++
            else if (c === '}') {
                if (depth === 0 && closer === '}') return k + 1
                depth--
            }
            k++
        }
        return n
    }

    while (i < n) {
        const c = src[i]
        const next = src[i + 1]
        if (c === '/' && next === '/') {
            const end = src.indexOf('\n', i)
            const stop = end < 0 ? n : end
            blank(i, stop)
            i = stop
        } else if (c === '/' && next === '*') {
            const end = src.indexOf('*/', i + 2)
            const stop = end < 0 ? n : end + 2
            blank(i, stop)
            i = stop
        } else if (c === '"' || c === "'") {
            const end = skipString(i)
            for (let k = i + 1; k < end - 1; k++) if (NEUTRAL.has(out[k])) out[k] = ' '
            i = end
        } else if (c === '`') {
            const end = skipTemplate(i)
            blank(i + 1, end - 1)
            i = end
        } else if (c === '/' && REGEX_PREV.has(lastSignificant(i))) {
            // Regex-Literal
            let k = i + 1
            let inClass = false
            while (k < n && src[k] !== '\n') {
                if (src[k] === '\\') { k += 2; continue }
                if (src[k] === '[') inClass = true
                else if (src[k] === ']') inClass = false
                else if (src[k] === '/' && !inClass) break
                k++
            }
            blank(i + 1, k)
            i = k + 1
        } else {
            i++
        }
    }
    return out.join('')
}

const OPEN = new Set(['{', '(', '['])
const CLOSE = new Set(['}', ')', ']'])

// Index der zum oeffnenden Zeichen bei `start` passenden schliessenden Klammer
function matchingClose(clean, start) {
    let depth = 0
    for (let k = start; k < clean.length; k++) {
        if (OPEN.has(clean[k])) depth++
        else if (CLOSE.has(clean[k])) {
            depth--
            if (depth === 0) return k
        }
    }
    return -1
}

const MEMBER_HEAD_RE = /^(?:async\s+)?(?:\*\s*)?(?:(?:get|set)\s+(?=[A-Za-z_$'"]))?([A-Za-z_$][\w$]*|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|\[)/

// Namen der Eigenschaften/Methoden eines Objekt-Literals (Klammer bei `open`)
function objectMembers(src, clean, open) {
    const close = matchingClose(clean, open)
    if (close < 0) throw new Error('Objekt-Literal nicht geschlossen')
    const names = []
    let depth = 0
    let expectMember = true
    for (let k = open; k <= close; k++) {
        const c = clean[k]
        if (OPEN.has(c)) {
            depth++
            if (depth === 1) expectMember = true
            continue
        }
        if (CLOSE.has(c)) { depth--; continue }
        if (depth !== 1) continue
        if (c === ',') { expectMember = true; continue }
        if (!expectMember || /\s/.test(c)) continue
        expectMember = false
        if (clean.startsWith('...', k)) throw new Error('Spread im Default-Export wird nicht unterstuetzt')
        const m = MEMBER_HEAD_RE.exec(src.slice(k, close))
        if (!m || m[1] === '[') throw new Error(`Eigenschaftsname nicht lesbar bei Offset ${k}`)
        const raw = m[1]
        names.push(raw.startsWith("'") || raw.startsWith('"') ? raw.slice(1, -1) : raw)
    }
    return names
}

export function moduleInfo(file) {
    const src = fs.readFileSync(file, 'utf-8')
    const clean = cleanSource(src)
    let open = -1
    const direct = /export\s+default\s+\{/.exec(clean)
    if (direct) open = direct.index + direct[0].length - 1
    else {
        const ref = /export\s+default\s+([A-Za-z_$][\w$]*)\s*;?/.exec(clean)
        if (ref) {
            const decl = new RegExp(`(?:const|let|var)\\s+${ref[1].replace(/\$/g, '\\$')}\\s*=\\s*\\{`).exec(clean)
            if (decl) open = decl.index + decl[0].length - 1
        }
    }
    if (open < 0) throw new Error(`${path.basename(file)}: kein "export default { ... }" gefunden`)
    const methods = objectMembers(src, clean, open)
    let overrides = []
    const ov = /export\s+const\s+overrides\s*=\s*\[([^\]]*)\]/.exec(clean)
    if (ov) {
        // Inhalt aus dem Original lesen (Strings sind im bereinigten Text erhalten, nur Kommas neutralisiert)
        const raw = src.slice(ov.index, ov.index + ov[0].length)
        overrides = [...raw.matchAll(/['"]([A-Za-z_$][\w$]*)['"]/g)].map((x) => x[1])
    }
    return { methods, overrides }
}

// Kernmethoden: Member der Klasse APIClient in api.js
export function coreMethods(apiFile) {
    const src = fs.readFileSync(apiFile, 'utf-8')
    const clean = cleanSource(src)
    const cls = /class\s+APIClient\b[^{]*\{/.exec(clean)
    if (!cls) throw new Error('class APIClient nicht gefunden')
    const open = cls.index + cls[0].length - 1
    const close = matchingClose(clean, open)
    const names = new Set()
    let depth = 0
    for (let k = open; k <= close; k++) {
        const c = clean[k]
        if (OPEN.has(c)) { depth++; continue }
        if (CLOSE.has(c)) { depth--; continue }
        if (depth !== 1 || !/[A-Za-z_$]/.test(c) || /[\w$]/.test(clean[k - 1] || '')) continue
        let prev = ''
        for (let j = k - 1; j >= open; j--) if (!/\s/.test(clean[j])) { prev = clean[j]; break }
        if (!['{', '}', ';'].includes(prev)) continue
        const m = /^(?:static\s+)?(?:async\s+)?(?:\*\s*)?(?:(?:get|set)\s+(?=[A-Za-z_$]))?([A-Za-z_$][\w$]*)\s*[(=;]/.exec(clean.slice(k, close))
        if (m && m[1] !== 'constructor') names.add(m[1])
    }
    return names
}

// Gesamtanalyse eines src-Ordners (api.js + api/*.js)
export function analyzeApiModules(srcDir, allowed = ALLOWED_OVERRIDES) {
    const core = coreMethods(path.join(srcDir, 'api.js'))
    const modDir = path.join(srcDir, 'api')
    const files = fs.existsSync(modDir) ? fs.readdirSync(modDir).filter((f) => f.endsWith('.js')).sort() : []
    const errors = []
    const owners = new Map() // methode -> [modul]
    const modules = {}
    for (const f of files) {
        const id = f.replace(/\.js$/, '').toLowerCase()
        let info
        try {
            info = moduleInfo(path.join(modDir, f))
        } catch (e) {
            errors.push({ kind: 'parse', module: f, message: e.message })
            continue
        }
        modules[f] = info
        const allowedHere = new Set(allowed[id] || [])
        for (const name of info.methods) {
            if (!owners.has(name)) owners.set(name, [])
            owners.get(name).push(f)
            if (core.has(name) && !info.overrides.includes(name)) {
                errors.push({ kind: 'undeclared-override', module: f, name, message: `${f} ueberschreibt Kernmethode "${name}" ohne Eintrag in overrides` })
            }
        }
        for (const name of info.overrides) {
            if (!allowedHere.has(name)) {
                errors.push({ kind: 'override-not-allowed', module: f, name, message: `${f}: Ueberschreibung von "${name}" ist laut Plan B.14 nicht erlaubt` })
            }
            if (!info.methods.includes(name)) {
                errors.push({ kind: 'override-unused', module: f, name, message: `${f}: overrides nennt "${name}", das Modul definiert es aber nicht` })
            }
        }
    }
    for (const [name, mods] of owners) {
        if (mods.length > 1) errors.push({ kind: 'duplicate', name, modules: mods, message: `"${name}" ist in mehreren Modulen definiert: ${mods.join(', ')}` })
    }
    return { core, modules, errors }
}

// ---------------------------------------------------------------------------------------------
// Tests

test('echtes src: api.js-Kernmethoden werden erkannt, API-Module sind konfliktfrei', () => {
    const res = analyzeApiModules(SRC)
    assert.ok(res.core.size >= 50, `zu wenige Kernmethoden erkannt: ${res.core.size}`)
    for (const name of ['request', 'getUser', 'listZones', 'createRecord', 'getAuditLog', 'enableDNSSEC', 'completeLogin2fa']) {
        assert.ok(res.core.has(name), `Kernmethode ${name} nicht erkannt`)
    }
    assert.ok(!res.core.has('constructor'))
    assert.deepEqual(res.errors, [], res.errors.map((e) => e.message).join('\n'))
})

test('Fixture api-modules-duplicate: doppelter Name und undeklarierte Ueberschreibung', () => {
    const res = analyzeApiModules(path.join(FIX, 'api-modules-duplicate'))
    assert.deepEqual([...res.core].sort(), ['getA', 'getAuditLog', 'getB', 'request'])
    assert.deepEqual(res.modules['one.js'].methods, ['newX', 'getA'])
    assert.deepEqual(res.modules['two.js'].methods, ['newX', 'quoted-name'])
    const kinds = res.errors.map((e) => `${e.kind}:${e.name}`).sort()
    assert.deepEqual(kinds, ['duplicate:newX', 'undeclared-override:getA'])
})

test('Fixture api-modules-ok: deklarierte, erlaubte Ueberschreibung; Kommentare/Strings zaehlen nicht', () => {
    const res = analyzeApiModules(path.join(FIX, 'api-modules-ok'))
    assert.deepEqual(res.modules['f7-fe.js'].overrides, ['getAuditLog'])
    assert.deepEqual(res.modules['f7-fe.js'].methods, ['getAuditLog', 'getZoneHistory', 'nested'])
    assert.deepEqual(res.modules['f6-fe.js'].methods, ['getWebhookDeliveries'])
    assert.deepEqual(res.errors, [])
})

test('nicht erlaubte und ungenutzte Ueberschreibungen werden gemeldet', () => {
    // f7-fe darf getAuditLog ueberschreiben – mit leerer Erlaubnisliste ist es ein Fehler
    const res = analyzeApiModules(path.join(FIX, 'api-modules-ok'), { 'f7-fe': [] })
    assert.deepEqual(res.errors.map((e) => `${e.kind}:${e.name}`), ['override-not-allowed:getAuditLog'])

    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'api-modules-'))
    try {
        fs.copyFileSync(path.join(FIX, 'api-modules-ok', 'api.js'), path.join(tmp, 'api.js'))
        fs.mkdirSync(path.join(tmp, 'api'))
        fs.writeFileSync(path.join(tmp, 'api', 'f4-b.js'),
            "export const overrides = ['enableDNSSEC', 'disableDNSSEC']\nexport default {\n    enableDNSSEC() { return null },\n}\n")
        const r2 = analyzeApiModules(tmp)
        assert.deepEqual(r2.errors.map((e) => `${e.kind}:${e.name}`), ['override-unused:disableDNSSEC'])
    } finally {
        fs.rmSync(tmp, { recursive: true, force: true })
    }
})

test('cleanSource neutralisiert Klammern in Strings, Templates, Kommentaren und Regex', () => {
    const src = "const a = '{(' + `x${ {b: 1}.b }y` // }}}\n/* { */ const r = /[{]+/g\n"
    const clean = cleanSource(src)
    const count = (s, ch) => s.split(ch).length - 1
    assert.equal(count(clean, '{'), count(clean, '}'))
    assert.equal(clean.length, src.length)
})
