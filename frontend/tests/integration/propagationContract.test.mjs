// Integrationstest WS-F12F13-FE <-> WS-F12F13-BE (Plan Regel 3: braucht Code des parallelen Workstreams,
// zaehlt nicht zum Fertig-Kriterium von WS-F12F13-FE, laeuft verpflichtend im Integrationsschritt F.2).
// Prueft den API-Vertrag statisch gegen die Backend-Quellen: Fehler-/Notiz-Codes haben Uebersetzungen,
// Status-/Art-Literale passen zum Modell, die Felder, die die Oberflaeche liest, existieren in den Schemas.
// Solange backend/app/schemas/propagation.py fehlt (Branch von WS-F12F13-BE noch nicht gemergt), werden die
// Tests mit Begruendung uebersprungen - sichtbar in der Ausgabe, kein stilles Bestehen.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { KIND_ORDER, statusLabelKey } from '../../src/zoneDetail/propagationModel.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FE = path.resolve(HERE, '..', '..')
const BE = path.resolve(FE, '..', 'backend')
const SCHEMA = path.join(BE, 'app', 'schemas', 'propagation.py')
const SERVICE = path.join(BE, 'app', 'services', 'propagation.py')
const LANGS = ['de', 'en', 'bs', 'hr', 'hu', 'sr']
const SKIP = fs.existsSync(SCHEMA) && fs.existsSync(SERVICE)
    ? false
    : 'Backend WS-F12F13-BE (schemas/services propagation.py) noch nicht im Stand - Integrationsschritt F.2'

function knownKeys(lang) {
    const keys = new Map()
    const walk = (obj, prefix) => {
        for (const [k, v] of Object.entries(obj)) {
            const full = prefix ? `${prefix}.${k}` : k
            if (v && typeof v === 'object') walk(v, full)
            else keys.set(full, v)
        }
    }
    walk(JSON.parse(fs.readFileSync(path.join(FE, 'src', 'locales', `${lang}.json`), 'utf-8')), '')
    const fragDir = path.join(FE, 'src', 'locales', 'fragments')
    if (fs.existsSync(fragDir)) {
        for (const f of fs.readdirSync(fragDir).filter((n) => n.endsWith(`.${lang}.json`))) {
            for (const [k, v] of Object.entries(JSON.parse(fs.readFileSync(path.join(fragDir, f), 'utf-8')).set || {})) keys.set(k, v)
        }
    }
    return keys
}

// Klassenrumpf (Python) -> Feldnamen
function classFields(src, name) {
    const m = new RegExp(`^class ${name}\\(BaseModel\\):\\n((?:(?:    .*)?\\n)+)`, 'm').exec(src)
    assert.ok(m, `Schema-Klasse ${name} fehlt`)
    return new Set([...m[1].matchAll(/^ {4}(\w+)\s*:/gm)].map((x) => x[1]))
}

function literalValues(src, alias) {
    const m = new RegExp(`^${alias}\\s*=\\s*Literal\\[([^\\]]+)\\]`, 'm').exec(src)
    assert.ok(m, `Literal ${alias} fehlt`)
    return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1])
}

test('Fehler- und Notiz-Codes des Backends sind in allen Sprachen uebersetzt', { skip: SKIP }, () => {
    const svc = fs.readFileSync(SERVICE, 'utf-8')
    const errBlock = /^ERROR_TEXTS\s*=\s*\{([\s\S]*?)^\}/m.exec(svc)
    assert.ok(errBlock, 'ERROR_TEXTS fehlt')
    const errorCodes = [...errBlock[1].matchAll(/^\s*"(\w+)"\s*:/gm)].map((x) => x[1])
    const noteBlock = /^NOTE_CODES\s*=\s*\(([\s\S]*?)\)/m.exec(svc)
    assert.ok(noteBlock, 'NOTE_CODES fehlt')
    const noteCodes = [...noteBlock[1].matchAll(/"(\w+)"/g)].map((x) => x[1])
    assert.ok(errorCodes.length >= 15 && noteCodes.length >= 9)
    for (const lang of LANGS) {
        const keys = knownKeys(lang)
        const missing = [
            ...errorCodes.map((c) => `propagation.err_${c}`),
            ...noteCodes.map((c) => `propagation.note_${c}`),
        ].filter((k) => !keys.has(k))
        assert.deepEqual(missing, [], `${lang}: fehlende Uebersetzungen`)
    }
})

test('Status- und Art-Literale des Schemas passen zum Frontend-Modell', { skip: SKIP }, () => {
    const schema = fs.readFileSync(SCHEMA, 'utf-8')
    assert.deepEqual(literalValues(schema, 'SourceKind'), KIND_ORDER)
    for (const status of literalValues(schema, 'SourceStatus')) {
        assert.equal(statusLabelKey(status), `propagation.status${status[0].toUpperCase()}${status.slice(1)}`)
    }
    assert.deepEqual(literalValues(schema, 'SerialRelation').sort(), ['ahead', 'behind', 'equal'])
})

test('Felder, die die Oberflaeche liest, existieren in den Antwort-Schemas', { skip: SKIP }, () => {
    const schema = fs.readFileSync(SCHEMA, 'utf-8')
    const need = {
        PropagationResponse: ['server', 'checked_at', 'cached', 'timed_out', 'expected_serial', 'separate_backends', 'nameservers',
            'record', 'external', 'comparable_types', 'sources', 'summary'],
        PropagationSource: ['source', 'kind', 'target', 'label', 'is_reference', 'status', 'serial', 'zone_kind', 'match',
            'serial_relation', 'record_values', 'record_match', 'content_match', 'content_diff_count', 'content_diff_sample', 'ttl',
            'latency_ms', 'error_code', 'error', 'notes'],
        PropagationSummary: ['total', 'ok', 'mismatch', 'failed', 'in_sync'],
        PropagationExternal: ['enabled', 'authoritative', 'resolvers'],
        PropagationSettingsOut: ['enabled', 'check_authoritative', 'ipv6', 'resolvers', 'default_resolvers'],
        PropagationSettingsIn: ['enabled', 'check_authoritative', 'ipv6', 'resolvers'],
        MetricsSettingsOut: ['enabled', 'effective_enabled', 'env_override', 'token_set', 'token_unreadable', 'token_hint',
            'token_created_at', 'pdns_probe', 'endpoint_path', 'scrape_url'],
        MetricsSettingsIn: ['enabled', 'pdns_probe'],
        MetricsTokenCreated: ['token'],
        MonitoringStatus: ['checked_at', 'ok', 'version', 'background', 'migration_errors', 'servers_not_loaded', 'secrets'],
        BackgroundStatus: ['enabled', 'tasks'],
        BackgroundTaskStatus: ['running', 'last_run_at', 'last_error_at'],
        SecretsShortStatus: ['mode', 'unreadable_values', 'runtime_unreadable'],
    }
    for (const [cls, fields] of Object.entries(need)) {
        const have = classFields(schema, cls)
        const missing = fields.filter((f) => !have.has(f))
        assert.deepEqual(missing, [], `${cls}: Felder fehlen`)
    }
})
