// Integrationstest WS-F9F11-FE <-> WS-F9F11-BE (Plan Regel 3: braucht Code des parallelen Workstreams,
// zaehlt nicht zum Fertig-Kriterium von WS-F9F11-FE, laeuft verpflichtend im Integrationsschritt F.2).
// Prueft den API-Vertrag statisch gegen die Backend-Quellen:
//   - jede Methode aus src/api/dyndns.js und src/api/ptr.js trifft eine Backend-Route (Methode + Pfad),
//   - die PTR-Begruendungen des Backends kennt lib/ptrResults.js,
//   - die DynDNS-/PTR-Audit-Aktionen des Backends stehen im Audit-Katalog und haben Labels in allen Sprachen,
//   - GET /dyndns/info liefert proxy_warning (Admin-Banner, Plan [S6]).
// Solange die Backend-Dateien fehlen (Branch WS-F9F11-BE noch nicht gemergt), werden die Tests mit Begruendung
// uebersprungen - sichtbar in der Ausgabe, kein stilles Bestehen.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { PTR_KNOWN_REASONS } from '../../src/lib/ptrResults.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FE = path.resolve(HERE, '..', '..')
const BE = path.resolve(FE, '..', 'backend')
const ROUTERS = path.join(BE, 'app', 'routers')
const BE_FILES = {
    dyndns: path.join(ROUTERS, 'dyndns.py'),
    ptr: path.join(ROUTERS, 'ptr.py'),
    settingsDyndns: path.join(ROUTERS, 'settings_dyndns.py'),
    ptrService: path.join(BE, 'app', 'services', 'ptr.py'),
    dyndnsService: path.join(BE, 'app', 'services', 'dyndns.py'),
}
const LANGS = ['de', 'en', 'bs', 'hr', 'hu', 'sr']
const SKIP = Object.values(BE_FILES).every((f) => fs.existsSync(f))
    ? false
    : 'Backend WS-F9F11-BE (routers/dyndns.py, ptr.py, settings_dyndns.py, services/ptr.py, dyndns.py) noch nicht im Stand - Integrationsschritt F.2'

const read = (f) => fs.readFileSync(f, 'utf-8')

// Routen eines Router-Moduls: [{ method, path }] mit Prefix aus APIRouter(prefix="...") (je Variable)
function routesOf(file) {
    const src = read(file)
    const prefixes = new Map()
    for (const m of src.matchAll(/^(\w+)\s*=\s*APIRouter\(([^)]*)\)/gm)) {
        const p = /prefix\s*=\s*["']([^"']*)["']/.exec(m[2])
        prefixes.set(m[1], p ? p[1] : '')
    }
    const out = []
    for (const m of src.matchAll(/@(\w+)\.(get|post|put|delete|patch)\(\s*["']([^"']*)["']/g)) {
        out.push({ method: m[2].toUpperCase(), path: norm((prefixes.get(m[1]) ?? '') + m[3]) })
    }
    return out
}

// '/dyndns/tokens/{token_id:int}' bzw. '/dyndns/tokens/${...}' -> '/dyndns/tokens/{}'
function norm(p) {
    return p.replace(/\{[^}]*\}/g, '{}').replace(/\$\{[^}]*\}/g, '{}').replace(/\?.*$/, '').replace(/\/+$/, '') || '/'
}

// Aufrufe this.request('METHOD', `pfad`) aus einem API-Modul
function apiCalls(file) {
    const src = read(file)
    const out = []
    for (const m of src.matchAll(/this\.request\(\s*'(\w+)'\s*,\s*[`'"]([^`'"]+)[`'"]/g)) {
        out.push({ method: m[1].toUpperCase(), path: norm(m[2].replace(/\$\{q\}$/, '')) })
    }
    return out
}

function knownKeys(lang) {
    const keys = new Map()
    const walk = (obj, prefix) => {
        for (const [k, v] of Object.entries(obj)) {
            const full = prefix ? `${prefix}.${k}` : k
            if (v && typeof v === 'object') walk(v, full)
            else keys.set(full, v)
        }
    }
    walk(JSON.parse(read(path.join(FE, 'src', 'locales', `${lang}.json`))), '')
    const fragDir = path.join(FE, 'src', 'locales', 'fragments')
    if (fs.existsSync(fragDir)) {
        for (const f of fs.readdirSync(fragDir).filter((n) => n.endsWith(`.${lang}.json`))) {
            for (const [k, v] of Object.entries(JSON.parse(read(path.join(fragDir, f))).set || {})) keys.set(k, v)
        }
    }
    return keys
}

test('API-Module treffen Backend-Routen (Methode + Pfad)', { skip: SKIP }, () => {
    const routes = [BE_FILES.dyndns, BE_FILES.ptr, BE_FILES.settingsDyndns].flatMap(routesOf)
    assert.ok(routes.length >= 10, `zu wenige Routen gefunden (${routes.length}) - Parser pruefen`)
    const calls = [
        ...apiCalls(path.join(FE, 'src', 'api', 'dyndns.js')),
        ...apiCalls(path.join(FE, 'src', 'api', 'ptr.js')),
    ]
    assert.ok(calls.length >= 14, `zu wenige API-Aufrufe gefunden (${calls.length})`)
    const missing = calls.filter((c) => !routes.some((r) => r.method === c.method && r.path === c.path))
    assert.deepEqual(missing, [], 'Frontend ruft Routen, die das Backend nicht hat')
})

test('PTR-Begruendungen des Backends kennt lib/ptrResults.js', { skip: SKIP }, () => {
    const src = read(BE_FILES.ptrService)
    const found = new Set()
    for (const m of src.matchAll(/reason\s*[=:]\s*["'](\w+)["']/g)) found.add(m[1])
    for (const m of src.matchAll(/["']reason["']\s*:\s*["'](\w+)["']/g)) found.add(m[1])
    const lit = /Literal\[([^\]]*"no_reverse_zone"[^\]]*)\]/.exec(src)
    if (lit) for (const m of lit[1].matchAll(/"(\w+)"/g)) found.add(m[1])
    assert.ok(found.size >= 3, `keine Begruendungen in services/ptr.py gefunden (${[...found]})`)
    const unknown = [...found].filter((r) => !PTR_KNOWN_REASONS.includes(r))
    assert.deepEqual(unknown, [], 'neue PTR-Begruendung: lib/ptrResults.js (+ ptr.reason.*) ergaenzen')
})

test('DynDNS-/PTR-Audit-Aktionen stehen im Katalog und haben Labels', { skip: SKIP }, async () => {
    const actions = new Set()
    for (const f of [BE_FILES.dyndns, BE_FILES.settingsDyndns, BE_FILES.ptrService, BE_FILES.dyndnsService, BE_FILES.ptr]) {
        for (const m of read(f).matchAll(/["']((?:DYNDNS|PTR)_[A-Z_]+)["']/g)) actions.add(m[1])
    }
    assert.ok(actions.size >= 3, `keine Aktionen gefunden (${[...actions]})`)
    const base = (await import('../../src/constants/auditActions/base.actions.js')).default
    const mine = (await import('../../src/constants/auditActions/ws-f9f11-be.actions.js')).default
    const catalog = new Set([...base, ...mine].map((a) => a.action))
    // Konstanten wie PTR_TYPES/DYNDNS_ENABLED_KEY sind keine Aktionen: nur Namen, die wie Aktionen aussehen
    const relevant = [...actions].filter((a) => /^(DYNDNS_(UPDATE|AUTH_FAILED|TOKEN_\w+|SETTINGS_UPDATE)|PTR_(SYNC|SETTINGS_UPDATE))$/.test(a))
    const missing = relevant.filter((a) => !catalog.has(a))
    assert.deepEqual(missing, [], 'Audit-Aktion fehlt in constants/auditActions')
    for (const lang of LANGS) {
        const keys = knownKeys(lang)
        const noLabel = relevant.filter((a) => !keys.has(`audit.actions.${a}`))
        assert.deepEqual(noLabel, [], `${lang}: Label fehlt`)
    }
})

test('GET /dyndns/info liefert proxy_warning (Admin-Banner [S6])', { skip: SKIP }, () => {
    const src = read(BE_FILES.dyndns) + read(BE_FILES.dyndnsService)
    assert.match(src, /proxy_warning/)
})
