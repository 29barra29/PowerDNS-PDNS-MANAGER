// Webhook-UI-Helfer (F6 §6, Plan WS-F6-FE): reine Funktionen aus src/components/webhooks/webhookUi.js.
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
    AUTO_REFRESH_MS, DELIVERY_STATUSES, PAGE_SIZE, PENDING_STATUSES, STATUS_STYLES, TEST_EVENT,
    buildEventFilters, canAddWebhook, categoryLabel, createWebhookPayload, deliveryEventOptions, diffWebhookUpdate,
    errorLabel, eventChips, eventKey, filterLabel, eventLabel, groupEvents, initialWebhookForm, isCoveredEvent, isValidWebhookUrl,
    parseEventFilters, prettyJson, removeLegacyFilter, responseSummary, retryAction, sameEventFilters,
    shouldAutoRefresh, statusLabel, statusStyle, testResultInfo, toggleEventToken, validateWebhookForm,
    webhookRowState, workerBannerKey,
} from '../src/components/webhooks/webhookUi.js'

// Katalog wie backend/app/services/webhook_events.SUBSCRIBABLE_EVENTS
const AVAILABLE = [
    'record.created', 'record.updated', 'record.deleted', 'record.bulk', 'record.rollback', 'record.ptr_synced',
    'zone.created', 'zone.updated', 'zone.deleted', 'zone.imported',
    'dnssec.enabled', 'dnssec.disabled', 'dnssec.key_created', 'dnssec.key_activated', 'dnssec.key_deactivated',
    'dnssec.key_published', 'dnssec.key_unpublished', 'dnssec.key_deleted', 'dnssec.nsec3_changed',
    'dyndns.updated',
]
const CATS = ['record', 'zone', 'dnssec', 'dyndns']

// Mini-t: bekannte Keys aus DICT (mit {{var}}-Ersetzung), sonst defaultValue, sonst der Key.
const DICT = {
    'webhooks.event.record_created': 'Record angelegt',
    'webhooks.errorCode.http_status': 'HTTP-Fehler {{code}}',
    'webhooks.errorCode.timeout': 'Zeitueberschreitung',
    'webhooks.status.dead': 'Endgueltig fehlgeschlagen',
    'webhooks.category.zone': 'Zonen',
    'webhooks.noResponse': 'Keine Antwort',
    'webhooks.testSuccess': 'OK {{code}} {{ms}}',
    'webhooks.testFailed': 'FEHLER {{error}}',
}
function t(key, opts = {}) {
    const raw = DICT[key] ?? opts.defaultValue ?? key
    return String(raw).replace(/\{\{(\w+)\}\}/g, (_, v) => String(opts[v] ?? ''))
}

// ------------------------------------------------------------------ Konstanten / Labels
test('Status-Konstanten: alle API-Status inkl. cancelled, Pending-Teilmenge, Styles fuer jeden Status', () => {
    assert.deepEqual([...DELIVERY_STATUSES], ['queued', 'in_progress', 'succeeded', 'failed', 'dead', 'cancelled'])
    assert.deepEqual([...PENDING_STATUSES], ['queued', 'in_progress', 'failed'])
    for (const s of DELIVERY_STATUSES) assert.ok(STATUS_STYLES[s], `Style fuer ${s}`)
    assert.equal(statusStyle('gibtsnicht'), statusStyle('cancelled'))
    assert.equal(PAGE_SIZE, 25)
    assert.equal(AUTO_REFRESH_MS, 5000)
})

test('eventKey/eventLabel: Punkte -> Unterstrich, unbekannte Ereignisse zeigen den Rohnamen', () => {
    assert.equal(eventKey('record.created'), 'webhooks.event.record_created')
    assert.equal(eventKey('dnssec.key_created'), 'webhooks.event.dnssec_key_created')
    assert.equal(eventLabel(t, 'record.created'), 'Record angelegt')
    assert.equal(eventLabel(t, 'record.ptr_synced'), 'record.ptr_synced')
    assert.equal(statusLabel(t, 'dead'), 'Endgueltig fehlgeschlagen')
    assert.equal(statusLabel(t, 'neu'), 'neu')
    assert.equal(categoryLabel(t, 'zone'), 'Zonen')
    assert.equal(categoryLabel(t, 'misc'), 'misc')
})

test('filterLabel: *, Kategorie, Kategorie.*, Ereignis, Altbestand', () => {
    const tt = (key, opts = {}) => (key === 'webhooks.categoryAll' ? `ALLE ${opts.category}` : t(key, opts))
    assert.equal(filterLabel(tt, '*'), 'webhooks.eventsAll')
    assert.equal(filterLabel(tt, 'zone', CATS), 'ALLE Zonen')
    assert.equal(filterLabel(tt, 'zone.*', CATS), 'ALLE Zonen')
    assert.equal(filterLabel(tt, 'record.created', CATS), 'Record angelegt')
    assert.equal(filterLabel(tt, 'record.create', CATS), 'record.create')
    assert.equal(filterLabel(tt, 'dyndns', []), 'ALLE dyndns')
})

test('errorLabel: Fehlercode uebersetzt mit HTTP-Code, unbekannter Code -> Servertext, ohne Fehler leer', () => {
    assert.equal(errorLabel(t, { last_error_code: 'http_status', last_status_code: 500 }), 'HTTP-Fehler 500')
    assert.equal(errorLabel(t, { last_error_code: 'timeout' }), 'Zeitueberschreitung')
    assert.equal(errorLabel(t, { last_error_code: 'neu_im_backend', last_error: 'Klartext' }), 'Klartext')
    assert.equal(errorLabel(t, { last_error_code: 'neu_im_backend' }), 'neu_im_backend')
    assert.equal(errorLabel(t, { last_error: 'nur Text' }), 'nur Text')
    assert.equal(errorLabel(t, {}), '')
    assert.equal(errorLabel(t, null), '')
})

test('responseSummary: Erfolg mit Dauer, Fehler, keine Antwort', () => {
    assert.equal(responseSummary(t, { last_status_code: 200, last_duration_ms: 12 }), 'HTTP 200 · 12 ms')
    assert.equal(responseSummary(t, { last_status_code: 204 }), 'HTTP 204')
    assert.equal(responseSummary(t, { last_status_code: 503, last_error_code: 'http_status' }), 'HTTP-Fehler 503')
    assert.equal(responseSummary(t, { status: 'queued' }), 'Keine Antwort')
})

test('testResultInfo: Erfolg mit Code/ms, Fehler mit uebersetztem Code bzw. Servermeldung', () => {
    assert.deepEqual(
        testResultInfo(t, { success: true, delivery: { last_status_code: 200, last_duration_ms: 34 } }),
        { ok: true, text: 'OK 200 34' },
    )
    assert.deepEqual(
        testResultInfo(t, { success: false, message: 'x', delivery: { last_error_code: 'http_status', last_status_code: 404 } }),
        { ok: false, text: 'FEHLER HTTP-Fehler 404' },
    )
    assert.deepEqual(testResultInfo(t, { success: false, message: 'Test fehlgeschlagen: kaputt' }),
        { ok: false, text: 'FEHLER Test fehlgeschlagen: kaputt' })
})

// ------------------------------------------------------------------ Ereignis-Baum
test('groupEvents: Kategorien in API-Reihenfolge, webhook.* faellt heraus, unbekannte Kategorie am Ende', () => {
    const g = groupEvents([...AVAILABLE, 'webhook.test', 'acme.renewed', 'record.created'], CATS)
    assert.deepEqual(g.map((x) => x.category), ['record', 'zone', 'dnssec', 'dyndns', 'acme'])
    assert.equal(g[0].events.length, 6)
    assert.deepEqual(g[3].events, ['dyndns.updated'])
    assert.ok(!g.flatMap((x) => x.events).includes('webhook.test'))
    // ohne Kategorienliste: Default; leere Kategorien entfallen
    assert.deepEqual(groupEvents(['zone.created']).map((x) => x.category), ['zone'])
})

test('parseEventFilters: *, leer, Kategorie, Kategorie.*, Einzelereignis und Altbestand', () => {
    const groups = groupEvents(AVAILABLE, CATS)
    assert.equal(parseEventFilters(['*'], groups).all, true)
    assert.equal(parseEventFilters([], groups).all, true)
    assert.equal(parseEventFilters(undefined, groups).all, true)
    const s = parseEventFilters(['record', 'zone.*', 'dnssec.enabled', 'Record.Create', 'foo'], groups)
    assert.equal(s.all, false)
    assert.deepEqual([...s.tokens].sort(), ['dnssec.enabled', 'record', 'zone'])
    assert.deepEqual(s.legacy, ['record.create', 'foo'])
})

test('buildEventFilters: * gewinnt, Einzelereignisse unter gewaehlter Kategorie entfallen, Altbestand bleibt', () => {
    const groups = groupEvents(AVAILABLE, CATS)
    assert.deepEqual(buildEventFilters({ all: true, tokens: new Set(['record']), legacy: ['x'] }, groups), ['*'])
    const state = { all: false, tokens: new Set(['record', 'record.created', 'dnssec.enabled']), legacy: ['old.filter'] }
    assert.deepEqual(buildEventFilters(state, groups), ['record', 'dnssec.enabled', 'old.filter'])
    assert.deepEqual(buildEventFilters({ all: false, tokens: new Set(), legacy: [] }, groups), [])
    // Rundlauf
    const round = buildEventFilters(parseEventFilters(['zone.deleted', 'dyndns'], groups), groups)
    assert.deepEqual(round, ['zone.deleted', 'dyndns'])
})

test('toggleEventToken/isCoveredEvent/removeLegacyFilter liefern neue Zustaende', () => {
    const s0 = { all: true, tokens: new Set(), legacy: ['a', 'b'] }
    const s1 = toggleEventToken(s0, '*')
    assert.equal(s1.all, false)
    assert.equal(s0.all, true)
    const s2 = toggleEventToken(s1, 'record')
    assert.ok(s2.tokens.has('record'))
    assert.ok(!s1.tokens.has('record'))
    assert.ok(isCoveredEvent(s2, 'record.created'))
    assert.ok(!isCoveredEvent(s2, 'zone.created'))
    assert.ok(isCoveredEvent(s0, 'zone.created'))
    const s3 = toggleEventToken(s2, 'record')
    assert.ok(!s3.tokens.has('record'))
    assert.deepEqual(removeLegacyFilter(s3, 'a').legacy, ['b'])
})

test('sameEventFilters: Bedeutung statt Schreibweise', () => {
    assert.ok(sameEventFilters(['*'], []))
    assert.ok(sameEventFilters(['record.*', 'zone'], ['zone', 'record']))
    assert.ok(sameEventFilters([' Record '], ['record']))
    assert.ok(!sameEventFilters(['record'], ['record.created']))
    assert.ok(!sameEventFilters(['*'], ['record']))
})

// ------------------------------------------------------------------ Formular
test('isValidWebhookUrl / validateWebhookForm', () => {
    assert.ok(isValidWebhookUrl('https://hooks.example.com/x'))
    assert.ok(isValidWebhookUrl('  http://example.org  '))
    assert.ok(!isValidWebhookUrl('ftp://example.org'))
    assert.ok(!isValidWebhookUrl('https://'))
    assert.ok(!isValidWebhookUrl('https://exa mple.org'))
    assert.ok(!isValidWebhookUrl(`https://e.org/${'a'.repeat(1100)}`))

    const ok = { name: 'Slack', url: 'https://hooks.example.com/x' }
    assert.equal(validateWebhookForm(ok, ['*']), null)
    assert.equal(validateWebhookForm({ ...ok, name: '   ' }, ['*']), 'webhooks.nameRequired')
    assert.equal(validateWebhookForm({ ...ok, name: 'x'.repeat(101) }, ['*']), 'webhooks.nameTooLong')
    assert.equal(validateWebhookForm({ ...ok, url: 'example.org' }, ['*']), 'webhooks.urlInvalid')
    assert.equal(validateWebhookForm({ ...ok, url: '' }, ['*']), 'webhooks.urlInvalid')
    assert.equal(validateWebhookForm(ok, []), 'webhooks.eventsNoneSelected')
    // Bearbeiten: leere URL heisst "unveraendert", ausser die gespeicherte URL ist unlesbar
    assert.equal(validateWebhookForm({ ...ok, url: '' }, ['*'], { urlRequired: false }), null)
    assert.equal(validateWebhookForm({ ...ok, url: 'kaputt' }, ['*'], { urlRequired: false }), 'webhooks.urlInvalid')
})

test('initialWebhookForm: Defaults, Vorbelegung, unlesbare URL bleibt leer [S10]', () => {
    assert.deepEqual(initialWebhookForm(null), { name: '', url: '', scope: 'own' })
    assert.deepEqual(initialWebhookForm({ name: 'A', url: 'https://a.example/x', scope: 'zones', has_url: true }),
        { name: 'A', url: 'https://a.example/x', scope: 'zones' })
    assert.deepEqual(initialWebhookForm({ name: 'A', url: null, url_display: null, has_url: false }),
        { name: 'A', url: '', scope: 'own' })
})

test('createWebhookPayload: getrimmt, Scope normalisiert, aktiv', () => {
    assert.deepEqual(createWebhookPayload({ name: ' A ', url: ' https://a.example ', scope: 'zones' }, ['record']), {
        name: 'A', url: 'https://a.example', events: ['record'], scope: 'zones', is_active: true,
    })
    assert.equal(createWebhookPayload({ name: 'A', url: 'https://a.example', scope: 'boese' }, ['*']).scope, 'own')
})

test('diffWebhookUpdate: nur geaenderte Felder; keine Aenderung -> leeres Objekt', () => {
    const hook = { id: 1, name: 'A', url: 'https://a.example/x', has_url: true, scope: 'own', events: ['record.*'] }
    assert.deepEqual(diffWebhookUpdate(hook, { name: 'A', url: 'https://a.example/x', scope: 'own' }, ['record']), {})
    assert.deepEqual(diffWebhookUpdate(hook, { name: ' B ', url: 'https://a.example/x', scope: 'zones' }, ['record']),
        { name: 'B', scope: 'zones' })
    assert.deepEqual(diffWebhookUpdate(hook, { name: 'A', url: 'https://b.example', scope: 'own' }, ['*']),
        { url: 'https://b.example', events: ['*'] })
    // unlesbare URL: jede eingetragene URL ist eine Aenderung
    const broken = { ...hook, url: null, has_url: false }
    assert.deepEqual(diffWebhookUpdate(broken, { name: 'A', url: 'https://a.example/x', scope: 'own' }, ['record']),
        { url: 'https://a.example/x' })
    // leere URL im Formular sendet keine URL
    assert.deepEqual(diffWebhookUpdate(hook, { name: 'A', url: '', scope: 'own' }, ['record']), {})
})

// ------------------------------------------------------------------ Liste
test('webhookRowState: Zaehler, Fehlversuch-Schwelle, letzter Fehler nach letztem Erfolg, unlesbar', () => {
    const st = webhookRowState({
        stats: { queued: 2, in_progress: 1, failed: 3, succeeded: 9, dead: 4, cancelled: 5 },
        consecutive_failures: 3,
        last_success_at: '2026-10-01T10:00:00+00:00',
        last_failure_at: '2026-10-01T11:00:00+00:00',
        has_url: false,
        has_secret: false,
    })
    assert.deepEqual(st, { pending: 6, dead: 4, failing: 3, showLastFailure: true, urlUnreadable: true, secretUnreadable: true })

    const ok = webhookRowState({
        stats: {}, consecutive_failures: 2,
        last_success_at: '2026-10-01T12:00:00Z', last_failure_at: '2026-10-01T11:00:00',
        has_url: true, has_secret: true,
    })
    assert.deepEqual(ok, { pending: 0, dead: 0, failing: 0, showLastFailure: false, urlUnreadable: false, secretUnreadable: false })
    assert.equal(webhookRowState({ last_failure_at: '2026-10-01T11:00:00Z' }).showLastFailure, true)
    assert.equal(webhookRowState({}).showLastFailure, false)
})

test('workerBannerKey / canAddWebhook / eventChips', () => {
    assert.equal(workerBannerKey({ worker_enabled: false, worker_running: false }), 'webhooks.workerDisabled')
    assert.equal(workerBannerKey({ worker_enabled: true, worker_running: false }), 'webhooks.workerNotRunning')
    assert.equal(workerBannerKey({ worker_enabled: true, worker_running: true }), null)
    assert.equal(workerBannerKey(null), null)

    assert.ok(canAddWebhook([], { max_webhooks: 20 }))
    assert.ok(!canAddWebhook(new Array(20).fill({}), { max_webhooks: 20 }))
    assert.ok(canAddWebhook(new Array(50).fill({}), {}))

    assert.deepEqual(eventChips(['a', 'b', 'c', 'd', 'e', 'f']), { shown: ['a', 'b', 'c', 'd'], rest: 2 })
    assert.deepEqual(eventChips([]), { shown: ['*'], rest: 0 })
})

// ------------------------------------------------------------------ Zustellprotokoll
test('shouldAutoRefresh: nur bei offenen Zeilen', () => {
    assert.ok(shouldAutoRefresh([{ status: 'succeeded' }, { status: 'failed' }]))
    assert.ok(shouldAutoRefresh([{ status: 'in_progress' }]))
    assert.ok(!shouldAutoRefresh([{ status: 'succeeded' }, { status: 'dead' }, { status: 'cancelled' }]))
    assert.ok(!shouldAutoRefresh([]))
    assert.ok(!shouldAutoRefresh(null))
})

test('retryAction: can_retry entscheidet, failed -> retryNow, succeeded mit Rueckfrage', () => {
    assert.equal(retryAction({ status: 'dead', can_retry: false }), null)
    assert.equal(retryAction({ status: 'queued', can_retry: false }), null)
    assert.deepEqual(retryAction({ status: 'failed', can_retry: true }), { kind: 'retryNow', confirmKey: null })
    assert.deepEqual(retryAction({ status: 'dead', can_retry: true }), { kind: 'retry', confirmKey: null })
    assert.deepEqual(retryAction({ status: 'cancelled', can_retry: true }), { kind: 'retry', confirmKey: null })
    assert.deepEqual(retryAction({ status: 'succeeded', can_retry: true }),
        { kind: 'retry', confirmKey: 'webhooks.retryConfirmSucceeded' })
})

test('deliveryEventOptions: abonnierbare Ereignisse + webhook.test, ohne Duplikate', () => {
    const opts = deliveryEventOptions(['record.created', 'record.created', 'zone.deleted'])
    assert.deepEqual(opts, ['record.created', 'zone.deleted', TEST_EVENT])
    assert.deepEqual(deliveryEventOptions(undefined), [TEST_EVENT])
})

test('prettyJson: Objekte eingerueckt, Strings unveraendert, leer bei null', () => {
    assert.equal(prettyJson({ a: 1 }), '{\n  "a": 1\n}')
    assert.equal(prettyJson('kein json'), 'kein json')
    assert.equal(prettyJson(null), '')
    const cyc = {}
    cyc.self = cyc
    assert.equal(prettyJson(cyc), '[object Object]')
})
