// Welle-1-Integrationstest WS-F6-FE <-> WS-F6-BE (Plan Regel 3, F.2): die Webhook-UI passt zum API-Vertrag.
// Liest die Backend-Quellen statisch (kein Python noetig):
//   backend/app/schemas/webhooks.py           DeliveryStatus, WebhookOut-Felder
//   backend/app/services/webhook_sender.py    ERROR_CODES
//   backend/app/services/webhook_events.py    EVENT_CATALOG, EVENT_CATEGORIES
// und prueft gegen src/components/webhooks/webhookUi.js sowie die Locale-Texte (en.json nach dem Merge bzw.
// das noch nicht gemergte Fragment f6-fe.en.json).
// Vor dem Merge von WS-F6-BE fehlen schemas/webhooks.py und webhook_sender.py -> die Tests werden mit Grund
// uebersprungen (sichtbar im Testbericht); im Integrationsschritt F.2 laufen sie.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { DEFAULT_EVENT_CATEGORIES, DELIVERY_STATUSES, STATUS_STYLES, eventKey } from '../../src/components/webhooks/webhookUi.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..', '..')
const BACKEND = path.resolve(FRONTEND, '..', 'backend', 'app')
const SCHEMAS = path.join(BACKEND, 'schemas', 'webhooks.py')
const SENDER = path.join(BACKEND, 'services', 'webhook_sender.py')
const EVENTS = path.join(BACKEND, 'services', 'webhook_events.py')

const missing = [SCHEMAS, SENDER, EVENTS].filter((p) => !fs.existsSync(p))
const SKIP = missing.length
    ? `WS-F6-BE noch nicht gemergt (fehlt: ${missing.map((p) => path.relative(path.resolve(FRONTEND, '..'), p)).join(', ')})`
    : false

function pyStrings(src) {
    return [...src.matchAll(/"([^"\\]*)"/g)].map((m) => m[1])
}

function pyTuple(file, name) {
    const src = fs.readFileSync(file, 'utf-8')
    const m = src.match(new RegExp(`^${name}\\s*(?::[^=]+)?=\\s*\\(([\\s\\S]*?)\\)`, 'm'))
    assert.ok(m, `${path.basename(file)}: ${name} nicht gefunden`)
    return pyStrings(m[1])
}

function flatten(obj, prefix = '', out = new Map()) {
    for (const [k, v] of Object.entries(obj)) {
        const key = prefix ? `${prefix}.${k}` : k
        if (v && typeof v === 'object') flatten(v, key, out)
        else out.set(key, v)
    }
    return out
}

// Englische Texte: en.json (nach dem Merge) plus noch vorhandenes Fragment
function englishKeys() {
    const keys = new Set(flatten(JSON.parse(fs.readFileSync(path.join(FRONTEND, 'src', 'locales', 'en.json'), 'utf-8'))).keys())
    const frag = path.join(FRONTEND, 'src', 'locales', 'fragments', 'f6-fe.en.json')
    if (fs.existsSync(frag)) {
        const f = JSON.parse(fs.readFileSync(frag, 'utf-8'))
        for (const k of f.remove || []) keys.delete(k)
        for (const k of Object.keys(f.set || {})) keys.add(k)
    }
    return keys
}

test('DeliveryStatus (Backend) == DELIVERY_STATUSES (UI), jeder Status mit Badge-Stil und Text', { skip: SKIP }, () => {
    const src = fs.readFileSync(SCHEMAS, 'utf-8')
    const m = src.match(/^DeliveryStatus\s*=\s*Literal\[([^\]]*)\]/m)
    assert.ok(m, 'DeliveryStatus nicht gefunden')
    assert.deepEqual(pyStrings(m[1]).sort(), [...DELIVERY_STATUSES].sort())
    const en = englishKeys()
    for (const s of DELIVERY_STATUSES) {
        assert.ok(STATUS_STYLES[s], `Stil fuer ${s}`)
        assert.ok(en.has(`webhooks.status.${s}`), `webhooks.status.${s} fehlt`)
    }
})

test('WebhookOut liefert die Felder, die die Karte liest (inkl. has_url/url_display [S10])', { skip: SKIP }, () => {
    const src = fs.readFileSync(SCHEMAS, 'utf-8')
    const body = src.match(/^class WebhookOut\(BaseModel\):([\s\S]*?)^class /m)
    assert.ok(body, 'WebhookOut nicht gefunden')
    for (const f of ['id', 'name', 'url', 'url_display', 'has_url', 'events', 'scope', 'is_active', 'has_secret',
        'last_success_at', 'last_failure_at', 'consecutive_failures', 'stats']) {
        assert.match(body[1], new RegExp(`^\\s+${f}:`, 'm'), `WebhookOut.${f} fehlt`)
    }
    assert.match(body[1], /^\s+url_display:\s*Optional\[str\]/m, 'url_display muss null sein koennen (URL unlesbar)')
})

test('jeder Fehlercode des Senders hat einen Text webhooks.errorCode.<code>', { skip: SKIP }, () => {
    const codes = pyTuple(SENDER, 'ERROR_CODES')
    assert.ok(codes.includes('url_unreadable'))
    const en = englishKeys()
    const fehlend = codes.filter((c) => !en.has(`webhooks.errorCode.${c}`))
    assert.deepEqual(fehlend, [])
})

test('jedes Ereignis des Katalogs hat ein Label; Kategorien stimmen ueberein', { skip: SKIP }, () => {
    const catalog = pyTuple(EVENTS, 'EVENT_CATALOG')
    assert.ok(catalog.includes('webhook.test'))
    const en = englishKeys()
    const fehlend = catalog.filter((ev) => !en.has(eventKey(ev)))
    assert.deepEqual(fehlend, [])
    const cats = pyTuple(EVENTS, 'EVENT_CATEGORIES')
    assert.deepEqual(cats, [...DEFAULT_EVENT_CATEGORIES])
    for (const c of cats) assert.ok(en.has(`webhooks.category.${c}`), `webhooks.category.${c} fehlt`)
})
