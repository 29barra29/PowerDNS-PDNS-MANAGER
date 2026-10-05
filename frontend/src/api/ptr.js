// API-Modul PTR-Pflege (F11 §3.7/3.8, Plan B.14 / Regel 10; Workstream WS-F9F11-FE).
// Wird von src/api.js per import.meta.glob in den API-Client gemischt (`this` = Client).
import { buildQuery } from '../lib/buildQuery.js'

export default {
    // { auto_default, reverse_zones_available } - Admin-Default und Anzahl beschreibbarer Reverse-Zonen
    getPtrConfig({ signal } = {}) {
        return this.request('GET', '/ptr/config', null, { signal })
    },
    // Live-Pruefung im Record-Dialog: { ip, ptr, zone, status, current, would, classless_zone, detail }
    lookupPtr(ip, name, server, { signal } = {}) {
        const q = buildQuery({ ip, name: name || undefined, server: server || undefined })
        return this.request('GET', `/ptr/lookup${q}`, null, { signal })
    },
    // Admin (Browser-Session): { auto_default }
    getPtrSettings({ signal } = {}) {
        return this.request('GET', '/settings/ptr', null, { signal })
    },
    updatePtrSettings(data) {
        return this.request('PUT', '/settings/ptr', data)
    },
}
