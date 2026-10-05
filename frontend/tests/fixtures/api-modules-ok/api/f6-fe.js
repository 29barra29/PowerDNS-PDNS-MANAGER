/* Fixture: neue Methoden, Kommentar mit getA() { } darf nicht zaehlen */
export default {
    // getB() { } im Kommentar
    getWebhookDeliveries(id, { limit = 25, offset = 0 } = {}) {
        const s = '{ getA() }'
        return this.request('GET', `/auth/me/webhooks/${id}/deliveries?limit=${limit}&offset=${offset}`, s)
    },
}
