/**
 * Audit-Aktionen von WS-F10-SVC (SSO-Dienste): Rollenabgleich aus IdP-/Verzeichnisgruppen
 * (services/sso_provisioning.apply_role). Label: `audit.actions.USER_ROLE_SYNC` (Fragment ws-f10-svc).
 * Die Aktionen der SSO-Router (SSO_SETTINGS_UPDATE, SSO_SETTINGS_TEST, USER_SSO_LINK, ...) liefert WS-F10-APP-BE.
 */
export default [
    { action: 'USER_ROLE_SYNC', group: 'users', history: false },
]
