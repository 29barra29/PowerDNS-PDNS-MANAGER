// Vertragspruefung der Slots aus W0-INT-FE2 (Plan B.14): Slot-Registry, Erweiterungs-Vertrag des Record-Dialogs,
// Slot-Dateien der Zonenansicht/Benutzerseite und Audit-Aktionen-Katalog. Nur reine Module - ohne React/JSX.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { collectSlots, mergeListSlots, parseSlotFile, slotApplies } from '../src/lib/slots.js'
import {
    CORE_BODY_KEYS, EXT_POSITIONS, activeExtensions, collectExtensions, extensionsAt, initialExtStates,
    mergeRequestBody, normalizeExtensions, notifyExtensions, validateExtensions,
} from '../src/zoneDetail/formExtensions.js'
import {
    AUDIT_ACTION_GROUPS, HISTORY_GROUPS, auditActionLabel, buildAuditCatalog, resourceTypeLabel,
} from '../src/lib/auditCatalog.js'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SRC = path.join(ROOT, 'src')
const LANGS = ['de', 'en', 'bs', 'hr', 'hu', 'sr']

function silent() {
    const problems = []
    return { problems, onProblem: (message) => problems.push(message) }
}

const Dummy = () => null

// ------------------------------------------------------------------ lib/slots
test('parseSlotFile: Praefix, Name, Art', () => {
    assert.deepEqual(parseSlotFile('./tabs/10-records.tab.jsx'), { file: '10-records.tab.jsx', prefix: 10, name: 'records', kind: 'tab' })
    assert.deepEqual(parseSlotFile('./form-extensions/ptr.ext.jsx'), { file: 'ptr.ext.jsx', prefix: null, name: 'ptr', kind: 'ext' })
    assert.equal(parseSlotFile('./auditActions/w0-secrets.actions.js').name, 'w0-secrets')
})

test('collectSlots: Sortierung nach order bzw. NN, id-Default, kaputte Slots werden uebersprungen', () => {
    const { problems, onProblem } = silent()
    const modules = {
        './tabs/30-propagation.tab.jsx': { tab: { labelKey: 'x' }, default: Dummy },
        './tabs/10-records.tab.jsx': { tab: { id: 'records', labelKey: 'zoneDetail.tabRecords' }, default: Dummy },
        './tabs/20-history.tab.jsx': { tab: { id: 'history', order: 5 }, default: Dummy },
        './tabs/40-broken.tab.jsx': { default: Dummy },                       // ohne Metadaten
        './tabs/50-nocomp.tab.jsx': { tab: { id: 'nocomp' } },                 // ohne Komponente
        './tabs/60-dup.tab.jsx': { tab: { id: 'records' }, default: Dummy },   // doppelte id
        './tabs/70-memo.tab.jsx': { tab: {}, default: { $$typeof: Symbol.for('react.memo') } },
    }
    const entries = collectSlots(modules, { exportName: 'tab', onProblem })
    assert.deepEqual(entries.map((e) => [e.id, e.order]), [['history', 5], ['records', 10], ['propagation', 30], ['memo', 70]])
    assert.equal(entries[1].labelKey, 'zoneDetail.tabRecords')
    assert.equal(entries[1].file, '10-records.tab.jsx')
    assert.equal(entries[1].Component, Dummy)
    assert.equal(problems.length, 3)
    assert.match(problems.join('\n'), /40-broken.*fehlt/)
    assert.match(problems.join('\n'), /50-nocomp.*keine Komponente/)
    assert.match(problems.join('\n'), /60-dup.*bereits belegt/)
})

test('collectSlots: requireComponent=false erlaubt Slots ohne Oberflaeche; exportName ist Pflicht', () => {
    const entries = collectSlots({ './form-extensions/x.ext.jsx': { ext: { when: () => true } } }, { exportName: 'ext', requireComponent: false })
    assert.equal(entries.length, 1)
    assert.equal(entries[0].Component, null)
    assert.throws(() => collectSlots({}, {}), TypeError)
    assert.deepEqual(collectSlots(undefined, { exportName: 'tab' }), [])
})

test('slotApplies: fehlendes when = sichtbar, Ausnahme = ausgeblendet', () => {
    const { problems, onProblem } = silent()
    assert.equal(slotApplies({}, []), true)
    assert.equal(slotApplies({ when: (r) => r.type === 'A' }, [{ type: 'A' }]), true)
    assert.equal(slotApplies({ when: (r) => r.type === 'A' }, [{ type: 'MX' }]), false)
    assert.equal(slotApplies({ id: 'x', when: () => { throw new Error('kaputt') } }, [], onProblem), false)
    assert.equal(problems.length, 1)
})

test('mergeListSlots: Reihenfolge first/alphabetisch, Duplikate, Konflikte', () => {
    const { problems, onProblem } = silent()
    const modules = {
        './a/zz.actions.js': { default: [{ action: 'B', group: 'x' }, { action: 'C', group: 'y' }] },
        './a/base.actions.js': { default: [{ action: 'A', group: 'x' }, { action: 'B', group: 'x' }] },
        './a/aa.actions.js': { default: [{ action: 'C', group: 'z' }], other: 1 },
        './a/bad.actions.js': { default: 'kein Array' },
    }
    const out = mergeListSlots(modules, { key: (i) => i.action, first: ['base'], onProblem })
    assert.deepEqual(out.map((e) => `${e.item.action}:${e.item.group}:${e.file}`), [
        'A:x:base.actions.js', 'B:x:base.actions.js', 'C:z:aa.actions.js',
    ])
    // B identisch -> still; C abweichend -> Konflikt; bad -> keine Liste
    assert.equal(problems.length, 2)
    assert.match(problems.join('\n'), /"C" ist bereits in aa\.actions\.js anders definiert/)
    assert.match(problems.join('\n'), /bad\.actions\.js.*keine Liste/)
})

// ------------------------------------------------------------------ Erweiterungs-Vertrag (form-extensions)

// Beispiel-Erweiterung aus zoneDetail/form-extensions/README.md (ohne Komponente)
function exampleExtension(log) {
    return {
        id: 'note',
        when: (type) => type === 'TXT',
        position: 'afterValues',
        initialState: ({ mode, record, type, zone }) => {
            log.push(['initialState', mode, record?.name ?? null, type, zone.name])
            return { enabled: mode !== 'edit', note: '' }
        },
        validate: (state) => {
            log.push(['validate', state.note])
            return state.enabled && state.note.length > 200 ? 'example.noteTooLong' : null
        },
        collect: (state, form) => {
            log.push(['collect', form.fqdn])
            return state.enabled ? { note: state.note.trim() } : null
        },
        onResult: (res, { state, form, ctx }) => {
            log.push(['onResult', res.details.note, state.note, form.type])
            if (res?.details?.note === 'stored') ctx.setSuccess((prev) => `${prev} (Notiz gespeichert)`)
        },
    }
}

test('Beispiel-Erweiterung durchlaeuft initialState -> validate -> collect -> onResult', () => {
    const log = []
    const exts = normalizeExtensions([{ ...exampleExtension(log), file: 'note.ext.jsx', Component: Dummy }])
    assert.equal(exts.length, 1)
    const zone = { id: 'example.com.', name: 'example.com', key: 'example.com.', meta: null }

    // 1. Oeffnen (Anlegen)
    const states = initialExtStates(exts, { record: null, mode: 'add', type: 'A', zone })
    assert.deepEqual(states, { note: { enabled: true, note: '' } })
    // Typ A: nicht aktiv, nicht gerendert, kein validate/collect
    assert.deepEqual(activeExtensions(exts, 'A'), [])
    assert.deepEqual(extensionsAt(exts, 'TXT', 'afterValues').map((e) => e.id), ['note'])
    assert.deepEqual(extensionsAt(exts, 'TXT', 'footer'), [])

    // 2. Nutzer waehlt TXT und gibt eine Notiz ein (setExtState)
    states.note = { ...states.note, note: '  hallo  ' }
    const form = { mode: 'add', isEdit: false, type: 'TXT', name: 'www', fqdn: 'www.example.com.', ttl: '3600', fieldsList: [{ text: 'v=1' }], contents: ['"v=1"'], oldContent: '' }

    // 3. Absenden: validate -> collect -> Merge in den Request-Body
    assert.equal(validateExtensions(exts, states, form), null)
    const { body, ignored } = collectExtensions(exts, states, form)
    assert.deepEqual(body, { note: 'hallo' })
    assert.deepEqual(ignored, [])
    const request = mergeRequestBody({ name: form.fqdn, type: 'TXT', ttl: 3600, records: [{ content: '"v=1"', disabled: false }] }, body)
    assert.deepEqual(request, { note: 'hallo', name: 'www.example.com.', type: 'TXT', ttl: 3600, records: [{ content: '"v=1"', disabled: false }] })

    // 4. Antwort: onResult haengt an die Erfolgsmeldung an (Updater-Form)
    let success = 'TXT-Record www.example.com angelegt'
    const ctx = { setSuccess: (v) => { success = typeof v === 'function' ? v(success) : v } }
    const errors = notifyExtensions(exts, states, { details: { note: 'stored', 'ns1': 'saved' } }, form, ctx)
    assert.deepEqual(errors, [])
    assert.equal(success, 'TXT-Record www.example.com angelegt (Notiz gespeichert)')

    assert.deepEqual(log.map((e) => e[0]), ['initialState', 'validate', 'collect', 'onResult'])
    assert.deepEqual(log[0], ['initialState', 'add', null, 'A', 'example.com'])
})

test('Erweiterung: Bearbeiten-Modus, Validierungsfehler als Key bzw. {key, values}, Ausnahme als Text', () => {
    const log = []
    const ext = exampleExtension(log)
    const exts = normalizeExtensions([{ ...ext, file: 'note.ext.jsx' }])
    const zone = { id: 'z.', name: 'z', key: 'z.', meta: {} }
    const states = initialExtStates(exts, { record: { name: 'a.z.', type: 'TXT' }, mode: 'edit', type: 'TXT', zone })
    assert.deepEqual(states.note, { enabled: false, note: '' })
    const form = { type: 'TXT', fqdn: 'a.z.' }
    // deaktiviert -> collect liefert null -> kein Feld
    assert.deepEqual(collectExtensions(exts, states, form).body, {})

    const long = { note: { enabled: true, note: 'x'.repeat(201) } }
    assert.deepEqual(validateExtensions(exts, long, form), { id: 'note', key: 'example.noteTooLong', values: {}, text: null })

    const withValues = normalizeExtensions([{ ...ext, file: 'v.ext.jsx', validate: () => ({ key: 'example.max', values: { max: 3 } }) }])
    assert.deepEqual(validateExtensions(withValues, states, form), { id: 'note', key: 'example.max', values: { max: 3 }, text: null })

    const throwing = normalizeExtensions([{ ...ext, file: 't.ext.jsx', validate: () => { throw new Error('IP ungueltig') } }])
    assert.deepEqual(validateExtensions(throwing, states, form), { id: 'note', key: null, values: {}, text: 'IP ungueltig' })
})

test('Erweiterung: Kernfelder sind geschuetzt, collect-Fehler tragen die id, onResult-Fehler isoliert', () => {
    const { problems, onProblem } = silent()
    const greedy = {
        id: 'greedy', file: 'greedy.ext.jsx', when: () => true, initialState: () => ({}),
        collect: () => ({ name: 'evil.', ttl: 1, records: [], manage_ptr: true }),
        onResult: () => { throw new Error('boom') },
    }
    const polite = {
        id: 'polite', file: 'polite.ext.jsx', when: () => true, initialState: () => ({ n: 0 }),
        collect: () => ({ extra: 1 }),
        onResult: (res, { state }) => { state.n += 1 },
    }
    const exts = normalizeExtensions([greedy, polite], { onProblem })
    const states = initialExtStates(exts, { record: null, mode: 'add', type: 'A', zone: {} }, { onProblem })
    const { body, ignored } = collectExtensions(exts, states, { type: 'A' }, { onProblem })
    assert.deepEqual(body, { manage_ptr: true, extra: 1 })
    assert.deepEqual(ignored.map((i) => i.key).sort(), ['name', 'records', 'ttl'])
    for (const key of ['name', 'type', 'ttl', 'records', 'old_content', 'new_content', 'disabled']) assert.ok(CORE_BODY_KEYS.includes(key))
    assert.deepEqual(mergeRequestBody({ name: 'ok.' }, { name: 'evil.', x: 1 }), { name: 'ok.', x: 1 })

    const errors = notifyExtensions(exts, states, {}, { type: 'A' }, {}, { onProblem })
    assert.equal(errors.length, 1)
    assert.equal(errors[0].id, 'greedy')
    assert.equal(states.polite.n, 1)

    const failing = normalizeExtensions([{ ...polite, collect: () => { throw new Error('nicht bereit') } }])
    assert.throws(() => collectExtensions(failing, states, { type: 'A' }), (err) => err.extId === 'polite' && err.message === 'nicht bereit')
})

test('normalizeExtensions: Pflichtfelder, Position, initialState-Fehler', () => {
    const { problems, onProblem } = silent()
    const exts = normalizeExtensions([
        { id: 'nowhen', file: 'a.ext.jsx', initialState: () => 1, collect: () => null },
        { id: 'nocollect', file: 'b.ext.jsx', when: () => true, initialState: () => 1 },
        { id: 'pos', file: 'c.ext.jsx', when: () => true, initialState: () => { throw new Error('x') }, collect: () => null, position: 'oben' },
        { id: 'ttl', file: 'd.ext.jsx', when: () => true, initialState: () => 1, collect: () => null, position: 'beforeTtl' },
    ], { onProblem })
    assert.deepEqual(exts.map((e) => [e.id, e.position]), [['pos', 'afterValues'], ['ttl', 'beforeTtl']])
    assert.equal(problems.length, 3)
    assert.deepEqual(initialExtStates(exts, {}, { onProblem }), { pos: null, ttl: 1 })
    assert.deepEqual([...EXT_POSITIONS], ['afterValues', 'beforeTtl', 'footer'])
    // when() wirft -> inaktiv
    const bad = normalizeExtensions([{ id: 'w', when: () => { throw new Error('x') }, initialState: () => 1, collect: () => null }])
    assert.deepEqual(activeExtensions(bad, 'A', { onProblem }), [])
})

// ------------------------------------------------------------------ Slot-Dateien im Repo

const SLOT_DIRS = [
    { dir: 'zoneDetail/tabs', suffix: '.tab.jsx', exportName: 'tab', numbered: true },
    { dir: 'zoneDetail/header-actions', suffix: '.action.jsx', exportName: 'action', numbered: true },
    { dir: 'zoneDetail/row-actions', suffix: '.action.jsx', exportName: 'action', numbered: true },
    { dir: 'zoneDetail/form-extensions', suffix: '.ext.jsx', exportName: 'ext', numbered: false },
    { dir: 'zoneDetail/value-renderers', suffix: '.renderer.jsx', exportName: 'renderer', numbered: false },
    { dir: 'components/users/badges', suffix: '.badge.jsx', exportName: 'badge', numbered: true },
    { dir: 'components/userSecurity/sections', suffix: '.section.jsx', exportName: 'section', numbered: true },
]

test('Slot-Dateien: Dateinamen, Metadaten-Export, eindeutige ids', () => {
    for (const { dir, suffix, exportName, numbered } of SLOT_DIRS) {
        const abs = path.join(SRC, dir)
        if (!fs.existsSync(abs)) continue
        const ids = new Set()
        for (const name of fs.readdirSync(abs)) {
            if (name === 'README.md') continue
            assert.ok(name.endsWith(suffix), `${dir}/${name}: Endung ${suffix} erwartet (sonst findet der Glob die Datei nicht)`)
            if (numbered) assert.match(name, /^\d{2}-[a-z0-9-]+\./, `${dir}/${name}: Schema NN-<name>${suffix}`)
            const text = fs.readFileSync(path.join(abs, name), 'utf-8')
            assert.match(text, new RegExp(`export const ${exportName}\\s*=\\s*\\{`), `${dir}/${name}: export const ${exportName} = { ... } fehlt`)
            if (exportName !== 'ext') assert.match(text, /export default function /, `${dir}/${name}: Default-Komponente fehlt`)
            const m = new RegExp(`export const ${exportName}\\s*=\\s*\\{[^}]*\\bid:\\s*'([^']+)'`).exec(text)
            const id = m ? m[1] : parseSlotFile(name).name
            if (m) assert.equal(id, parseSlotFile(name).name, `${dir}/${name}: id "${id}" sollte dem Dateinamen entsprechen`)
            assert.ok(!ids.has(id), `${dir}: id ${id} doppelt`)
            ids.add(id)
        }
    }
})

test('Zonenansicht W0: Records-Tab und Kopf-Aktionen sind belegt', () => {
    const list = (dir) => fs.readdirSync(path.join(SRC, dir)).filter((n) => n !== 'README.md').sort()
    assert.deepEqual(list('zoneDetail/tabs'), ['10-records.tab.jsx'])
    assert.deepEqual(list('zoneDetail/header-actions'), ['10-dnssec-ds.action.jsx', '90-add-record.action.jsx'])
    const records = fs.readFileSync(path.join(SRC, 'zoneDetail/tabs/10-records.tab.jsx'), 'utf-8')
    assert.match(records, /labelKey:\s*'zoneDetail\.tabRecords'/)
    for (const readme of ['zoneDetail/row-actions/README.md', 'zoneDetail/form-extensions/README.md']) {
        assert.ok(fs.existsSync(path.join(SRC, readme)), `${readme} fehlt`)
    }
})

// ------------------------------------------------------------------ Audit-Aktionen

async function loadAuditModules() {
    const dir = path.join(SRC, 'constants/auditActions')
    const modules = {}
    for (const name of fs.readdirSync(dir).filter((n) => n.endsWith('.actions.js')).sort()) {
        modules[`./auditActions/${name}`] = await import(pathToFileURL(path.join(dir, name)).href)
    }
    return modules
}

// F7 §6.6 (Basis) - Key = Action-String
const F7_BASE_ACTIONS = [
    'CREATE', 'UPDATE', 'DELETE', 'IMPORT', 'BULK_UPDATE', 'RECORD_ROLLBACK', 'ZONE_NOTIFY', 'DYNDNS_UPDATE',
    'DNSSEC_ENABLE', 'DNSSEC_DISABLE', 'KEY_ACTIVATE', 'KEY_DEACTIVATE', 'KEY_DELETE', 'ACME_PRESENT', 'ACME_CLEANUP',
    'ACME_TOKEN_CREATE', 'ACME_TOKEN_DELETE', 'SERVER_CREATE', 'SERVER_UPDATE', 'SERVER_DELETE', 'REVEAL_API_KEY',
    'SMTP_UPDATE', 'LOGIN', 'LOGIN_FAILED', 'PASSWORD_CHANGE', 'USER_CREATE', 'USER_UPDATE', 'USER_DELETE',
    'USER_PASSWORD_RESET', 'ZONE_ACCESS_UPDATE', 'TOTP_ENABLE', 'TOTP_DISABLE', 'PASSKEY_ADD', 'PASSKEY_DELETE',
    'PANEL_TOKEN_CREATE', 'PANEL_TOKEN_DELETE', 'AUDIT_PURGE', 'AUDIT_SETTINGS_UPDATE',
]
const F7_RESOURCE_TYPES = ['zone', 'record', 'dnssec_key', 'user', 'server_config', 'settings', 'acme', 'acme_token', 'audit_log']

test('Audit-Katalog: alle Slot-Dateien ohne Konflikte, Basis vollstaendig, Gruppen gueltig', async () => {
    const { problems, onProblem } = silent()
    const modules = await loadAuditModules()
    assert.ok(modules['./auditActions/base.actions.js'], 'base.actions.js fehlt')
    const catalog = buildAuditCatalog(modules, { onProblem })
    assert.deepEqual(problems, [])

    const names = catalog.actions.map((a) => a.action)
    assert.deepEqual(names.slice(0, F7_BASE_ACTIONS.length).sort(), [...F7_BASE_ACTIONS].sort(), 'Basis steht vorne und ist vollstaendig')
    assert.equal(new Set(names).size, names.length)
    for (const a of catalog.actions) assert.ok(AUDIT_ACTION_GROUPS.includes(a.group), `${a.action}: Gruppe ${a.group}`)
    // Slot aus Welle 0a (W0-SECRETS) ist aggregiert
    for (const a of ['SECRETS_KEY_GENERATED', 'SECRETS_MIGRATE', 'SECRETS_ROTATE']) assert.equal(catalog.actionMap[a]?.group, 'system')

    for (const rt of F7_RESOURCE_TYPES) assert.ok(catalog.resourceTypes.includes(rt), `resource_type ${rt}`)

    // Zonenverlauf: zonenbezogene Gruppen, ACME-Token-Verwaltung und System nicht
    assert.ok(catalog.historyActions.includes('CREATE'))
    assert.ok(catalog.historyActions.includes('KEY_DELETE'))
    assert.ok(catalog.historyActions.includes('ACME_PRESENT'))
    assert.ok(!catalog.historyActions.includes('ACME_TOKEN_CREATE'))
    assert.ok(!catalog.historyActions.includes('LOGIN'))
    assert.ok(!catalog.historyActions.includes('SECRETS_MIGRATE'))
    for (const a of catalog.historyActions) {
        const entry = catalog.actionMap[a]
        assert.ok(entry.history === true)
        assert.ok(HISTORY_GROUPS.includes(entry.group) || entry.history === true)
    }
})

test('Audit-Katalog: Validierung und history-Flag einer Slot-Datei', () => {
    const { problems, onProblem } = silent()
    const catalog = buildAuditCatalog({
        './auditActions/base.actions.js': { default: [{ action: 'CREATE', group: 'records' }], resourceTypes: ['zone'] },
        './auditActions/f6.actions.js': {
            default: [{ action: 'WEBHOOK_CREATE', group: 'settings' }, { action: 'webhook_bad', group: 'settings' },
                { action: 'ZONE_EXPORT', group: 'zone', history: false }, { action: 'X_ODD', group: 'nope' }],
            resourceTypes: ['webhook', 'Bad Type'],
        },
    }, { onProblem })
    assert.deepEqual(catalog.actions.map((a) => `${a.action}:${a.group}:${a.history}`), [
        'CREATE:records:true', 'WEBHOOK_CREATE:settings:false', 'ZONE_EXPORT:zone:false', 'X_ODD:system:false',
    ])
    assert.deepEqual([...catalog.resourceTypes], ['zone', 'webhook'])
    assert.equal(problems.length, 3)
})

test('Audit-Labels: Fallback auf den Rohstring, Keys fuer alle Basis-Aktionen in allen Sprachen', () => {
    const t = (key, opts) => (key === 'audit.actions.CREATE' ? 'Erstellt' : opts.defaultValue)
    assert.equal(auditActionLabel(t, 'CREATE'), 'Erstellt')
    assert.equal(auditActionLabel(t, 'UNKNOWN_X'), 'UNKNOWN_X')
    assert.equal(auditActionLabel(t, ''), '')
    assert.equal(resourceTypeLabel(t, 'webhook'), 'webhook')

    // Labels stehen im Fragment w0-fe2 (vor dem Wellenende) bzw. nach dem Merge in locales/<lang>.json
    for (const lang of LANGS) {
        const fragFile = path.join(SRC, 'locales/fragments', `w0-fe2.${lang}.json`)
        const set = fs.existsSync(fragFile) ? JSON.parse(fs.readFileSync(fragFile, 'utf-8')).set : {}
        const locale = JSON.parse(fs.readFileSync(path.join(SRC, 'locales', `${lang}.json`), 'utf-8'))
        const lookup = (key) => set[key] ?? key.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), locale)
        for (const a of F7_BASE_ACTIONS) {
            const v = lookup(`audit.actions.${a}`)
            assert.ok(typeof v === 'string' && v.trim(), `${lang}: audit.actions.${a} fehlt`)
        }
        for (const rt of F7_RESOURCE_TYPES) {
            const v = lookup(`audit.resourceTypes.${rt}`)
            assert.ok(typeof v === 'string' && v.trim(), `${lang}: audit.resourceTypes.${rt} fehlt`)
        }
        for (const key of ['zoneDetail.tabRecords', 'zoneDetail.fanoutNotLoaded']) {
            assert.ok(typeof lookup(key) === 'string', `${lang}: ${key} fehlt`)
        }
        assert.match(lookup('zoneDetail.fanoutNotLoaded'), /\{\{servers\}\}/)
    }
})
