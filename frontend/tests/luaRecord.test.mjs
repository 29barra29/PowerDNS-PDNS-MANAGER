// Tests fuer src/lib/luaRecord.js (F15 9.3): Parsen/Bauen, Live-Pruefung, Vorlagen, Geo-Erkennung, Server-Status-
// Helfer, Tabellenfilter - und der Sync-Test der Listen gegen das Backend (lua_records.py, schemas/dns.py).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
    GEO_FUNCTIONS, LUA_MAX_CONTENT_LENGTH, LUA_TARGET_TYPES, LUA_TEMPLATES, LUA_TEMPLATE_GROUPS, analyzeLuaCode,
    buildLuaContent, checkLuaBrackets, filterRecords, luaCodeOneLine, luaInactiveServers, luaServersWithoutGeoip,
    luaStatusLine, luaTemplateById, normalizeRecordFilter, parseLuaContent, recordFilterTypes, relevantLuaServers,
    usesGeoFunctions,
} from '../src/lib/luaRecord.js'
import { ALL_RECORD_TYPE_KEYS } from '../src/constants/dnsRecordTypes.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const BACKEND = path.resolve(HERE, '..', '..', 'backend', 'app')

// ------------------------------------------------------------------ parse/build

test('parseLuaContent/buildLuaContent: Roundtrip', () => {
    const content = "A \"ifportup(443, {'192.0.2.1', '192.0.2.2'})\""
    const p = parseLuaContent(content)
    assert.deepEqual(p, { rtype: 'A', code: "ifportup(443, {'192.0.2.1', '192.0.2.2'})", parsed: true })
    assert.equal(buildLuaContent(p.rtype, p.code), content)
    assert.deepEqual(parseLuaContent(' aaaa  "x()"  '), { rtype: 'AAAA', code: 'x()', parsed: true })
})

test('parseLuaContent: mehrere Abschnitte werden mit Leerzeichen verbunden, Unparsebares bleibt roh', () => {
    assert.deepEqual(parseLuaContent('A "pick" "random()"'), { rtype: 'A', code: 'pick random()', parsed: true })
    assert.deepEqual(parseLuaContent('A x()'), { rtype: 'A', code: 'x()', parsed: false })
    assert.deepEqual(parseLuaContent('kaputt'), { rtype: 'A', code: 'kaputt', parsed: false })
    assert.deepEqual(parseLuaContent('A "a" b'), { rtype: 'A', code: '"a" b', parsed: false })
    assert.equal(parseLuaContent('').parsed, false)
})

test('buildLuaContent: mehrzeiliger Code wird eine Zeile, Tabs werden Leerzeichen', () => {
    const code = ";if country('DE') then\n\treturn '192.0.2.1'\n\nend\r\nreturn '198.51.100.1'"
    assert.equal(buildLuaContent('a', code),
        "A \";if country('DE') then return '192.0.2.1' end return '198.51.100.1'\"")
    assert.equal(luaCodeOneLine('  a\t b  \n\n c '), 'a  b c')
    assert.equal(buildLuaContent(undefined, 'x()'), 'A "x()"')
})

// ------------------------------------------------------------------ Klammer-Tokenizer (wie Backend)

test('checkLuaBrackets: gleiche Ergebnisse wie check_lua_brackets im Backend', () => {
    const cases = [
        ['f({1, 2}, [3])', null],
        ["f('a\\'b')", null],
        ['f(]', 'unbalanced'],
        ["f('x", 'unterminated_string'],
        ['--[[ x', 'unterminated_comment'],
        ['x -- (', null],
        ['f( -- )', 'unbalanced'],
        ["[[ ( ]] .. pickrandom({'1.2.3.4'}) -- ( kommentar", null],
        ["--[==[ ( ]==] pickrandom({'1.2.3.4'})", null],
        ["[=[ ]] ( ]=] .. 'x'", null],
        ['[[ offen', 'unterminated_string'],
        ['f({1)', 'unbalanced'],
        ['f())', 'unbalanced'],
    ]
    for (const [code, expected] of cases) assert.equal(checkLuaBrackets(code), expected, code)
})

// ------------------------------------------------------------------ analyzeLuaCode

test('analyzeLuaCode: Fehlercodes (Faelle wie Backend 9.1-4)', () => {
    const err = (rtype, code) => analyzeLuaCode(rtype, code).errors
    assert.deepEqual(err('A', ''), ['empty'])
    assert.deepEqual(err('A', '   \n  '), ['empty'])
    assert.deepEqual(err('NS', 'x()'), ['targetType'])
    assert.deepEqual(err('LUA', 'x()'), ['targetType'])
    assert.deepEqual(err('A', 'x("y")'), ['doubleQuote'])
    assert.deepEqual(err('A', 'f({1)'), ['unbalanced'])
    assert.deepEqual(err('A', "f('x)"), ['unterminatedString'])
    assert.deepEqual(err('A', '--[[ offen'), ['unterminatedComment'])
    assert.deepEqual(err('A', 'x'.repeat(LUA_MAX_CONTENT_LENGTH)), ['tooLong'])
    // Grenze genau: A "<code>" = Laenge code + 4
    assert.deepEqual(err('A', 'x'.repeat(LUA_MAX_CONTENT_LENGTH - 4)), [])
    assert.deepEqual(err('A', 'x'.repeat(LUA_MAX_CONTENT_LENGTH - 3)), ['tooLong'])
    assert.deepEqual(err('a', "ifportup(443, {'192.0.2.1'})"), [])
})

test('analyzeLuaCode: Warnungen', () => {
    assert.deepEqual(analyzeLuaCode('A', "x() -- kommentar").warnings, [])
    assert.deepEqual(analyzeLuaCode('A', "x()\n-- kommentar\ny()").warnings, ['lineComment'])
    assert.deepEqual(analyzeLuaCode('A', "f('a\\.b')").warnings, ['backslash'])
    assert.deepEqual(analyzeLuaCode('A', "f('a\\.b')").errors, [])
})

// ------------------------------------------------------------------ Vorlagen und Geo

test('LUA_TEMPLATES: alle ohne Fehler, Ziel-Typ erlaubt, Gruppen gueltig, ids eindeutig', () => {
    assert.equal(LUA_TEMPLATES.length, 9)
    const ids = new Set()
    for (const tpl of LUA_TEMPLATES) {
        assert.ok(!ids.has(tpl.id), tpl.id)
        ids.add(tpl.id)
        assert.ok(LUA_TARGET_TYPES.includes(tpl.rtype), tpl.id)
        assert.ok(LUA_TEMPLATE_GROUPS.includes(tpl.group), tpl.id)
        assert.deepEqual(analyzeLuaCode(tpl.rtype, tpl.code), { errors: [], warnings: [] }, tpl.id)
        assert.equal(usesGeoFunctions(tpl.code), tpl.geo, `${tpl.id}: geo-Flag passt nicht zum Code`)
        assert.equal(tpl.geo, tpl.group === 'geo', tpl.id)
    }
    assert.equal(luaTemplateById('latlon').rtype, 'TXT')
    assert.equal(luaTemplateById('fehlt'), null)
})

test('usesGeoFunctions', () => {
    assert.equal(usesGeoFunctions("pickclosest({'192.0.2.1'})"), true)
    assert.equal(usesGeoFunctions("country ('DE')"), true)
    assert.equal(usesGeoFunctions("ifportup(443, {'192.0.2.1'})"), false)
    assert.equal(usesGeoFunctions("mycountry('DE')"), false)
    assert.equal(usesGeoFunctions(undefined), false)
})

// ------------------------------------------------------------------ Server-Status

const STATUS = {
    servers: [
        { name: 'ns1', lua_records: 'yes', geoip_backend: true, reachable: true, error: null },
        { name: 'ns2', lua_records: 'no', geoip_backend: false, reachable: true, error: null },
        { name: 'ns3', lua_records: null, geoip_backend: null, reachable: false, error: 'Server nicht erreichbar' },
        { name: 'ns4', lua_records: 'shared', geoip_backend: null, reachable: true, error: null },
    ],
}

test('relevantLuaServers: aktueller Server + schreibbare, erreichbare Peers', () => {
    const all = [
        { name: 'ns1', allow_writes: true, is_reachable: true },
        { name: 'ns2', allow_writes: true, is_reachable: true },
        { name: 'ns3', allow_writes: false, is_reachable: true },
        { name: 'ns4', is_reachable: false },
        { name: 'ns5', is_reachable: true },
    ]
    assert.deepEqual(relevantLuaServers('ns1', all), ['ns1', 'ns2', 'ns5'])
    assert.deepEqual(relevantLuaServers('ns9', []), ['ns9'])
    assert.deepEqual(relevantLuaServers('', null), [])
})

test('luaStatusLine/luaInactiveServers/luaServersWithoutGeoip', () => {
    assert.deepEqual(luaStatusLine(STATUS, 'ns1'), { server: 'ns1', kind: 'enabled', mode: 'yes', error: null })
    assert.deepEqual(luaStatusLine(STATUS, 'ns4'), { server: 'ns4', kind: 'enabled', mode: 'shared', error: null })
    assert.deepEqual(luaStatusLine(STATUS, 'ns2'), { server: 'ns2', kind: 'disabled', mode: 'no', error: null })
    assert.deepEqual(luaStatusLine(STATUS, 'ns3'),
        { server: 'ns3', kind: 'unknown', mode: null, error: 'Server nicht erreichbar' })
    assert.deepEqual(luaStatusLine(null, 'ns1'), { server: 'ns1', kind: 'unknown', mode: null, error: null })
    assert.deepEqual(luaInactiveServers(STATUS, ['ns1', 'ns2', 'ns3']), ['ns2'])
    assert.deepEqual(luaInactiveServers(null, ['ns1']), [])
    assert.deepEqual(luaServersWithoutGeoip(STATUS, ['ns1', 'ns2', 'ns3', 'ns4']), ['ns2'])
})

// ------------------------------------------------------------------ Filter (F15 2.5)

const RECORDS = [
    { name: 'example.com.', type: 'SOA', content: 'ns1.example.com. hostmaster.example.com. 1 2 3 4 5' },
    { name: 'www.example.com.', type: 'A', content: '192.0.2.1' },
    { name: 'geo.example.com.', type: 'LUA', content: "A \"pickclosest({'192.0.2.1', '198.51.100.1'})\"" },
    { name: 'mail.example.com.', type: 'MX', content: '10 mx.example.com.' },
    { name: 'x.example.com.', type: 'CERT', content: '1 2 3 abc' },
    { name: 'y.example.com.', type: 'URI', content: '10 1 "https://example.com"' },
]

test('filterRecords: Name, Typ oder Inhalt; Typauswahl exakt; Gross/klein egal', () => {
    assert.equal(filterRecords(RECORDS, { text: '', type: '' }), RECORDS)
    assert.deepEqual(filterRecords(RECORDS, { text: 'PICKCLOSEST' }).map((r) => r.name), ['geo.example.com.'])
    assert.deepEqual(filterRecords(RECORDS, { text: ' 192.0.2.1 ' }).map((r) => r.type), ['A', 'LUA'])
    assert.deepEqual(filterRecords(RECORDS, { text: '192.0.2.1', type: 'LUA' }).map((r) => r.type), ['LUA'])
    assert.deepEqual(filterRecords(RECORDS, { type: 'MX' }).map((r) => r.type), ['MX'])
    assert.deepEqual(filterRecords(RECORDS, { text: 'lua' }).map((r) => r.type), ['LUA'])
    assert.deepEqual(filterRecords(RECORDS, { text: 'nichts' }), [])
    assert.deepEqual(normalizeRecordFilter({ text: '  AbC ', type: '' }), { text: 'abc', type: '', active: true })
    assert.equal(normalizeRecordFilter(null).active, false)
})

test('recordFilterTypes: Reihenfolge der Typliste, unbekannte Typen alphabetisch hinten', () => {
    assert.deepEqual(recordFilterTypes(RECORDS, ALL_RECORD_TYPE_KEYS), ['SOA', 'A', 'LUA', 'MX', 'CERT', 'URI'])
    assert.deepEqual(recordFilterTypes([], ALL_RECORD_TYPE_KEYS), [])
})

// ------------------------------------------------------------------ Sync-Test mit dem Backend (F15 9.1-14)

function pyTuple(text, name) {
    const m = new RegExp(`^${name}(?:\\s*:[^=]+)?\\s*=\\s*\\(([^)]*)\\)`, 'm').exec(text)
    assert.ok(m, `${name} im Backend nicht gefunden`)
    return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1])
}

test('Sync: LUA_TARGET_TYPES, LUA_MAX_CONTENT_LENGTH, GEO_FUNCTIONS und Typliste wie im Backend', (t) => {
    const luaPy = path.join(BACKEND, 'services', 'lua_records.py')
    const dnsPy = path.join(BACKEND, 'schemas', 'dns.py')
    if (!fs.existsSync(luaPy) || !fs.existsSync(dnsPy)) {
        t.skip('Backend-Quellen nicht vorhanden (Frontend-only-Checkout)')
        return
    }
    const lua = fs.readFileSync(luaPy, 'utf8')
    assert.deepEqual(pyTuple(lua, 'LUA_TARGET_TYPES'), LUA_TARGET_TYPES)
    assert.deepEqual(pyTuple(lua, 'GEO_FUNCTIONS'), GEO_FUNCTIONS)
    const max = /^LUA_MAX_CONTENT_LENGTH\s*=\s*(\d+)/m.exec(lua)
    assert.ok(max)
    assert.equal(Number(max[1]), LUA_MAX_CONTENT_LENGTH)
    const allowed = pyTuple(fs.readFileSync(dnsPy, 'utf8'), 'ALLOWED_RECORD_TYPES')
    assert.deepEqual([...ALL_RECORD_TYPE_KEYS].sort(), [...allowed].sort())
    assert.equal(ALL_RECORD_TYPE_KEYS.indexOf('LUA'), ALL_RECORD_TYPE_KEYS.indexOf('ALIAS') + 1)
})
