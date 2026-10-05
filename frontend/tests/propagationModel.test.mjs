// Tests fuer zoneDetail/propagationModel.js (F12 §6.4, F13 §2.3, Plan WS-F12F13-FE inkl. [D9]).
// Reine Funktionen; `t` wird durch einen Fake ersetzt, der Key und Parameter sichtbar macht.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
    DEFAULT_RESOLVERS, GRAFANA_QUERIES, KIND_LABEL_KEYS, KIND_ORDER, SCRAPE_PLACEHOLDER_HOST, backgroundTasks,
    buildCheckParams, buildCurlCommand, buildScrapeYaml, canRunCheck, contentInfo, errorText, formatSerial,
    groupSources, isSeparateBackendSame, latencyText, monitoringStatusRows, needsRecordType, noteText,
    parseResolverText, recordCell, resolverText, resultNotices, scrapeTarget, serialTone, sourceLabel,
    statusBadgeClass, statusLabelKey, summaryInfo, visibleNoteCodes,
} from '../src/zoneDetail/propagationModel.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')

// Fake-t: "key" bzw. "key|{params}"; defaultValue wird zurueckgegeben, wenn der Key "unbekannt" ist
function makeT(known = null) {
    return (key, opts = {}) => {
        if (known && !known.has(key) && opts.defaultValue !== undefined) return opts.defaultValue
        const { defaultValue: _d, ...params } = opts
        return Object.keys(params).length ? `${key}|${JSON.stringify(params)}` : key
    }
}
const t = makeT()

function panel(extra = {}) {
    return { source: 'ns2', kind: 'panel-api', status: 'ok', serial: 2026100503, match: true, serial_relation: 'equal', notes: [], content_diff_sample: [], ...extra }
}

// ---------------------------------------------------------------------------------------------- Gruppen/Badges
test('groupSources: Reihenfolge panel-api, authoritative, resolver; leere Gruppen fehlen; Reihenfolge in der Gruppe bleibt', () => {
    const sources = [
        { source: '1.1.1.1', kind: 'resolver' },
        { source: 'ns1', kind: 'panel-api', is_reference: true },
        { source: 'ns2', kind: 'panel-api' },
        { source: 'a.ns.example.', kind: 'authoritative' },
        null,
    ]
    const groups = groupSources(sources)
    assert.deepEqual(groups.map((g) => g.kind), ['panel-api', 'authoritative', 'resolver'])
    assert.deepEqual(groups[0].rows.map((r) => r.source), ['ns1', 'ns2'])
    assert.equal(groups[0].labelKey, 'propagation.groupPanel')
    assert.deepEqual(groupSources([{ source: 'x', kind: 'resolver' }]).map((g) => g.kind), ['resolver'])
    assert.deepEqual(groupSources(undefined), [])
    // unbekannte Art wird hinten angehaengt (Vorwaertskompatibilitaet)
    const g2 = groupSources([{ source: 'q', kind: 'future' }, { source: 'r', kind: 'resolver' }])
    assert.deepEqual(g2.map((g) => [g.kind, g.labelKey]), [['resolver', 'propagation.groupResolver'], ['future', null]])
    assert.deepEqual(KIND_ORDER, Object.keys(KIND_LABEL_KEYS))
})

test('statusBadgeClass/statusLabelKey: Farben und Keys je Status, Fallback skipped', () => {
    assert.match(statusBadgeClass('ok'), /bg-success\/10/)
    assert.match(statusBadgeClass('mismatch'), /text-warning/)
    assert.match(statusBadgeClass('error'), /text-danger/)
    assert.match(statusBadgeClass('timeout'), /text-danger/)
    assert.match(statusBadgeClass('skipped'), /bg-bg-secondary text-text-muted/)
    assert.match(statusBadgeClass('???'), /text-text-muted/)
    assert.equal(statusLabelKey('ok'), 'propagation.statusOk')
    assert.equal(statusLabelKey('mismatch'), 'propagation.statusMismatch')
    assert.equal(statusLabelKey('error'), 'propagation.statusError')
    assert.equal(statusLabelKey('timeout'), 'propagation.statusTimeout')
    assert.equal(statusLabelKey('skipped'), 'propagation.statusSkipped')
    assert.equal(statusLabelKey('nope'), 'propagation.statusSkipped')
})

// ---------------------------------------------------------------------------------------------- Texte
test('noteText: Key note_<code> mit ttl; unbekannter Code bleibt roh', () => {
    assert.equal(noteText(t, 'resolver_cache', { ttl: 1240 }), 'propagation.note_resolver_cache|{"ttl":1240}')
    assert.equal(noteText(t, 'resolver_cache', {}), 'propagation.note_resolver_cache|{"ttl":"?"}')
    const tk = makeT(new Set(['propagation.note_resolver_cache']))
    assert.equal(noteText(tk, 'brand_new_code', {}), 'brand_new_code')
})

test('errorText: uebersetzter Code, Fallback Backend-Text, api_error behaelt Admin-Detail, ohne Code leer', () => {
    assert.equal(errorText(t, { error_code: 'refused', error: 'Anfrage abgelehnt' }), 'propagation.err_refused')
    const tk = makeT(new Set())
    assert.equal(errorText(tk, { error_code: 'not_loaded', error: 'Server nicht geladen' }), 'Server nicht geladen')
    assert.equal(errorText(tk, { error_code: 'xyz' }), 'xyz')
    assert.equal(errorText(t, { error_code: 'api_error', error: 'PowerDNS-API-Fehler: Zone locked' }), 'propagation.err_api_error: Zone locked')
    assert.equal(errorText(t, { error_code: 'api_error', error: 'PowerDNS-API-Fehler' }), 'propagation.err_api_error')
    assert.equal(errorText(t, { status: 'ok' }), '')
    assert.equal(errorText(t, null), '')
})

test('formatSerial/serialTone: Relation aelter/neuer, fehlende Serial, getrennte Backends ohne Relation', () => {
    assert.equal(formatSerial(panel(), t), '2026100503')
    assert.equal(formatSerial(panel({ serial: 2026100501, match: false, serial_relation: 'behind', status: 'mismatch' }), t), '2026100501 (propagation.serialBehind)')
    assert.equal(formatSerial(panel({ serial: 2026100509, match: false, serial_relation: 'ahead', status: 'mismatch' }), t), '2026100509 (propagation.serialAhead)')
    assert.equal(formatSerial({ serial: null }, t), '–')
    assert.equal(formatSerial(undefined, t), '–')
    assert.equal(formatSerial({ serial: 0, serial_relation: 'equal' }, t), '0')
    const sep = panel({ serial: 7, match: false, serial_relation: 'behind', notes: ['separate_backend'], content_match: true })
    assert.equal(formatSerial(sep, t), '7')
    assert.equal(serialTone(sep), 'normal')
    assert.equal(serialTone(panel({ match: false })), 'warn')
    assert.equal(serialTone({ serial: null }), 'muted')
    assert.equal(serialTone(panel()), 'normal')
})

// ---------------------------------------------------------------------------------------------- [D9] Peer-Zeile
test('[D9] Peer mit getrennter DB und gleichem Inhalt: ok mit "Inhalt gleich, Serial abweichend (getrennte Datenbank)"', () => {
    const row = panel({
        serial: 2026100401, match: false, serial_relation: 'behind', status: 'ok',
        notes: ['separate_backend'], content_match: true, content_diff_count: 0,
    })
    assert.equal(isSeparateBackendSame(row), true)
    assert.deepEqual(contentInfo(row), { key: 'propagation.contentSameSeparate', params: {}, ok: true, sample: [] })
    assert.deepEqual(visibleNoteCodes(row), [])
    // mit content=true schickt das Backend zusaetzlich content_same_serial_differs - wird zusammengefasst
    const row2 = { ...row, notes: ['content_same_serial_differs', 'separate_backend', 'notify_pending'] }
    assert.equal(contentInfo(row2).key, 'propagation.contentSameSeparate')
    assert.deepEqual(visibleNoteCodes(row2), ['notify_pending'])
})

test('[D9] Peer mit getrennter DB und abweichendem Inhalt: mismatch, Inhalt abweichend + Beispiele, Notiz bleibt', () => {
    const row = panel({
        serial: 2026100401, match: false, serial_relation: 'behind', status: 'mismatch',
        notes: ['separate_backend'], content_match: false, content_diff_count: 7,
        content_diff_sample: ['a.example.com. A', 'b.example.com. TXT', 'c. A', 'd. A', 'e. A', 'f. A'],
    })
    assert.equal(isSeparateBackendSame(row), false)
    const info = contentInfo(row)
    assert.equal(info.key, 'propagation.contentDiffers')
    assert.deepEqual(info.params, { count: 7 })
    assert.equal(info.ok, false)
    assert.equal(info.sample.length, 5)
    assert.deepEqual(visibleNoteCodes(row), ['separate_backend'])
    assert.equal(formatSerial(row, t), '2026100401 (propagation.serialBehind)')
    assert.equal(serialTone(row), 'warn')
})

test('contentInfo: Referenz, DNS-Zeilen und Zeilen ohne Vergleich liefern null; gleicher Inhalt ohne Serial-Abweichung', () => {
    assert.equal(contentInfo(panel({ is_reference: true, content_match: true })), null)
    assert.equal(contentInfo({ kind: 'resolver', content_match: true }), null)
    assert.equal(contentInfo(panel()), null)
    assert.equal(contentInfo(null), null)
    assert.deepEqual(contentInfo(panel({ content_match: true, content_diff_count: 0 })), { key: 'propagation.contentSame', params: {}, ok: true, sample: [] })
    // ohne getrennte Backends: Inhalt gleich, Serial anders -> Notiz content_same_serial_differs entfaellt neben "Inhalt gleich"
    const row = panel({ match: false, status: 'ok', content_match: true, notes: ['content_same_serial_differs'] })
    assert.equal(contentInfo(row).key, 'propagation.contentSame')
    assert.deepEqual(visibleNoteCodes(row), [])
    // abweichender Inhalt ohne Anzahl: Anzahl aus den Beispielen
    assert.deepEqual(contentInfo(panel({ content_match: false, content_diff_sample: ['x. A'] })).params, { count: 1 })
})

test('visibleNoteCodes: Duplikate und Leerwerte entfallen, Reihenfolge bleibt', () => {
    assert.deepEqual(visibleNoteCodes({ kind: 'resolver', notes: ['resolver_cache', 'resolver_cache', '', null, 'ns_from_glue'] }), ['resolver_cache', 'ns_from_glue'])
    assert.deepEqual(visibleNoteCodes({}), [])
})

test('recordCell: none/empty/values mit match', () => {
    assert.deepEqual(recordCell({ record_values: null }), { kind: 'none' })
    assert.deepEqual(recordCell({}), { kind: 'none' })
    assert.deepEqual(recordCell({ record_values: [], record_match: false }), { kind: 'empty', match: false })
    assert.deepEqual(recordCell({ record_values: ['192.0.2.1', '192.0.2.2'], record_match: true }), { kind: 'values', values: ['192.0.2.1', '192.0.2.2'], match: true })
    assert.deepEqual(recordCell({ record_values: ['x'], record_match: null }), { kind: 'values', values: ['x'], match: null })
})

test('latencyText/sourceLabel', () => {
    assert.equal(latencyText(t, { latency_ms: 12 }), 'propagation.latencyMs|{"ms":12}')
    assert.equal(latencyText(t, { latency_ms: 0 }), 'propagation.latencyMs|{"ms":0}')
    assert.equal(latencyText(t, { latency_ms: null }), '')
    assert.equal(sourceLabel({ source: '1.1.1.1', label: 'Cloudflare' }), '1.1.1.1 (Cloudflare)')
    assert.equal(sourceLabel({ source: 'ns1' }), 'ns1')
    assert.equal(sourceLabel(null), '')
})

// ---------------------------------------------------------------------------------------------- Kopf/Hinweise
test('summaryInfo: in_sync gruen mit total, sonst Teil-Zusammenfassung', () => {
    assert.deepEqual(summaryInfo({ total: 7, ok: 7, mismatch: 0, failed: 0, skipped: 2, in_sync: true }),
        { key: 'propagation.summaryInSync', params: { total: 7, ok: 7, mismatch: 0, failed: 0 }, tone: 'ok' })
    const partial = summaryInfo({ total: 7, ok: 5, mismatch: 1, failed: 1, skipped: 0, in_sync: false })
    assert.equal(partial.key, 'propagation.summaryPartial')
    assert.equal(partial.tone, 'warn')
    assert.deepEqual(partial.params, { total: 7, ok: 5, mismatch: 1, failed: 1 })
    assert.equal(summaryInfo(null), null)
})

test('resultNotices: extern aus + ein Server, Zeitlimit, keine NS, keine Resolver, getrennte Backends', () => {
    const base = { sources: [panel({ is_reference: true })], nameservers: ['ns1.example.com.'], external: { enabled: false, authoritative: false, resolvers: [] } }
    assert.deepEqual(resultNotices(base).map((n) => n.id), ['externalDisabled', 'singleServer'])
    const two = { ...base, sources: [panel({ is_reference: true }), panel({ source: 'ns3' })] }
    assert.deepEqual(resultNotices(two).map((n) => n.id), ['externalDisabled'])
    const ext = { ...two, timed_out: true, nameservers: [], external: { enabled: true, authoritative: true, resolvers: [] }, separate_backends: true }
    const ids = resultNotices(ext).map((n) => n.id)
    assert.deepEqual(ids, ['timedOut', 'noNameservers', 'noResolvers', 'separateBackends'])
    assert.equal(resultNotices(ext)[0].tone, 'warn')
    // authoritative aus -> kein NS-Hinweis; Resolver vorhanden -> kein Resolver-Hinweis
    const ext2 = { ...two, nameservers: [], external: { enabled: true, authoritative: false, resolvers: ['1.1.1.1'] } }
    assert.deepEqual(resultNotices(ext2), [])
    assert.deepEqual(resultNotices(null), [])
})

// ---------------------------------------------------------------------------------------------- Formular
test('buildCheckParams/needsRecordType/canRunCheck: Name nur mit Typ, Inhalt als bool', () => {
    assert.deepEqual(buildCheckParams({ recName: '  www ', recType: 'A', compareContent: 1 }), { name: 'www', type: 'A', content: true })
    assert.deepEqual(buildCheckParams({ recName: '', recType: 'A' }), { name: undefined, type: undefined, content: false })
    assert.deepEqual(buildCheckParams({ recName: 'www', recType: '' }), { name: undefined, type: undefined, content: false })
    assert.deepEqual(buildCheckParams(), { name: undefined, type: undefined, content: false })
    assert.equal(needsRecordType('www', ''), true)
    assert.equal(needsRecordType('  ', ''), false)
    assert.equal(needsRecordType('@', 'MX'), false)
    assert.equal(canRunCheck({ loading: false, recName: 'www', recType: '' }), false)
    assert.equal(canRunCheck({ loading: true, recName: '', recType: '' }), false)
    assert.equal(canRunCheck({ loading: false, recName: '', recType: '' }), true)
})

// ---------------------------------------------------------------------------------------------- Monitoring
test('Resolver-Textarea <-> Liste', () => {
    assert.deepEqual(parseResolverText(' 1.1.1.1 \r\n\n 2606:4700:4700::1111\n  \n9.9.9.9'), ['1.1.1.1', '2606:4700:4700::1111', '9.9.9.9'])
    assert.deepEqual(parseResolverText(''), [])
    assert.deepEqual(parseResolverText(null), [])
    assert.equal(resolverText(['1.1.1.1', '8.8.8.8']), '1.1.1.1\n8.8.8.8')
    assert.equal(resolverText(null), '')
    assert.deepEqual(DEFAULT_RESOLVERS, ['1.1.1.1', '8.8.8.8', '9.9.9.9'])
})

test('scrapeTarget/buildScrapeYaml/buildCurlCommand: Schema, Port nur aus der URL, Platzhalter ohne Basis-URL', () => {
    assert.deepEqual(scrapeTarget('https://dns.example.com/metrics'),
        { scheme: 'https', target: 'dns.example.com', path: '/metrics', url: 'https://dns.example.com/metrics', placeholder: false })
    assert.equal(scrapeTarget('http://10.0.0.5:8080/metrics').target, '10.0.0.5:8080')
    assert.equal(scrapeTarget('http://10.0.0.5:8080/metrics').scheme, 'http')
    assert.equal(scrapeTarget(null).placeholder, true)
    assert.equal(scrapeTarget('ftp://x/metrics').placeholder, true)
    assert.equal(scrapeTarget('kaputt').placeholder, true)

    const yaml = buildScrapeYaml('https://dns.example.com/metrics')
    assert.equal(yaml, [
        'scrape_configs:',
        '  - job_name: pdns-manager',
        '    scheme: https',
        '    metrics_path: /metrics',
        '    authorization:',
        '      type: Bearer',
        '      credentials_file: /etc/prometheus/pdns-manager.token',
        '    static_configs:',
        "      - targets: ['dns.example.com']",
    ].join('\n'))
    assert.match(buildScrapeYaml(null), new RegExp(`targets: \\['${SCRAPE_PLACEHOLDER_HOST}'\\]`))
    assert.match(buildScrapeYaml('http://h:9000/metrics'), /scheme: http\n/)
    assert.equal(buildCurlCommand('https://dns.example.com/metrics'),
        'curl -H "Authorization: Bearer $(cat pdns-manager.token)" https://dns.example.com/metrics | head')
    assert.match(buildCurlCommand(null), /https:\/\/<dein-host>\/metrics \| head$/)
    // Token erscheint nie in den Beispielen
    assert.doesNotMatch(yaml + buildCurlCommand(null), /dnsmgr_metrics_/)
    assert.equal(GRAFANA_QUERIES.split('\n').length, 5)
})

test('monitoringStatusRows: alles ok', () => {
    const rows = monitoringStatusRows({
        ok: true, version: '3.0.0', migration_errors: 0, servers_not_loaded: {},
        background: { enabled: true, tasks: { webhook_worker: { running: true }, audit_retention: { running: true } } },
        secrets: { mode: 'encrypted', unreadable_values: 0, runtime_unreadable: 0 },
    })
    assert.deepEqual(rows.map((r) => [r.id, r.key, r.tone]), [
        ['background', 'settings.monitoring.statusBackgroundOk', 'ok'],
        ['migrations', 'settings.monitoring.statusMigrationsOk', 'ok'],
        ['servers', 'settings.monitoring.statusServersOk', 'ok'],
        ['secrets', 'settings.monitoring.secretsModeEncrypted', 'ok'],
    ])
    assert.deepEqual(rows[0].params, { n: 2 })
    assert.deepEqual(monitoringStatusRows(null), [])
})

test('monitoringStatusRows: Probleme (Worker gestoppt, Migrationen, Server, Klartext, unlesbar)', () => {
    const rows = monitoringStatusRows({
        ok: false, migration_errors: 2, servers_not_loaded: { ns9: 'api key unreadable', ns3: 'api key empty' },
        background: { enabled: true, tasks: { webhook_worker: { running: false }, audit_retention: { running: true } } },
        secrets: { mode: 'plaintext_fallback', unreadable_values: 1, runtime_unreadable: 2 },
    })
    const byId = Object.fromEntries(rows.map((r) => [r.id, r]))
    assert.equal(byId.background.key, 'settings.monitoring.statusBackgroundStopped')
    assert.deepEqual(byId.background.params, { list: 'webhook_worker' })
    assert.equal(byId.background.tone, 'error')
    assert.deepEqual([byId.migrations.key, byId.migrations.params, byId.migrations.tone], ['settings.monitoring.statusMigrationErrors', { n: 2 }, 'error'])
    assert.deepEqual(byId.servers.params, { list: 'ns3, ns9' })
    assert.deepEqual(byId.servers.detail, [{ text: 'ns3: api key empty' }, { text: 'ns9: api key unreadable' }])
    assert.equal(byId.secrets.key, 'settings.monitoring.secretsModePlaintext')
    assert.equal(byId.secrets.tone, 'warn')
    assert.deepEqual(byId.secrets.detail, [{ key: 'settings.monitoring.statusSecretsUnreadable', params: { n: 3 } }])

    const off = monitoringStatusRows({ background: { enabled: false, tasks: {} }, secrets: { mode: 'encrypted', runtime_unreadable: 1 } })
    assert.equal(off[0].key, 'settings.monitoring.statusBackgroundDisabled')
    assert.equal(off[0].tone, 'info')
    assert.equal(off.find((r) => r.id === 'secrets').tone, 'error')
    const unknown = monitoringStatusRows({ secrets: { mode: 'weird' } }).find((r) => r.id === 'secrets')
    assert.equal(unknown.key, 'settings.monitoring.secretsModeUnknown')
    assert.deepEqual(unknown.params, { mode: 'weird' })
})

test('backgroundTasks: sortiert, Felder normalisiert', () => {
    assert.deepEqual(backgroundTasks({ background: { tasks: { z: { running: 1, last_run_at: 'x' }, a: {} } } }), [
        { name: 'a', running: false, last_run_at: null, last_error_at: null },
        { name: 'z', running: true, last_run_at: 'x', last_error_at: null },
    ])
    assert.deepEqual(backgroundTasks({}), [])
})

// ---------------------------------------------------------------------------------------------- i18n-Abdeckung
test('Alle statisch genutzten propagation.*/settings.monitoring.*-Keys und alle Notiz-/Fehlercodes haben Texte in 6 Sprachen', () => {
    const LANGS = ['de', 'en', 'bs', 'hr', 'hu', 'sr']
    const fragDir = path.join(SRC, 'locales', 'fragments')
    const files = [
        path.join(SRC, 'zoneDetail', 'propagationModel.js'),
        path.join(SRC, 'zoneDetail', 'tabs', '30-propagation.tab.jsx'),
        path.join(SRC, 'components', 'settings', 'tabs', 'monitoring.tab.jsx'),
    ]
    const used = new Set()
    for (const f of files) {
        for (const m of fs.readFileSync(f, 'utf-8').matchAll(/'((?:propagation|settings\.monitoring|zoneDetail)\.[A-Za-z0-9_.]+)'/g)) {
            if (!m[1].endsWith('.') && !m[1].endsWith('_')) used.add(m[1])
        }
    }
    // Codes laut Backend (services/propagation.py: ERROR_TEXTS, NOTE_CODES)
    const errorCodes = ['timeout', 'deadline', 'unreachable', 'zone_missing', 'not_loaded', 'api_error', 'refused', 'servfail',
        'nxdomain', 'not_authoritative', 'no_soa', 'bad_response', 'network_error', 'ns_unresolvable', 'ipv6_disabled', 'address_not_allowed']
    const noteCodes = ['notify_pending', 'separate_backend', 'content_same_serial_differs', 'resolver_cache', 'secondary_lagging',
        'lua_not_comparable', 'record_query_failed', 'truncated_targets', 'ns_from_glue']
    for (const c of errorCodes) used.add(`propagation.err_${c}`)
    for (const c of noteCodes) used.add(`propagation.note_${c}`)
    used.add('settings.monitoring.tab')

    const flat = (obj, prefix = '', out = new Map()) => {
        for (const [k, v] of Object.entries(obj)) {
            const full = prefix ? `${prefix}.${k}` : k
            if (v && typeof v === 'object') flat(v, full, out)
            else out.set(full, v)
        }
        return out
    }
    for (const lang of LANGS) {
        const known = flat(JSON.parse(fs.readFileSync(path.join(SRC, 'locales', `${lang}.json`), 'utf-8')))
        for (const f of fs.readdirSync(fragDir).filter((n) => n.endsWith(`.${lang}.json`))) {
            for (const [k, v] of Object.entries(JSON.parse(fs.readFileSync(path.join(fragDir, f), 'utf-8')).set || {})) known.set(k, v)
        }
        const missing = [...used].filter((k) => typeof known.get(k) !== 'string' || !known.get(k).trim())
        assert.deepEqual(missing, [], `${lang}: fehlende Keys`)
    }
})
