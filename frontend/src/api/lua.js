// API-Modul LUA-Records (F15 3.2–3.5, Plan B.14 / Regel 10; Workstream WS-F15).
// Wird von src/api.js per import.meta.glob in den API-Client gemischt (`this` = Client).
import { buildQuery } from '../lib/buildQuery.js'

export default {
    // { policy, can_write, reason, target_types, max_content_length } - LUA-Policy fuer den angemeldeten Nutzer
    getLuaPolicy({ signal } = {}) {
        return this.request('GET', '/lua/policy', null, { signal })
    },
    // { servers: [{ name, reachable, lua_records, geoip_backend, edns_subnet_processing, exec_limit,
    //   health_checks_interval, error, checked_at }], checked_at, cached } - refresh wirkt nur fuer Admins
    getLuaServerStatus(refresh = false, { signal } = {}) {
        const q = buildQuery({ refresh: refresh ? 'true' : undefined })
        return this.request('GET', `/lua/server-status${q}`, null, { signal })
    },
    // Admin (Browser-Session): { policy, default_policy, target_types, max_content_length }
    getLuaSettings({ signal } = {}) {
        return this.request('GET', '/settings/lua', null, { signal })
    },
    // { policy: 'admin'|'manage'|'disabled' } -> { message, settings: { policy } }
    updateLuaSettings(data) {
        return this.request('PUT', '/settings/lua', data)
    },
}
