/**
 * Audit-Aktionen aus WS-F2F3 (F2/F3, Bauplan [S9]). ZONE_NOTIFY steht bereits in base.actions.js.
 * Labels: `audit.actions.<ACTION>` im Fragment f2f3.<lang>.json.
 * - ZONE_EXPORT: zonenbezogen, aber nicht im Zonenverlauf (Backup-Skripte erzeugen je Lauf einen Eintrag, F3 12).
 * - USER_*: Admin-Aktionen auf ein Benutzerkonto (details.target_user_id).
 * - PASSWORD_RESET: Nutzer hat per Reset-Link ein neues Passwort gesetzt (details.via = "link").
 */
export default [
    { action: 'ZONE_EXPORT', group: 'zone', history: false },
    { action: 'USER_PASSWORD_RESET_LINK', group: 'users' },
    { action: 'USER_2FA_RESET', group: 'users' },
    { action: 'USER_PASSKEYS_RESET', group: 'users' },
    { action: 'USER_ACCESS_REVOKE', group: 'users' },
    { action: 'PASSWORD_RESET', group: 'auth' },
]
