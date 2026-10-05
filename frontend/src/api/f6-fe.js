// Webhook-Verwaltung des eigenen Kontos (F6 §6.2, Plan WS-F6-FE).
// Pfade /auth/me/webhooks… (Router routers/webhooks.py, WS-F6-BE). Alles ausser der Liste verlangt eine
// Browser-Session; per API-Token antwortet das Backend mit 403.
//
// Dateiname = Schluessel in ALLOWED_OVERRIDES (frontend/tests/api-modules.test.mjs, Entscheidung wave-0b-1).
// Ueberschreibungen laut Plan B.14: createWebhook/deleteWebhook ersetzen die Kernmethoden aus api.js
// (Body normalisiert, IDs kodiert); listWebhooks ist der neue Name fuer das Laden der Liste (Kern: getWebhooks,
// bleibt fuer Altaufrufer erhalten). updateWebhook bleibt die Kernmethode aus api.js.
import { buildQuery } from '../lib/buildQuery.js'

export const overrides = ['listWebhooks', 'createWebhook', 'deleteWebhook']

const BASE = '/auth/me/webhooks'
const seg = (v) => encodeURIComponent(String(v))

export default {
    // -> { webhooks, available_events, event_categories, worker_enabled, worker_running, max_attempts,
    //      retention_days, max_webhooks }
    listWebhooks({ signal } = {}) {
        return this.request('GET', BASE, null, { signal })
    },

    // data: { name, url, events, scope, is_active } -> 201 { webhook, secret, warning }
    createWebhook(data = {}) {
        const body = {
            name: String(data.name ?? '').trim(),
            url: String(data.url ?? '').trim(),
            events: Array.isArray(data.events) && data.events.length ? data.events : ['*'],
            scope: data.scope === 'zones' ? 'zones' : 'own',
            is_active: data.is_active !== false,
        }
        return this.request('POST', BASE, body)
    },

    // -> { message, deleted_deliveries }
    deleteWebhook(id) {
        return this.request('DELETE', `${BASE}/${seg(id)}`)
    },

    // -> WebhookOut (deaktivieren verwirft offene Zustellungen)
    setWebhookActive(id, isActive) {
        return this.request('PUT', `${BASE}/${seg(id)}`, { is_active: !!isActive })
    },

    // -> WebhookOut + new_secret
    rotateWebhookSecret(id) {
        return this.request('PUT', `${BASE}/${seg(id)}`, { rotate_secret: true })
    },

    // -> { success, message, delivery }; 429 bei Cooldown (10 s)
    testWebhook(id) {
        return this.request('POST', `${BASE}/${seg(id)}/test`, {})
    },

    // -> { total, limit, offset, deliveries }
    getWebhookDeliveries(id, { limit = 25, offset = 0, status = '', event = '', signal } = {}) {
        const q = buildQuery({ limit, offset, status: status || undefined, event: event || undefined })
        return this.request('GET', `${BASE}/${seg(id)}/deliveries${q}`, null, { signal })
    },

    // -> DeliveryOut + body + request_headers
    getWebhookDelivery(id, deliveryPk, { signal } = {}) {
        return this.request('GET', `${BASE}/${seg(id)}/deliveries/${seg(deliveryPk)}`, null, { signal })
    },

    // -> { message, delivery }; 409 mit detail, wenn nicht erlaubt
    retryWebhookDelivery(id, deliveryPk) {
        return this.request('POST', `${BASE}/${seg(id)}/deliveries/${seg(deliveryPk)}/retry`, {})
    },
}
