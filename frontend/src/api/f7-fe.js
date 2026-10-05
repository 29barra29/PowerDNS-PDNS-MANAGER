// API-Modul F7 (Record-Historie, Rollback, Admin-Audit-Log) - Plan B.14, Regel 10, Spec F7 §6.2.
// Vertrag der Endpunkte: F7 §3.2-3.10 (Backend WS-F7-BE, routers/history.py und routers/search.py).
//
// Dateiname = Schluessel in ALLOWED_OVERRIDES (frontend/tests/api-modules.test.mjs): nur `getAuditLog` darf
// die gleichnamige Kernmethode aus api.js ersetzen (abwaertskompatibel: Zahl = { limit }).
// Der CSV-Download mit Filtern heisst `exportAuditLogCsv`, weil `downloadAuditLogCsv` eine Kernmethode ohne
// Freigabe ist; die Kernmethode (ohne Filter) bleibt unveraendert bestehen.
import { buildQuery } from '../lib/buildQuery.js'

export const overrides = ['getAuditLog']

const seg = (v) => encodeURIComponent(String(v ?? ''))

function zonePath(server, zone) {
    return `/zones/${seg(server)}/${seg(zone)}/history`
}

// Bereinigt Parameter: `signal` gehoert nicht in die Query.
function splitSignal(params) {
    if (!params || typeof params !== 'object') return { query: {}, signal: undefined }
    const { signal, ...query } = params
    return { query, signal }
}

export default {
    // GET /audit-log (Admin). params: { limit, offset, action, resource_type, server_name, zone, user_id,
    // status, date_from, date_to, q, full, signal }. Eine Zahl wird als { limit } gelesen (2.4.1-Aufrufer).
    getAuditLog(params = {}) {
        const p = typeof params === 'number' ? { limit: params } : params
        const { query, signal } = splitSignal(p)
        return this.request('GET', `/audit-log${buildQuery(query)}`, null, { signal })
    },

    // GET /audit-log/{id} (Admin): ungekuerzte Details.
    getAuditLogEntry(id, { signal } = {}) {
        return this.request('GET', `/audit-log/${seg(id)}`, null, { signal })
    },

    // GET /audit-log/settings (Admin-Session): Aufbewahrung, Statistik, Worker-Status.
    getAuditSettings({ signal } = {}) {
        return this.request('GET', '/audit-log/settings', null, { signal })
    },

    // PUT /audit-log/settings (Admin-Session): { retention_days } -> { message, settings }.
    updateAuditSettings(data) {
        return this.request('PUT', '/audit-log/settings', data)
    },

    // GET /audit-log/export mit denselben Filtern wie die Liste (ohne limit/offset/full) -> Browser-Download.
    // Fehlerantworten (z. B. 400 Zeitraum) liest requestRaw als JSON und wirft err.message.
    async exportAuditLogCsv(params = {}) {
        const { query, signal } = splitSignal(params)
        delete query.limit
        delete query.offset
        delete query.full
        const res = await this.requestRaw('GET', `/audit-log/export${buildQuery(query)}`, { signal })
        const blob = await res.blob()
        const disposition = res.headers.get('Content-Disposition') || ''
        const match = /filename="?([^";]+)"?/i.exec(disposition)
        const name = match ? match[1] : 'audit-log.csv'
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = name
        document.body.appendChild(a)
        a.click()
        a.remove()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
    },

    // GET /zones/{server}/{zone}/history (Lese- oder Schreibrecht auf die Zone).
    // params: { limit, offset, action, resource_type, type, name, q, user_id, status, date_from, date_to, signal }
    getZoneHistory(server, zone, params = {}) {
        const { query, signal } = splitSignal(params)
        return this.request('GET', `${zonePath(server, zone)}${buildQuery(query)}`, null, { signal })
    },

    // GET /zones/{server}/{zone}/history/{id}: ein Eintrag mit allen Aenderungen (404 bei fremder Zone).
    getZoneHistoryEntry(server, zone, auditId, { signal } = {}) {
        return this.request('GET', `${zonePath(server, zone)}/${seg(auditId)}`, null, { signal })
    },

    // GET .../history/{id}/rollback-preview (Schreibrecht): Plan, Konflikte, Zielserver.
    getRollbackPreview(server, zone, auditId, { signal } = {}) {
        return this.request('GET', `${zonePath(server, zone)}/${seg(auditId)}/rollback-preview`, null, { signal })
    },

    // POST .../history/{id}/rollback mit { force }. 409 = Konflikt (err.code 'rollback_conflict').
    rollbackZoneChange(server, zone, auditId, { force = false } = {}) {
        return this.request('POST', `${zonePath(server, zone)}/${seg(auditId)}/rollback`, { force: Boolean(force) })
    },
}
