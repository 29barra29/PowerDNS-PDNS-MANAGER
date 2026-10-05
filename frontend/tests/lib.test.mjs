// Reine Hilfsmodule (F8 §9.2, Plan B.14): ohne React/i18n, direkt per node --test ladbar.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import { normalizeRecordName, relativeRecordName } from '../src/lib/dnsName.js'
import { IPV4_RE, isValidIP, isValidIPv4, isValidIPv6 } from '../src/lib/ip.js'
import { TTL_MAX, TTL_MIN, TTL_PRESETS, formatTtl, isValidTtl, parseTtl, splitTtl } from '../src/lib/ttl.js'
import {
    fanoutErrors, fanoutMap, fanoutSummary, fanoutWarnings, formatFanoutErrors, formatFanoutWarnings,
} from '../src/lib/fanout.js'
import {
    EMPTY_VALUE, formatDate, formatDateTime, formatRelative, parseDateValue, toIsoFromLocalInput, toLocalInputFromIso,
} from '../src/lib/datetime.js'
import { buildQuery } from '../src/lib/buildQuery.js'
import { SECRET_MASK, isSecretMask } from '../src/constants/secrets.js'

// ------------------------------------------------------------------ dnsName
test('normalizeRecordName: Apex, Trim, relative und absolute Namen', () => {
    assert.deepEqual(normalizeRecordName('', 'test.de'), { fqdn: 'test.de.' })
    assert.deepEqual(normalizeRecordName('@', 'test.de.'), { fqdn: 'test.de.' })
    assert.deepEqual(normalizeRecordName(undefined, 'test.de'), { fqdn: 'test.de.' })
    assert.deepEqual(normalizeRecordName(' www ', 'test.de'), { fqdn: 'www.test.de.' })
    assert.deepEqual(normalizeRecordName('contest.de', 'test.de'), { fqdn: 'contest.de.test.de.' })
    assert.deepEqual(normalizeRecordName('mail.test.de', 'test.de'), { fqdn: 'mail.test.de.' })
    assert.deepEqual(normalizeRecordName('MAIL.Test.de', 'TEST.de'), { fqdn: 'mail.test.de.' })
    assert.deepEqual(normalizeRecordName('test.de.', 'test.de'), { fqdn: 'test.de.' })
    assert.deepEqual(normalizeRecordName('x.test.de.', 'test.de'), { fqdn: 'x.test.de.' })
})

test('normalizeRecordName: Fehlerfaelle', () => {
    assert.deepEqual(normalizeRecordName('a b', 'test.de'), { error: 'whitespace' })
    assert.deepEqual(normalizeRecordName('a\tb', 'test.de'), { error: 'whitespace' })
    assert.deepEqual(normalizeRecordName('a..b', 'test.de'), { error: 'emptyLabel' })
    assert.deepEqual(normalizeRecordName('.a', 'test.de'), { error: 'emptyLabel' })
    assert.deepEqual(normalizeRecordName('x.other.com.', 'test.de'), { error: 'outsideZone' })
    assert.deepEqual(normalizeRecordName('a'.repeat(64), 'test.de'), { error: 'labelTooLong' })
    assert.deepEqual(normalizeRecordName('a'.repeat(63), 'test.de'), { fqdn: `${'a'.repeat(63)}.test.de.` })
    const long = Array.from({ length: 5 }, () => 'b'.repeat(60)).join('.')
    assert.deepEqual(normalizeRecordName(long, 'test.de'), { error: 'tooLong' })
})

test('relativeRecordName', () => {
    assert.equal(relativeRecordName('test.de.', 'test.de'), '@')
    assert.equal(relativeRecordName('www.Test.de.', 'test.de.'), 'www')
    assert.equal(relativeRecordName('other.com.', 'test.de'), 'other.com')
})

// ------------------------------------------------------------------ ip
test('isValidIPv6: gueltige Adressen', () => {
    for (const ok of ['::', '::1', '2001:db8::1', '::ffff:192.0.2.1', 'fe80::1', '2001:0db8:0000:0000:0000:0000:0000:0001',
        '1:2:3:4:5:6:7:8', '1::8', '1:2:3:4:5:6:7::', ' 2001:db8::1 ', '64:ff9b::192.0.2.33']) {
        assert.equal(isValidIPv6(ok), true, ok)
    }
})

test('isValidIPv6: ungueltige Adressen', () => {
    for (const bad of ['1:2:3:4:5:6:7:8:9', '2001:db8:::1', ':1', 'fe80::1%eth0', '::ffff:999.0.0.1', '', 'abc', '1.2.3.4',
        '2001:db8::g', '12345::', '1::2::3', '1:2:3:4:5:6:7', null, undefined, 42]) {
        assert.equal(isValidIPv6(bad), false, String(bad))
    }
})

test('isValidIPv4 / IPV4_RE / isValidIP', () => {
    assert.equal(isValidIPv4('192.0.2.1'), true)
    assert.equal(isValidIPv4(' 10.0.0.255 '), true)
    assert.equal(isValidIPv4('256.0.0.1'), false)
    assert.equal(isValidIPv4('01.2.3.4'), false)
    assert.equal(isValidIPv4('1.2.3'), false)
    assert.equal(isValidIPv4(undefined), false)
    assert.ok(IPV4_RE.test('0.0.0.0'))
    assert.equal(isValidIP('::1'), true)
    assert.equal(isValidIP('1.2.3.4'), true)
    assert.equal(isValidIP('nope'), false)
})

// ------------------------------------------------------------------ ttl
test('parseTtl / isValidTtl', () => {
    assert.equal(parseTtl('3600'), 3600)
    assert.equal(parseTtl(' 60 '), 60)
    assert.equal(parseTtl(300), 300)
    assert.equal(parseTtl('1e3'), null)
    assert.equal(parseTtl('-5'), null)
    assert.equal(parseTtl('3.5'), null)
    assert.equal(parseTtl(''), null)
    assert.equal(parseTtl(null), null)
    assert.equal(isValidTtl('60'), true)
    assert.equal(isValidTtl('59'), false)
    assert.equal(isValidTtl(String(TTL_MAX)), true)
    assert.equal(isValidTtl(String(TTL_MAX + 1)), false)
    assert.equal(isValidTtl('30', 1, 100), true)
    assert.equal(isValidTtl('abc'), false)
    assert.equal(TTL_MIN, 60)
    assert.ok(TTL_PRESETS.every((s) => s >= TTL_MIN && s <= TTL_MAX))
})

test('splitTtl waehlt die groesste glatte Einheit', () => {
    assert.deepEqual(splitTtl(7200), { unit: 'hours', count: 2 })
    assert.deepEqual(splitTtl(90), { unit: 'seconds', count: 90 })
    assert.deepEqual(splitTtl(604800), { unit: 'weeks', count: 1 })
    assert.deepEqual(splitTtl(86400), { unit: 'days', count: 1 })
    assert.deepEqual(splitTtl(300), { unit: 'minutes', count: 5 })
    assert.deepEqual(splitTtl(5400), { unit: 'minutes', count: 90 })
    assert.deepEqual(splitTtl(0), { unit: 'seconds', count: 0 })
})

test('formatTtl nutzt die uebergebene t-Funktion mit Plural-count', () => {
    const calls = []
    const t = (key, opts) => { calls.push([key, opts]); return `${key}:${opts.count}` }
    assert.equal(formatTtl(3600, t), 'ttl.hours:1')
    assert.equal(formatTtl(120, t), 'ttl.minutes:2')
    assert.equal(formatTtl(61, t), 'ttl.seconds:61')
    assert.deepEqual(calls[0], ['ttl.hours', { count: 1 }])
})

// ------------------------------------------------------------------ fanout
test('fanoutErrors: flache Map und details.fanout', () => {
    const flat = { ns1: 'saved', ns2: 'error: HTTP 422 – bad', ns3: 'skipped (read-only)' }
    assert.deepEqual(fanoutErrors(flat), [{ server: 'ns2', message: 'HTTP 422 – bad' }])
    const nested = { zone: 'x.de.', fanout: { a: 'deleted', b: 'error:timeout', c: 'error: unklar – bitte Zone neu laden' } }
    assert.deepEqual(fanoutErrors(nested), [
        { server: 'b', message: 'timeout' },
        { server: 'c', message: 'unklar – bitte Zone neu laden' },
    ])
    assert.deepEqual(fanoutErrors(null), [])
    assert.deepEqual(fanoutErrors({ fanout: null, a: 'error: x' }), [{ server: 'a', message: 'x' }])
    assert.deepEqual(fanoutErrors({ a: 42, b: { nested: 'error: no' } }), [])
    assert.equal(formatFanoutErrors(fanoutErrors(flat)), 'ns2: HTTP 422 – bad')
    assert.equal(formatFanoutErrors([]), '')
})

test('fanoutWarnings: skipped (not loaded: ...) ist eine Warnung [D4]', () => {
    const d = { fanout: {
        ns1: 'saved',
        ns2: 'skipped (not loaded: connection refused)',
        ns3: 'skipped (zone not present)',
        ns4: 'skipped (not loaded)',
        ns5: 'error: boom',
    } }
    assert.deepEqual(fanoutWarnings(d), [
        { server: 'ns2', reason: 'connection refused' },
        { server: 'ns4', reason: '' },
    ])
    assert.equal(formatFanoutWarnings(fanoutWarnings(d)), 'ns2: connection refused · ns4')
    const s = fanoutSummary(d)
    assert.equal(s.hasErrors, true)
    assert.equal(s.hasWarnings, true)
    assert.equal(s.errors.length, 1)
    assert.deepEqual(fanoutMap(d), d.fanout)
    assert.deepEqual(fanoutSummary({ a: 'saved' }), { errors: [], warnings: [], hasErrors: false, hasWarnings: false })
})

// ------------------------------------------------------------------ datetime
test('formatDateTime: leer, ungueltig, ISO mit Offset', () => {
    assert.equal(formatDateTime(null, 'de'), '–')
    assert.equal(formatDateTime(undefined, 'en'), EMPTY_VALUE)
    assert.equal(formatDateTime('', 'en'), '–')
    assert.equal(formatDateTime('kein datum', 'de'), 'kein datum')
    const s = formatDateTime('2026-10-05T12:34:56+00:00', 'de')
    assert.match(s, /2026|26/)
    assert.notEqual(s, '2026-10-05T12:34:56+00:00')
    const full = formatDateTime('2026-10-05T12:34:56+00:00', 'en', { year: 'numeric', timeZone: 'UTC' })
    assert.equal(full, '2026')
})

test('formatDateTime: lang ist Pflicht', () => {
    assert.throws(() => formatDateTime('2026-01-01T00:00:00Z'), TypeError)
    assert.throws(() => formatDateTime('2026-01-01T00:00:00Z', ''), TypeError)
    assert.throws(() => formatDate(null), TypeError)
    assert.throws(() => formatRelative(null), TypeError)
})

test('formatDateTime: Sprache wirkt, unbekannte Tags fallen auf en zurueck', () => {
    const opts = { month: 'long', timeZone: 'UTC' }
    assert.equal(formatDateTime('2026-03-15T00:00:00Z', 'de', opts), 'März')
    assert.equal(formatDateTime('2026-03-15T00:00:00Z', 'en', opts), 'March')
    assert.equal(formatDateTime('2026-03-15T00:00:00Z', 'not a lang!', opts), 'March')
    // App-Sprache sr = lateinische Schrift
    assert.equal(formatDateTime('2026-03-15T00:00:00Z', 'sr', opts), 'mart')
    assert.equal(formatRelative('2026-10-05T11:00:00Z', 'sr', Date.parse('2026-10-05T12:00:00Z')), 'pre 1 sata')
})

test('parseDateValue: naive ISO-Werte gelten als UTC', () => {
    assert.equal(parseDateValue('2026-10-05T12:00:00').toISOString(), '2026-10-05T12:00:00.000Z')
    assert.equal(parseDateValue('2026-10-05 12:00:00.5').toISOString(), '2026-10-05T12:00:00.500Z')
    assert.equal(parseDateValue('2026-10-05T12:00:00+02:00').toISOString(), '2026-10-05T10:00:00.000Z')
    assert.equal(parseDateValue(0).toISOString(), '1970-01-01T00:00:00.000Z')
    assert.equal(parseDateValue(new Date('x')), null)
    assert.equal(parseDateValue({}), null)
    assert.equal(parseDateValue(''), null)
})

test('formatDate und formatRelative', () => {
    assert.match(formatDate('2026-10-05T00:00:00Z', 'en'), /2026/)
    const now = Date.parse('2026-10-05T12:00:00Z')
    assert.equal(formatRelative('2026-10-05T11:55:00Z', 'en', now), '5 minutes ago')
    assert.equal(formatRelative('2026-10-07T12:00:00Z', 'en', new Date(now)), 'in 2 days')
    assert.equal(formatRelative('2026-10-05T12:00:00Z', 'en', now), 'now')
    assert.equal(formatRelative('2026-10-05T11:00:00Z', 'de', now), 'vor 1 Stunde')
    assert.equal(formatRelative(null, 'de', now), '–')
    assert.equal(formatRelative('xyz', 'de', now), 'xyz')
})

test('datetime-local <-> ISO', () => {
    assert.equal(toIsoFromLocalInput(''), '')
    assert.equal(toIsoFromLocalInput('nonsense'), '')
    const iso = toIsoFromLocalInput('2026-10-05T12:30')
    assert.match(iso, /^2026-10-0[45]T\d{2}:30:00\.000Z$/)
    assert.equal(toLocalInputFromIso(iso), '2026-10-05T12:30')
    assert.equal(toLocalInputFromIso(null), '')
    assert.equal(toLocalInputFromIso('bad'), '')
})

// ------------------------------------------------------------------ buildQuery / secrets
test('buildQuery ueberspringt leere Werte', () => {
    assert.equal(buildQuery(), '')
    assert.equal(buildQuery({}), '')
    assert.equal(buildQuery({ a: undefined, b: null, c: '' }), '')
    assert.equal(buildQuery({ limit: 50, offset: 0, q: 'a b&c' }), '?limit=50&offset=0&q=a+b%26c')
    assert.equal(buildQuery({ status: ['queued', '', 'failed'] }), '?status=queued&status=failed')
    assert.equal(buildQuery({ flag: false }), '?flag=false')
    assert.equal(buildQuery(null), '')
})

test('SECRET_MASK entspricht dem Backend-Wert', () => {
    assert.equal(SECRET_MASK, '•'.repeat(8))
    assert.equal(isSecretMask(` ${SECRET_MASK} `), true)
    assert.equal(isSecretMask('geheim'), false)
    assert.equal(isSecretMask(null), false)
})
