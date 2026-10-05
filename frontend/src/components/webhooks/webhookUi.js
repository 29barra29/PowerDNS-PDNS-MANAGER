// Reine Helfer der Webhook-Oberflaeche (F6 §6.1/§6.4, Plan WS-F6-FE).
// Keine React-/i18n-Importe: per `node --test` ladbar (frontend/tests/webhookUi.test.mjs) und ohne
// Komponenten-Exporte (react-refresh). Uebersetzt wird ueber das uebergebene `t`.

// Zustell-Status laut API (F6 3.0 + `cancelled` aus dem Zugangs-Widerruf [S9]).
export const DELIVERY_STATUSES = Object.freeze(['queued', 'in_progress', 'succeeded', 'failed', 'dead', 'cancelled'])

// Status, bei denen noch ein Versuch kommt bzw. laeuft (Badge "offen", Auto-Refresh im Protokoll).
export const PENDING_STATUSES = Object.freeze(['queued', 'in_progress', 'failed'])

// Fallback, falls die Liste keine `event_categories` liefert (Backend: webhook_events.EVENT_CATEGORIES).
export const DEFAULT_EVENT_CATEGORIES = Object.freeze(['record', 'zone', 'dnssec', 'dyndns'])

// Ereignis der Test-Zustellung: nicht abonnierbar, aber im Protokoll-Filter waehlbar.
export const TEST_EVENT = 'webhook.test'

// Ab so vielen Fehlversuchen in Folge erscheint das Badge "failing" (F6 2.1).
export const FAILING_THRESHOLD = 3

export const PAGE_SIZE = 25
export const AUTO_REFRESH_MS = 5000
export const NAME_MAX = 100
export const URL_MAX = 1024

// Tailwind-Klassen je Status-Badge.
export const STATUS_STYLES = Object.freeze({
    queued: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
    in_progress: 'bg-accent/15 text-accent-light border-accent/30',
    succeeded: 'bg-success/15 text-success border-success/30',
    failed: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    dead: 'bg-danger/15 text-danger border-danger/30',
    cancelled: 'bg-bg-hover text-text-muted border-border',
})

const UNKNOWN_STATUS_STYLE = 'bg-bg-hover text-text-muted border-border'

export function statusStyle(status) {
    return STATUS_STYLES[status] || UNKNOWN_STATUS_STYLE
}

export function isPendingStatus(status) {
    return PENDING_STATUSES.includes(status)
}

// ---------------------------------------------------------------------------------------------
// Labels

// i18n-Key eines Ereignisses: 'record.created' -> 'webhooks.event.record_created'
export function eventKey(ev) {
    return `webhooks.event.${String(ev ?? '').replaceAll('.', '_')}`
}

// Uebersetztes Ereignis-Label; unbekannte Ereignisse (Altbestand, neuere Server) zeigen den Rohnamen.
export function eventLabel(t, ev) {
    return t(eventKey(ev), { defaultValue: String(ev ?? '') })
}

export function statusLabel(t, status) {
    return t(`webhooks.status.${status}`, { defaultValue: String(status ?? '') })
}

export function categoryLabel(t, category) {
    return t(`webhooks.category.${category}`, { defaultValue: String(category ?? '') })
}

// Label eines gespeicherten Abo-Filters (Chips in der Liste): '*' -> "Alle Ereignisse", Kategorie bzw.
// '<kategorie>.*' -> "Alle <Kategorie>-Ereignisse", sonst das Ereignis-Label.
export function filterLabel(t, filter, categories = DEFAULT_EVENT_CATEGORIES) {
    const f = String(filter ?? '').trim().toLowerCase()
    if (!f || f === '*') return t('webhooks.eventsAll')
    const cat = f.endsWith('.*') ? f.slice(0, -2) : f
    if ((categories && categories.length ? categories : DEFAULT_EVENT_CATEGORIES).includes(cat)) {
        return t('webhooks.categoryAll', { category: categoryLabel(t, cat) })
    }
    return eventLabel(t, f)
}

// Fehlertext einer Zustellung: bekannter Fehlercode -> Uebersetzung (mit HTTP-Code), sonst der deutsche
// Klartext des Servers (`last_error`), sonst der Rohcode. Ohne Fehler -> ''.
export function errorLabel(t, d) {
    if (!d) return ''
    const code = d.last_error_code
    const httpCode = d.last_status_code ?? ''
    if (code) {
        return t(`webhooks.errorCode.${code}`, { code: httpCode, defaultValue: d.last_error || String(code) })
    }
    return d.last_error || ''
}

// Spalte "Antwort": Erfolg -> "HTTP 200 · 123 ms", Fehler -> errorLabel, nichts -> webhooks.noResponse.
export function responseSummary(t, d) {
    if (!d) return ''
    if (!d.last_error_code && d.last_status_code != null) {
        const ms = d.last_duration_ms != null ? ` · ${d.last_duration_ms} ms` : ''
        return `HTTP ${d.last_status_code}${ms}`
    }
    const err = errorLabel(t, d)
    if (err) return err
    return t('webhooks.noResponse')
}

// Ergebnis von POST .../test -> { ok, text } fuer die Inline-Anzeige unter der Zeile.
export function testResultInfo(t, res) {
    const d = res?.delivery || null
    if (res?.success) {
        return {
            ok: true,
            text: t('webhooks.testSuccess', { code: d?.last_status_code ?? '', ms: d?.last_duration_ms ?? '' }),
        }
    }
    const error = errorLabel(t, d) || res?.message || t('webhooks.noResponse')
    return { ok: false, text: t('webhooks.testFailed', { error }) }
}

// ---------------------------------------------------------------------------------------------
// Ereignis-Auswahl (Checkbox-Baum, F6 6.4)

function categoryOf(ev) {
    const s = String(ev)
    const i = s.indexOf('.')
    return i > 0 ? s.slice(0, i) : s
}

// Gruppiert die abonnierbaren Ereignisse nach Kategorie (Reihenfolge laut `categories`, danach unbekannte
// Kategorien in Fundreihenfolge). `webhook.*` ist nie abonnierbar und faellt heraus.
export function groupEvents(available, categories = DEFAULT_EVENT_CATEGORIES) {
    const cats = [...(categories && categories.length ? categories : DEFAULT_EVENT_CATEGORIES)]
    const byCat = new Map(cats.map((c) => [c, []]))
    for (const raw of available || []) {
        const ev = String(raw || '').trim()
        if (!ev || ev.startsWith('webhook.')) continue
        const c = categoryOf(ev)
        if (!byCat.has(c)) byCat.set(c, [])
        const list = byCat.get(c)
        if (!list.includes(ev)) list.push(ev)
    }
    return [...byCat.entries()]
        .filter(([, events]) => events.length > 0)
        .map(([category, events]) => ({ category, events }))
}

// Gespeicherte Filter -> Baum-Zustand { all, tokens: Set, legacy: [] }.
// '*' oder leer -> all; 'record' und 'record.*' -> Kategorie-Token; bekannte Ereignisse -> Token;
// alles andere (Altbestand) -> legacy (bleibt erhalten, bis der Benutzer es entfernt).
export function parseEventFilters(events, groups) {
    const cats = new Set((groups || []).map((g) => g.category))
    const known = new Set((groups || []).flatMap((g) => g.events))
    const list = (Array.isArray(events) ? events : [])
        .map((e) => String(e ?? '').trim().toLowerCase())
        .filter(Boolean)
    if (list.length === 0 || list.includes('*')) return { all: true, tokens: new Set(), legacy: [] }
    const tokens = new Set()
    const legacy = []
    for (const e of list) {
        const cat = e.endsWith('.*') ? e.slice(0, -2) : e
        if (cats.has(cat)) tokens.add(cat)
        else if (known.has(e)) tokens.add(e)
        else if (!legacy.includes(e)) legacy.push(e)
    }
    return { all: false, tokens, legacy }
}

// Baum-Zustand -> Array fuer die API. Einzel-Ereignisse einer gewaehlten Kategorie entfallen.
// Leeres Ergebnis bedeutet "nichts gewaehlt" (Validierung: webhooks.eventsNoneSelected).
export function buildEventFilters(state, groups) {
    if (!state) return []
    if (state.all) return ['*']
    const out = []
    for (const g of groups || []) {
        if (state.tokens.has(g.category)) {
            out.push(g.category)
            continue
        }
        for (const ev of g.events) if (state.tokens.has(ev)) out.push(ev)
    }
    for (const l of state.legacy || []) if (!out.includes(l)) out.push(l)
    return out
}

// Ein Token im Baum umschalten ('*', Kategorie oder Ereignis); liefert einen neuen Zustand.
export function toggleEventToken(state, token) {
    if (token === '*') return { ...state, all: !state.all }
    const tokens = new Set(state.tokens)
    if (tokens.has(token)) tokens.delete(token)
    else tokens.add(token)
    return { ...state, tokens }
}

export function removeLegacyFilter(state, value) {
    return { ...state, legacy: (state.legacy || []).filter((l) => l !== value) }
}

// Ist ein Einzel-Ereignis durch '*' oder seine Kategorie bereits abgedeckt (Checkbox abgehakt + gesperrt)?
export function isCoveredEvent(state, ev) {
    return Boolean(state?.all || state?.tokens?.has(categoryOf(ev)))
}

// Vergleich zweier Filterlisten nach Bedeutung ('record.*' == 'record', Reihenfolge egal, leer == '*').
export function sameEventFilters(a, b) {
    const norm = (list) => {
        const xs = (Array.isArray(list) ? list : [])
            .map((e) => String(e ?? '').trim().toLowerCase())
            .filter(Boolean)
            .map((e) => (e.endsWith('.*') ? e.slice(0, -2) : e))
        if (xs.length === 0 || xs.includes('*')) return ['*']
        return [...new Set(xs)].sort()
    }
    const x = norm(a)
    const y = norm(b)
    return x.length === y.length && x.every((v, i) => v === y[i])
}

// ---------------------------------------------------------------------------------------------
// Formular

const URL_RE = /^https?:\/\/[^\s/?#]+\S*$/i

export function isValidWebhookUrl(url) {
    const s = String(url ?? '').trim()
    return s.length >= 8 && s.length <= URL_MAX && URL_RE.test(s)
}

// Formular-Startwerte aus einem Webhook (edit) bzw. Defaults (create).
export function initialWebhookForm(hook) {
    return {
        name: hook?.name || '',
        // Bei unlesbarer URL (has_url === false) bleibt das Feld leer und muss neu befuellt werden [S10]
        url: hook && hook.has_url !== false ? (hook.url || '') : '',
        scope: hook?.scope === 'zones' ? 'zones' : 'own',
    }
}

// Client-Validierung (F6 2.2 Schritt 3). Liefert den i18n-Key des ersten Fehlers oder null.
// `urlRequired`: beim Anlegen immer; beim Bearbeiten nur, wenn die gespeicherte URL unlesbar ist.
export function validateWebhookForm(form, events, { urlRequired = true } = {}) {
    const name = String(form?.name ?? '').trim()
    if (!name) return 'webhooks.nameRequired'
    if (name.length > NAME_MAX) return 'webhooks.nameTooLong'
    const url = String(form?.url ?? '').trim()
    if (url || urlRequired) {
        if (!isValidWebhookUrl(url)) return 'webhooks.urlInvalid'
    }
    if (!Array.isArray(events) || events.length === 0) return 'webhooks.eventsNoneSelected'
    return null
}

// Body fuer POST (Anlegen).
export function createWebhookPayload(form, events) {
    return {
        name: String(form.name ?? '').trim(),
        url: String(form.url ?? '').trim(),
        events,
        scope: form.scope === 'zones' ? 'zones' : 'own',
        is_active: true,
    }
}

// Nur geaenderte Felder fuer PUT (F6 2.3 Schritt 3). Leeres Objekt -> kein Request.
export function diffWebhookUpdate(hook, form, events) {
    const out = {}
    const name = String(form.name ?? '').trim()
    if (name && name !== hook.name) out.name = name
    const url = String(form.url ?? '').trim()
    const storedUrl = hook.has_url === false ? null : (hook.url || null)
    if (url && url !== storedUrl) out.url = url
    const scope = form.scope === 'zones' ? 'zones' : 'own'
    if (scope !== (hook.scope === 'zones' ? 'zones' : 'own')) out.scope = scope
    if (!sameEventFilters(events, hook.events)) out.events = events
    return out
}

// ---------------------------------------------------------------------------------------------
// Liste

function ts(value) {
    if (!value) return NaN
    const s = String(value)
    // naive ISO-Werte als UTC lesen (wie lib/datetime.js)
    const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(s) ? s : `${s.replace(' ', 'T')}Z`)
    return d.getTime()
}

// Anzeige-Zustand einer Webhook-Zeile (F6 2.1 Schritt 4).
export function webhookRowState(hook) {
    const stats = hook?.stats || {}
    const n = (k) => Number(stats[k] || 0)
    const failure = ts(hook?.last_failure_at)
    const success = ts(hook?.last_success_at)
    const failing = Number(hook?.consecutive_failures || 0)
    return {
        pending: n('queued') + n('in_progress') + n('failed'),
        dead: n('dead'),
        failing: failing >= FAILING_THRESHOLD ? failing : 0,
        showLastFailure: Number.isFinite(failure) && (!Number.isFinite(success) || failure > success),
        urlUnreadable: hook?.has_url === false,
        secretUnreadable: hook?.has_secret === false,
    }
}

// Banner oben in der Karte: Zustellung abgeschaltet bzw. Worker laeuft nicht. null = kein Banner.
export function workerBannerKey(meta) {
    if (!meta) return null
    if (meta.worker_enabled === false) return 'webhooks.workerDisabled'
    if (meta.worker_enabled && meta.worker_running === false) return 'webhooks.workerNotRunning'
    return null
}

export function canAddWebhook(hooks, meta) {
    const max = Number(meta?.max_webhooks || 0)
    return !max || (hooks?.length || 0) < max
}

// Ereignis-Chips: hoechstens `max` sichtbar, Rest als "+n".
export function eventChips(events, max = 4) {
    const list = Array.isArray(events) && events.length ? events.map(String) : ['*']
    return { shown: list.slice(0, max), rest: Math.max(0, list.length - max) }
}

// ---------------------------------------------------------------------------------------------
// Zustellprotokoll

// Auto-Refresh nur, solange eine sichtbare Zeile noch offen ist (F6 2.7 Schritt 6).
export function shouldAutoRefresh(items) {
    return Array.isArray(items) && items.some((d) => isPendingStatus(d?.status))
}

// Retry-Button: null (keiner), 'retryNow' (failed) oder 'retry'; confirm bei bereits zugestellten Zeilen.
export function retryAction(d) {
    if (!d?.can_retry) return null
    return {
        kind: d.status === 'failed' ? 'retryNow' : 'retry',
        confirmKey: d.status === 'succeeded' ? 'webhooks.retryConfirmSucceeded' : null,
    }
}

// Ereignis-Optionen des Protokoll-Filters: abonnierbare Ereignisse + Test-Zustellung.
export function deliveryEventOptions(available) {
    const out = []
    for (const ev of available || []) if (ev && !out.includes(ev)) out.push(String(ev))
    if (!out.includes(TEST_EVENT)) out.push(TEST_EVENT)
    return out
}

// Payload/Header huebsch formatiert; Strings (nicht parsebarer Body) bleiben unveraendert.
export function prettyJson(value) {
    if (value === undefined || value === null) return ''
    if (typeof value === 'string') return value
    try {
        return JSON.stringify(value, null, 2)
    } catch {
        return String(value)
    }
}
