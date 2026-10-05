// Fixture: erlaubte, deklarierte Ueberschreibung (F7-FE getAuditLog)
export const overrides = ['getAuditLog']

export default {
    getAuditLog(params = {}) {
        const q = typeof params === 'number' ? { limit: params } : params
        return this.request('GET', '/audit-log', q)
    },
    getZoneHistory(server, zone) { return this.request('GET', `/zones/${server}/${zone}/history`) },
    nested: undefined,
}
