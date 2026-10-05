/**
 * Audit-Aktionen aus WS-F5-BE: Recovery/Downgrade per CLI (`python -m app.cli.secrets`), Settings-Backend
 * (Captcha, SMTP-Test) und der Backend-Teil von F8 (App-Info, Logo). Alle ohne Werte in den Details.
 * Labels: `audit.actions.<ACTION>` im Fragment f5-be (Plan Anhang A2); die Labels fuer APP_INFO_UPDATE und
 * APP_LOGO_UPLOAD liefert laut F8 §11 der Audit-Workstream (F7), bis dahin erscheint der Rohname.
 */
export default [
    { action: 'SECRETS_RESET_UNREADABLE', group: 'system' },
    { action: 'SECRETS_DECRYPT_ALL', group: 'system' },
    { action: 'DOWNGRADE_PREPARED', group: 'system' },
    { action: 'CAPTCHA_UPDATE', group: 'settings' },
    { action: 'SMTP_TEST', group: 'settings' },
    { action: 'APP_INFO_UPDATE', group: 'settings' },
    { action: 'APP_LOGO_UPLOAD', group: 'settings' },
]

// resource_type der Geheimnis-Audits (Startmigration, CLI): "system"
export const resourceTypes = ['system']
