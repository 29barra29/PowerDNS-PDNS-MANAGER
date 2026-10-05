/**
 * Audit-Aktionen aus dem F5-Kern (W0-SECRETS): Startmigration der gespeicherten Geheimnisse
 * (backend/app/core/secrets.py, init_secrets). Ohne handelnden Nutzer, nur Anzahlen/Fingerprint.
 * Labels (`audit.actions.*`) liefern F5-BE/F5-FE (Bauplan Anhang A2).
 */
export default [
    { action: 'SECRETS_KEY_GENERATED', group: 'system' },
    { action: 'SECRETS_MIGRATE', group: 'system' },
    { action: 'SECRETS_ROTATE', group: 'system' },
]
