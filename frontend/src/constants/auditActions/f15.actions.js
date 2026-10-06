/**
 * Audit-Aktionen WS-F15 (backend/app/routers/settings_lua.py): Aenderung der LUA-Policy
 * (resource_type "settings", resource_name "lua", details.changed.policy.from/to).
 * LUA-Records selbst laufen ueber die bestehenden Record-Aktionen (CREATE/UPDATE/DELETE/BULK_UPDATE), ein
 * abgelehnter Import mit LUA ueber IMPORT (status error, details.lua_count).
 * Label: `audit.actions.LUA_SETTINGS_UPDATE` im Fragment f15.<lang>.json.
 */
export default [
    { action: 'LUA_SETTINGS_UPDATE', group: 'settings' },
]
