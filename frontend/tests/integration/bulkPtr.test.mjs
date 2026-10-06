// WS-F1 x WS-F9F11-FE (Welle 2, parallel, Plan Regel 3): Der Bulk-Editor und die Record-Tabelle nutzen die
// PTR-Bausteine aus F9F11-FE mit den Namen/Signaturen aus Plan B.14/Block WS-F9F11-FE:
//   lib/ptrPreference.js  getManagePtr(zone) -> bool|null, setManagePtr(zone, v), getDefault() -> bool
//   lib/ptrResults.js     summarizePtr(ptrList) -> { ok, warnings, errors, lines }
//   components/PtrSyncOption.jsx  Props { checked, onChange, values, server, lookup }
// Fehlen die Dateien (vor dem Merge von WS-F9F11-FE), werden die Tests sichtbar uebersprungen.
// Laeuft ohne Browser/React: node --test tests/integration/bulkPtr.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'src')
const PREF = path.join(SRC, 'lib', 'ptrPreference.js')
const RESULTS = path.join(SRC, 'lib', 'ptrResults.js')
const OPTION = path.join(SRC, 'components', 'PtrSyncOption.jsx')
const missing = (f) => (fs.existsSync(f) ? false : `WS-F9F11-FE noch nicht gemergt (${path.relative(SRC, f)} fehlt)`)

test('ptrPreference: getManagePtr/setManagePtr/getDefault', { skip: missing(PREF) }, async () => {
    const mod = await import(pathToFileURL(PREF).href)
    for (const name of ['getManagePtr', 'setManagePtr', 'getDefault']) assert.equal(typeof mod[name], 'function', name)
    // Ohne gemerkte Auswahl -> null (Bulk-Editor/Papierkorb senden dann kein manage_ptr -> Admin-Default)
    assert.equal(mod.getManagePtr('example.com.'), null)
    assert.equal(typeof mod.getDefault(), 'boolean')
})

test('ptrResults: summarizePtr liefert ok/warnings/errors/lines', { skip: missing(RESULTS) }, async () => {
    const { summarizePtr } = await import(pathToFileURL(RESULTS).href)
    const s = summarizePtr([
        { ip: '192.0.2.5', ptr: '5.2.0.192.in-addr.arpa.', zone: '2.0.192.in-addr.arpa.', target: 'a.example.com.', op: 'set', action: 'set', reason: null },
        { ip: '192.0.2.6', ptr: '6.2.0.192.in-addr.arpa.', zone: '2.0.192.in-addr.arpa.', target: 'a.example.com.', op: 'set', action: 'skipped', reason: 'conflict', existing: ['b.example.com.'] },
        { ip: '192.0.2.7', ptr: '7.2.0.192.in-addr.arpa.', zone: null, target: 'a.example.com.', op: 'set', action: 'skipped', reason: 'no_reverse_zone' },
    ])
    for (const k of ['ok', 'warnings', 'errors', 'lines']) assert.ok(Array.isArray(s[k]), k)
    assert.equal(s.ok.length, 1)
    assert.equal(s.warnings.length + s.errors.length, 1)
    // ohne PTR-Pflege (details.ptr fehlt) -> leer
    const empty = summarizePtr(undefined)
    assert.equal(empty.ok.length + empty.warnings.length + empty.errors.length, 0)
})

test('PtrSyncOption: Props checked/onChange/values/server/lookup', { skip: missing(OPTION) }, () => {
    const text = fs.readFileSync(OPTION, 'utf-8')
    assert.match(text, /export default function PtrSyncOption/)
    for (const prop of ['checked', 'onChange', 'values', 'server', 'lookup']) assert.match(text, new RegExp(`\\b${prop}\\b`), prop)
})
