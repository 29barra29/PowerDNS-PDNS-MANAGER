/**
 * Audit-Aktionen von WS-F10-APP-BE (SSO-Router, Login-Umbau, Step-up). Labels: `audit.actions.<ACTION>` im
 * Fragment ws-f10-app-be. Details enthalten nie Secret-Werte, Passwoerter oder Tokens.
 * - SSO_SETTINGS_UPDATE / SSO_SETTINGS_TEST: PUT bzw. POST /settings/sso[/test] (resource_type "system")
 * - USER_SSO_LINK: Selbst-Verknuepfung mit OIDC/LDAP (auch Fehlversuche mit Status "error")
 * - USER_CONVERT_LOCAL: Admin wandelt ein externes Konto in ein lokales um
 * LOGIN/LOGIN_FAILED (Methoden oidc, ldap, *+totp) sind Basis-Aktionen, USER_ROLE_SYNC liefert ws-f10-svc.
 */
export default [
    { action: 'SSO_SETTINGS_UPDATE', group: 'settings' },
    { action: 'SSO_SETTINGS_TEST', group: 'settings' },
    { action: 'USER_SSO_LINK', group: 'auth' },
    { action: 'USER_CONVERT_LOCAL', group: 'users' },
]
