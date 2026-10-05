// Vertragstest der Einstellungs-Slots (Plan B.14, W0-INT-FE1b):
// tabs/*.tab.jsx, integrations-cards/NN-*.card.jsx, dns-cards/NN-*.card.jsx.
// Die JSX-Dateien werden statisch gelesen (unter node --test nicht ladbar); collectSlots wird echt importiert.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { collectSlots } from '../src/components/settings/settingsContext.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const settingsDir = path.join(here, '..', 'src', 'components', 'settings')
const localesDir = path.join(here, '..', 'src', 'locales')

function listFiles(dir, suffix) {
    if (!fs.existsSync(dir)) return []
    return fs.readdirSync(dir).filter((f) => f.endsWith(suffix)).sort()
}

// Liest das Objekt-Literal hinter `export const <name> = { ... }` (eine Zeile) als einfache Feldtabelle.
function readMeta(text, name) {
    const m = text.match(new RegExp(`^export const ${name} = \\{(.*)\\}\\s*$`, 'm'))
    if (!m) return null
    const meta = {}
    for (const part of m[1].split(',')) {
        const kv = part.match(/^\s*(\w+)\s*:\s*(.+?)\s*$/)
        if (!kv) continue
        const [, key, raw] = kv
        if (/^'[^']*'$/.test(raw)) meta[key] = raw.slice(1, -1)
        else if (/^\d+$/.test(raw)) meta[key] = Number(raw)
        else if (raw === 'true' || raw === 'false') meta[key] = raw === 'true'
        else meta[key] = { expr: raw }
    }
    return meta
}

// Alle bekannten Keys: en.json plus noch nicht gemergte Fragmente (en)
function knownEnKeys() {
    const keys = new Set()
    const walk = (obj, prefix) => {
        for (const [k, v] of Object.entries(obj)) {
            const full = prefix ? `${prefix}.${k}` : k
            if (v && typeof v === 'object') walk(v, full)
            else keys.add(full)
        }
    }
    walk(JSON.parse(fs.readFileSync(path.join(localesDir, 'en.json'), 'utf-8')), '')
    for (const f of listFiles(path.join(localesDir, 'fragments'), '.en.json')) {
        const frag = JSON.parse(fs.readFileSync(path.join(localesDir, 'fragments', f), 'utf-8'))
        for (const k of Object.keys(frag.set || {})) keys.add(k)
    }
    return keys
}

test('Tabs: Dateiname = id, Pflichtfelder, eindeutige id/order, Default-Export', () => {
    const dir = path.join(settingsDir, 'tabs')
    const files = listFiles(dir, '.tab.jsx')
    assert.ok(files.length >= 11, `zu wenige Tabs: ${files.join(', ')}`)
    const keys = knownEnKeys()
    const ids = new Set()
    const orders = new Map()
    for (const f of files) {
        const text = fs.readFileSync(path.join(dir, f), 'utf-8')
        const meta = readMeta(text, 'tab')
        assert.ok(meta, `${f}: "export const tab = { ... }" fehlt (einzeilig)`)
        const id = f.replace(/\.tab\.jsx$/, '')
        assert.equal(meta.id, id, `${f}: tab.id muss "${id}" sein`)
        assert.equal(typeof meta.order, 'number', `${f}: tab.order fehlt`)
        assert.equal(typeof meta.labelKey, 'string', `${f}: tab.labelKey fehlt`)
        assert.ok(keys.has(meta.labelKey), `${f}: labelKey ${meta.labelKey} fehlt in en.json/Fragmenten`)
        assert.ok(meta.icon && meta.icon.expr, `${f}: tab.icon fehlt`)
        assert.equal(typeof meta.adminOnly, 'boolean', `${f}: tab.adminOnly muss true/false sein`)
        assert.match(text, /^export default function \w+/m, `${f}: Default-Komponente fehlt`)
        assert.ok(!ids.has(id), `doppelte Tab-ID ${id}`)
        ids.add(id)
        assert.ok(!orders.has(meta.order), `${f}: order ${meta.order} schon von ${orders.get(meta.order)} belegt`)
        orders.set(meta.order, f)
    }
    for (const id of ['profile', 'integrations', 'servers', 'templates', 'smtp', 'welcome', 'security', 'acme', 'updates', 'about', 'dns']) {
        assert.ok(ids.has(id), `Tab ${id} fehlt`)
    }
})

test('Nicht-Admin-Tabs wie 2.4.1: profile, integrations, about', () => {
    const dir = path.join(settingsDir, 'tabs')
    const nonAdmin = listFiles(dir, '.tab.jsx')
        .map((f) => readMeta(fs.readFileSync(path.join(dir, f), 'utf-8'), 'tab'))
        .filter((m) => m.adminOnly === false)
        .map((m) => m.id)
        .sort()
    assert.deepEqual(nonAdmin, ['about', 'integrations', 'profile'])
})

for (const sub of ['integrations-cards', 'dns-cards']) {
    test(`${sub}: NN-<name>.card.jsx, card.order = NN, eindeutige id, Default-Export`, () => {
        const dir = path.join(settingsDir, sub)
        const ids = new Set()
        for (const f of listFiles(dir, '.jsx')) {
            const m = f.match(/^(\d{2})-[a-z0-9-]+\.card\.jsx$/)
            assert.ok(m, `${sub}/${f}: Dateiname muss NN-<name>.card.jsx sein`)
            const text = fs.readFileSync(path.join(dir, f), 'utf-8')
            const meta = readMeta(text, 'card')
            assert.ok(meta, `${sub}/${f}: "export const card = { ... }" fehlt (einzeilig)`)
            assert.equal(typeof meta.id, 'string', `${sub}/${f}: card.id fehlt`)
            assert.equal(meta.order, Number(m[1]), `${sub}/${f}: card.order muss ${Number(m[1])} sein`)
            assert.match(text, /^export default function \w+/m, `${sub}/${f}: Default-Komponente fehlt`)
            assert.ok(!ids.has(meta.id), `${sub}: doppelte Karten-ID ${meta.id}`)
            ids.add(meta.id)
        }
    })
}

test('Integrations-Karten aus 2.4.1 vorhanden, altes Panel geloescht', () => {
    const files = listFiles(path.join(settingsDir, 'integrations-cards'), '.card.jsx')
    for (const f of ['10-totp.card.jsx', '20-passkeys.card.jsx', '30-panel-tokens.card.jsx', '40-webhooks.card.jsx']) {
        assert.ok(files.includes(f), `${f} fehlt`)
    }
    assert.ok(!fs.existsSync(path.join(settingsDir, '..', 'SettingsIntegrationsPanel.jsx')))
})

test('Einmal-Secrets (f76) laufen ueber OneTimeSecretModal', () => {
    for (const f of ['30-panel-tokens.card.jsx', '40-webhooks.card.jsx']) {
        const text = fs.readFileSync(path.join(settingsDir, 'integrations-cards', f), 'utf-8')
        assert.match(text, /<OneTimeSecretModal\b/, `${f}: OneTimeSecretModal fehlt`)
        assert.doesNotMatch(text, /clipboard\.writeText\([^)]*\);\s*setPlain/, `${f}: Secret wird nach dem Kopieren verworfen`)
    }
})

test('Shell bindet keine Tabs direkt ein (nur Registry)', () => {
    const text = fs.readFileSync(path.join(here, '..', 'src', 'pages', 'SettingsPage.jsx'), 'utf-8')
    assert.doesNotMatch(text, /from '[^']*settings\/tabs\//)
    assert.match(text, /SettingsTabRegistry/)
})

test('collectSlots: sortiert nach order/id, ignoriert kaputte und doppelte Slots', () => {
    const A = () => null
    const errors = []
    const orig = console.error
    console.error = (msg) => errors.push(String(msg))
    try {
        const list = collectSlots({
            './tabs/b.tab.jsx': { tab: { id: 'b', order: 20 }, default: A },
            './tabs/a.tab.jsx': { tab: { id: 'a', order: 20 }, default: A },
            './tabs/c.tab.jsx': { tab: { id: 'c', order: 5 }, default: A },
            './tabs/x.tab.jsx': { default: A },
            './tabs/y.tab.jsx': { tab: { id: 'y', order: 1 } },
            './tabs/a2.tab.jsx': { tab: { id: 'a', order: 1 }, default: A },
            './tabs/n.tab.jsx': { tab: { id: 'n' }, default: A },
        }, 'tab')
        assert.deepEqual(list.map((s) => s.id), ['c', 'a', 'b', 'n'])
        assert.equal(list[3].order, 1000)
        assert.equal(list[0].Component, A)
        assert.equal(list[0].file, './tabs/c.tab.jsx')
        assert.equal(errors.length, 3)
    } finally {
        console.error = orig
    }
})
