// Tests fuer die reinen Hilfsmodule von WS-F8b (node --test, keine Abhaengigkeiten):
// lib/recordContent.js (F8-F03/F04/F09/F10), lib/searchResults.js (E03/E05), lib/settingsForms.js (D02/D04/D05/D08/B07).
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
    CAA_COMMON_TAGS, CAA_TAGS, FQDN_RE, NAME_ERROR_KEYS, buildCaa, classifyHostname, defaultFieldSet, parseCaa,
    rrsetValueCount, selectOptions, truncateValue,
} from '../src/lib/recordContent.js'
import { normalizeRecordName } from '../src/lib/dnsName.js'
import { SEARCH_MAX_RESULTS, mergeSearchOutcomes, searchResultLink, searchResultZone } from '../src/lib/searchResults.js'
import {
    PROFILE_OPTIONAL_FIELDS, VERSION_CACHE_MAX_AGE_MS, buildAppInfoPayload, buildProfilePayload, commitErrorKind,
    makeVersionCacheEntry, readVersionCache, smtpPasswordValue,
} from '../src/lib/settingsForms.js'

// ---------------------------------------------------------------- recordContent: CAA (F8-F03, N18, N19)

test('CAA: Tag-Auswahl und gebraeuchliche Tags', () => {
    assert.deepEqual([...CAA_TAGS], ['issue', 'issuewild', 'issuemail', 'iodef', 'contactemail', 'contactphone'])
    assert.deepEqual([...CAA_COMMON_TAGS], ['issue', 'issuewild', 'iodef'])
    for (const tag of CAA_COMMON_TAGS) assert.ok(CAA_TAGS.includes(tag))
})

test('buildCaa: Wert in Anfuehrungszeichen, " und \\ escaped', () => {
    assert.equal(buildCaa({ flag: '0', tag: 'issue', val: 'letsencrypt.org' }), '0 issue "letsencrypt.org"')
    assert.equal(buildCaa({ flag: '128', tag: 'iodef', val: 'mailto:a@b.de' }), '128 iodef "mailto:a@b.de"')
    assert.equal(buildCaa({ flag: '0', tag: 'issue', val: 'a"b' }), '0 issue "a\\"b"')
    assert.equal(buildCaa({ flag: '0', tag: 'issue', val: 'a\\b' }), '0 issue "a\\\\b"')
    assert.equal(buildCaa({ flag: '0', tag: 'issue', val: '' }), '0 issue ""')
})

test('parseCaa: Gegenstueck zu buildCaa (Round-Trip), Leerzeichen im Wert bleiben', () => {
    for (const val of ['letsencrypt.org', 'a"b', 'a\\b', 'x\\"y', 'ca.example; validationmethods=dns-01', '']) {
        const built = buildCaa({ flag: '0', tag: 'issue', val })
        assert.deepEqual(parseCaa(built), { flag: '0', tag: 'issue', val }, built)
    }
    assert.deepEqual(parseCaa('0 issuewild "a b c"'), { flag: '0', tag: 'issuewild', val: 'a b c' })
    // ohne Anfuehrungszeichen (z. B. aus einem Import) bleibt der Wert erhalten
    assert.deepEqual(parseCaa('0 issue letsencrypt.org'), { flag: '0', tag: 'issue', val: 'letsencrypt.org' })
})

test('parseCaa: unbekannter Tag bleibt erhalten, unpassender Inhalt -> Fallback mit Rohwert', () => {
    assert.deepEqual(parseCaa('0 tbs "x"'), { flag: '0', tag: 'tbs', val: 'x' })
    assert.deepEqual(parseCaa('kaputt'), { flag: '0', tag: 'issue', val: 'kaputt' })
    assert.deepEqual(parseCaa(''), { flag: '0', tag: 'issue', val: '' })
})

test('defaultFieldSet: nur Felder mit default', () => {
    const def = { fields: [{ id: 'flag', default: '0' }, { id: 'tag', default: 'issue' }, { id: 'val' }] }
    assert.deepEqual(defaultFieldSet(def), { flag: '0', tag: 'issue' })
    assert.deepEqual(defaultFieldSet({ fields: [{ id: 'ipv4' }] }), {})
    assert.deepEqual(defaultFieldSet(undefined), {})
    assert.deepEqual(defaultFieldSet({ fields: [{ id: 'rtype', default: 'A' }] }), { rtype: 'A' }) // F15 LUA
})

test('selectOptions: unbekannter aktueller Wert steht vorne, bekannte Werte unveraendert', () => {
    assert.deepEqual(selectOptions(['issue', 'iodef'], 'issue'), ['issue', 'iodef'])
    assert.deepEqual(selectOptions(['issue', 'iodef'], 'tbs'), ['tbs', 'issue', 'iodef'])
    assert.deepEqual(selectOptions(['issue'], ''), ['issue'])
    assert.deepEqual(selectOptions(['issue'], undefined), ['issue'])
    const src = ['issue']
    selectOptions(src, 'x')
    assert.deepEqual(src, ['issue'], 'Eingabeliste wird nicht veraendert')
})

// ---------------------------------------------------------------- recordContent: Hostnamen (F8-F10, f103)

test('classifyHostname: gueltige Namen inkl. Punycode-TLD, Unterstrich, Punkt am Ende', () => {
    for (const v of ['mail.example.com', 'mail.example.com.', 'xn--80ak6aa92e.xn--p1ai', 'srv._tcp.example.de',
        'a.b.c.example.org', 'MAIL.Example.COM']) {
        assert.equal(classifyHostname(v), 'valid', v)
    }
})

test('classifyHostname: einlabelig ist nur Warnung, "." nur mit allowRoot, Fehlerfaelle', () => {
    assert.equal(classifyHostname('localhost'), 'singleLabel')
    assert.equal(classifyHostname('mailhost.'), 'singleLabel')
    assert.equal(classifyHostname('.'), 'empty')
    assert.equal(classifyHostname('.', { allowRoot: true }), 'root')
    assert.equal(classifyHostname(' . ', { allowRoot: true }), 'root')
    assert.equal(classifyHostname(''), 'empty')
    assert.equal(classifyHostname('   '), 'empty')
    assert.equal(classifyHostname('exa mple.com'), 'invalid')
    assert.equal(classifyHostname('-bad.example.com'), 'invalid')
    assert.equal(classifyHostname('a..b.com'), 'invalid')
    assert.equal(classifyHostname('192.0.2.1'), 'invalid', 'IP-Adresse ist kein Hostname')
    assert.equal(classifyHostname(`${'a'.repeat(64)}.example.com`), 'invalid', 'Label > 63')
})

test('FQDN_RE: Gesamtlaenge hoechstens 253 Zeichen', () => {
    const label = 'a'.repeat(63)
    const ok = `${label}.${label}.${label}.${'b'.repeat(58)}.de` // 253 Zeichen
    assert.equal(ok.length, 253)
    assert.ok(FQDN_RE.test(ok))
    assert.ok(!FQDN_RE.test(`x${ok}`))
})

// ---------------------------------------------------------------- recordContent: Loeschen (F8-F09)

test('truncateValue: 80 Zeichen mit Auslassungszeichen', () => {
    assert.equal(truncateValue('kurz'), 'kurz')
    const long = 'x'.repeat(200)
    const cut = truncateValue(long)
    assert.equal(cut.length, 80)
    assert.ok(cut.endsWith('…'))
    assert.equal(truncateValue('x'.repeat(80)), 'x'.repeat(80))
    assert.equal(truncateValue(null), '')
})

test('rrsetValueCount: Name ohne Gross-/Kleinschreibung und Punkt, nur gleicher Typ', () => {
    const records = [
        { name: 'www.example.com.', type: 'A', content: '192.0.2.1' },
        { name: 'WWW.example.com.', type: 'A', content: '192.0.2.2' },
        { name: 'www.example.com.', type: 'AAAA', content: '2001:db8::1' },
        { name: 'mail.example.com.', type: 'A', content: '192.0.2.3' },
    ]
    assert.equal(rrsetValueCount(records, 'www.example.com.', 'A'), 2)
    assert.equal(rrsetValueCount(records, 'www.example.com', 'A'), 2)
    assert.equal(rrsetValueCount(records, 'www.example.com.', 'AAAA'), 1)
    assert.equal(rrsetValueCount(records, 'nix.example.com.', 'A'), 0)
    assert.equal(rrsetValueCount(undefined, 'x', 'A'), 0)
})

test('NAME_ERROR_KEYS deckt alle Fehlercodes von normalizeRecordName ab', () => {
    const codes = new Set()
    for (const input of ['a b', 'a..b', `${'x'.repeat(64)}`, `${'abcdefghi.'.repeat(26)}x`, 'x.other.com.']) {
        const r = normalizeRecordName(input, 'test.de')
        assert.ok(r.error, input)
        codes.add(r.error)
    }
    assert.deepEqual([...codes].sort(), Object.keys(NAME_ERROR_KEYS).sort())
    for (const key of Object.values(NAME_ERROR_KEYS)) assert.match(key, /^zoneDetail\.name[A-Z]/)
})

// ---------------------------------------------------------------- searchResults (F8-E03, E05)

test('searchResultZone / searchResultLink', () => {
    assert.equal(searchResultZone({ zone_id: 'example.com.' }), 'example.com')
    assert.equal(searchResultZone({ zone: 'example.org.' }), 'example.org')
    assert.equal(searchResultZone({ object_type: 'zone', name: 'kunde.de.' }), 'kunde.de')
    assert.equal(searchResultZone({ object_type: 'record', name: 'www.kunde.de.' }), '')
    assert.equal(searchResultLink({ zone_id: 'ex.com.', _servers: ['ns 1', 'ns2'] }), '/zones/ns%201/ex.com')
    assert.equal(searchResultLink({ zone_id: 'ex.com.', _servers: [] }), '')
    assert.equal(searchResultLink({ _servers: ['ns1'] }), '')
})

test('mergeSearchOutcomes: zusammenfuehren, Fehler, Kappung, Abbruch', () => {
    const abort = Object.assign(new Error('aborted'), { name: 'AbortError' })
    const res = mergeSearchOutcomes([
        { server: 'ns1', status: 'fulfilled', value: { truncated: true, results: [
            { name: 'a.ex.', type: 'A', content: '1' },
            { name: 'b.ex.', type: 'A', content: '2' },
        ] } },
        { server: 'ns2', status: 'fulfilled', value: { results: [{ name: 'a.ex.', type: 'A', content: '1' }] } },
        { server: 'ns3', status: 'rejected', reason: new Error('Server nicht erreichbar (x)') },
        { server: 'ns4', status: 'rejected', reason: abort },
    ])
    assert.equal(res.results.length, 2)
    assert.deepEqual(res.results[0]._servers, ['ns1', 'ns2'])
    assert.deepEqual(res.results[1]._servers, ['ns1'])
    assert.deepEqual(res.serverErrors, [{ server: 'ns3', message: 'Server nicht erreichbar (x)' }])
    assert.deepEqual(res.truncatedServers, ['ns1'])
    assert.equal(res.aborted, true)
    assert.equal(SEARCH_MAX_RESULTS, 100)
})

test('mergeSearchOutcomes: leere Eingabe', () => {
    assert.deepEqual(mergeSearchOutcomes([]), { results: [], serverErrors: [], truncatedServers: [], aborted: false })
})

// ---------------------------------------------------------------- settingsForms (F8-D02, D04, D05, D08, B07)

test('buildProfilePayload: Felder immer als String (leer = leeren), Sprache nur wenn gesetzt', () => {
    const body = buildProfilePayload({
        username: 'max', display_name: '', email: 'max@example.com',
        phone: '', company: 'ACME', street: undefined, postal_code: '', city: '', country: '',
        date_of_birth: '', preferred_language: '',
    })
    for (const key of PROFILE_OPTIONAL_FIELDS) assert.equal(typeof body[key], 'string', key)
    assert.equal(body.phone, '')
    assert.equal(body.company, 'ACME')
    assert.equal(body.street, '')
    assert.equal(body.date_of_birth, '')
    assert.ok(!('preferred_language' in body))
    assert.equal(buildProfilePayload({ preferred_language: 'hu' }).preferred_language, 'hu')
})

test('buildAppInfoPayload: getrimmt, Logo leer = entfernen, Basis-URL nur wenn geladen', () => {
    const form = {
        app_name: ' PDNS ', app_base_url: '  ', registration_enabled: 1, forgot_password_enabled: 0,
        app_tagline: '  ', app_creator: ' Team ', app_logo_url: '',
    }
    const body = buildAppInfoPayload(form)
    assert.deepEqual(body, {
        app_name: 'PDNS', registration_enabled: true, forgot_password_enabled: false,
        app_tagline: '', app_creator: 'Team', app_logo_url: '', app_base_url: '',
    })
    assert.ok(!('app_base_url' in buildAppInfoPayload(form, { includeBaseUrl: false })))
    assert.equal(buildAppInfoPayload({ app_logo_url: '/uploads/custom-logo.png' }).app_logo_url, '/uploads/custom-logo.png')
})

test('smtpPasswordValue: neu ersetzt, Haken loescht, sonst null = behalten', () => {
    assert.equal(smtpPasswordValue('neu', false), 'neu')
    assert.equal(smtpPasswordValue('neu', true), 'neu')
    assert.equal(smtpPasswordValue('', true), '')
    assert.equal(smtpPasswordValue('', false), null)
    assert.equal(smtpPasswordValue(undefined, false), null)
})

test('commitErrorKind: 404 / 403+429 / sonst', () => {
    assert.equal(commitErrorKind(404), 'notFound')
    assert.equal(commitErrorKind(403), 'rateLimit')
    assert.equal(commitErrorKind(429), 'rateLimit')
    assert.equal(commitErrorKind(500), 'other')
    assert.equal(commitErrorKind(undefined), 'other') // Netzwerkfehler/CSP
})

test('Versions-Cache: 6 h gueltig, kaputte Eintraege ignoriert', () => {
    const now = 1_700_000_000_000
    const entry = makeVersionCacheEntry('3.0.0', now)
    assert.equal(readVersionCache(entry, now), '3.0.0')
    assert.equal(readVersionCache(entry, now + VERSION_CACHE_MAX_AGE_MS - 1), '3.0.0')
    assert.equal(readVersionCache(entry, now + VERSION_CACHE_MAX_AGE_MS + 1), null)
    assert.equal(readVersionCache(entry, now - 1000), null, 'Zeitstempel in der Zukunft')
    assert.equal(readVersionCache(null, now), null)
    assert.equal(readVersionCache('{kaputt', now), null)
    assert.equal(readVersionCache(JSON.stringify({ v: '', at: now }), now), null)
    assert.equal(readVersionCache(JSON.stringify({ v: '1.0', at: 'x' }), now), null)
    assert.equal(VERSION_CACHE_MAX_AGE_MS, 6 * 3600 * 1000)
})
