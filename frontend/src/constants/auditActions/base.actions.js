/**
 * Basis-Audit-Aktionen (F7 §6.6, W0-INT-FE2): alles, was 2.4.1 bereits schreibt, plus die F7-Aktionen
 * RECORD_ROLLBACK, AUDIT_PURGE und AUDIT_SETTINGS_UPDATE sowie die vorab benannten ZONE_NOTIFY und DYNDNS_UPDATE.
 * Labels: `audit.actions.<ACTION>` / `audit.resourceTypes.<type>` (Fragment w0-fe2).
 * Vertrag der Slot-Dateien: siehe src/lib/auditCatalog.js. Neue Aktionen gehoeren in die eigene Datei
 * `<ws>.actions.js` des jeweiligen Workstreams, nicht hierher.
 */
export default [
    // Records (resource_type "record")
    { action: 'CREATE', group: 'records' },
    { action: 'UPDATE', group: 'records' },
    { action: 'DELETE', group: 'records' },
    { action: 'BULK_UPDATE', group: 'records' },
    { action: 'RECORD_ROLLBACK', group: 'records' },
    { action: 'DYNDNS_UPDATE', group: 'records' },
    // Zone (resource_type "zone"; CREATE/UPDATE/DELETE einer Zone teilen sich die Aktionsnamen oben)
    { action: 'IMPORT', group: 'zone' },
    { action: 'ZONE_NOTIFY', group: 'zone' },
    // DNSSEC (resource_type "dnssec_key")
    { action: 'DNSSEC_ENABLE', group: 'dnssec' },
    { action: 'DNSSEC_DISABLE', group: 'dnssec' },
    { action: 'KEY_ACTIVATE', group: 'dnssec' },
    { action: 'KEY_DEACTIVATE', group: 'dnssec' },
    { action: 'KEY_DELETE', group: 'dnssec' },
    // ACME (Challenges sind zonenbezogen, Token-Verwaltung nicht)
    { action: 'ACME_PRESENT', group: 'acme' },
    { action: 'ACME_CLEANUP', group: 'acme' },
    { action: 'ACME_TOKEN_CREATE', group: 'acme', history: false },
    { action: 'ACME_TOKEN_DELETE', group: 'acme', history: false },
    // Server und Einstellungen
    { action: 'SERVER_CREATE', group: 'settings' },
    { action: 'SERVER_UPDATE', group: 'settings' },
    { action: 'SERVER_DELETE', group: 'settings' },
    { action: 'REVEAL_API_KEY', group: 'settings' },
    { action: 'SMTP_UPDATE', group: 'settings' },
    // Anmeldung und eigenes Konto
    { action: 'LOGIN', group: 'auth' },
    { action: 'LOGIN_FAILED', group: 'auth' },
    { action: 'PASSWORD_CHANGE', group: 'auth' },
    { action: 'TOTP_ENABLE', group: 'auth' },
    { action: 'TOTP_DISABLE', group: 'auth' },
    { action: 'PASSKEY_ADD', group: 'auth' },
    { action: 'PASSKEY_DELETE', group: 'auth' },
    { action: 'PANEL_TOKEN_CREATE', group: 'auth' },
    { action: 'PANEL_TOKEN_DELETE', group: 'auth' },
    // Benutzerverwaltung (Admin)
    { action: 'USER_CREATE', group: 'users' },
    { action: 'USER_UPDATE', group: 'users' },
    { action: 'USER_DELETE', group: 'users' },
    { action: 'USER_PASSWORD_RESET', group: 'users' },
    { action: 'ZONE_ACCESS_UPDATE', group: 'users' },
    // Audit-Log selbst (F7)
    { action: 'AUDIT_PURGE', group: 'system' },
    { action: 'AUDIT_SETTINGS_UPDATE', group: 'system' },
]

// resource_type-Werte von 2.4.1 plus "audit_log" (F7). Weitere Typen liefern die Workstreams in ihrer Datei.
export const resourceTypes = [
    'zone', 'record', 'dnssec_key', 'user', 'server_config', 'settings', 'acme', 'acme_token', 'audit_log',
]
