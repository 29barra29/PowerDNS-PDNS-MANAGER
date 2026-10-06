// API-Modul F1 (Bulk-Editor) - Plan B.14, Regel 10, Spec F1 6.1. Keine Ueberschreibungen von Kernmethoden.
// Vertrag: POST /records/{server}/{zone}/bulk/preview (Trockenlauf, Antwort BulkPreviewResponse) und
// POST /records/{server}/{zone}/bulk (Body = ops aus der Vorschau, unveraendert inkl. expected).
const seg = (v) => encodeURIComponent(String(v ?? ''))

export default {
    // data: { ops } (Auswahl) oder { text: { content, mode, scope, default_ttl } }
    previewBulkRecords(server, zone, data, { signal } = {}) {
        return this.request('POST', `/records/${seg(server)}/${seg(zone)}/bulk/preview`, data, { signal })
    },

    // data: BulkRecordUpdate (create/delete/merge/set_ttl/set_disabled/expected/force/source/mode/manage_ptr)
    bulkRecords(server, zone, data) {
        return this.request('POST', `/records/${seg(server)}/${seg(zone)}/bulk`, data)
    },
}
