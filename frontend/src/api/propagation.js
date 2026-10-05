// API-Modul Propagations-Check (F12 §6.1, Plan B.14 / Regel 10; Workstream WS-F12F13-FE).
// Wird von src/api.js per import.meta.glob in den API-Client gemischt (`this` = Client).
import { buildQuery } from '../lib/buildQuery.js'

export default {
    // GET /zones/{server}/{zone}/propagation – Vergleich der SOA-Serial (optional eines Records und des
    // Zoneninhalts) ueber Panel-Server, autoritative Nameserver und oeffentliche Resolver.
    // opts: { name, type, content, signal }; name/type nur zusammen (sonst 422 vom Backend).
    checkZonePropagation(server, zone, { name, type, content, signal } = {}) {
        const q = buildQuery({ name: name || undefined, type: type || undefined, content: content ? 'true' : undefined })
        return this.request(
            'GET',
            `/zones/${encodeURIComponent(server)}/${encodeURIComponent(zone)}/propagation${q}`,
            null,
            { signal },
        )
    },
    // Admin-Einstellungen der externen DNS-Abfragen (F12 §3.2/3.3, nur Browser-Session)
    getPropagationSettings() {
        return this.request('GET', '/settings/propagation')
    },
    updatePropagationSettings(data) {
        return this.request('PUT', '/settings/propagation', data)
    },
}
