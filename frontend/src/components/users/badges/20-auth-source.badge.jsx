import { useTranslation } from 'react-i18next'
import { isExternalAccount } from '../../sso/ssoModel'

// Badge "SSO" bzw. "LDAP" der Benutzerliste (F10 §2.8, WS-F10-APP-FE). Tooltip: "Anmeldung über <Aussteller>";
// bei Rollen-Modus "sync" zusaetzlich users.roleManagedBySso. Den sso-Block aus GET /auth/users
// ({ oidc_role_mode, ldap_role_mode, ... }) reicht die Seite optional als context.sso durch.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const badge = { id: 'auth-source', order: 20, when: (user) => isExternalAccount(user) }

export default function AuthSourceBadge({ user, context }) {
    const { t } = useTranslation()
    const ldap = user.auth_source === 'ldap'
    const label = ldap ? t('users.authSourceLdap') : t('users.authSourceOidc')
    const issuer = user.external_issuer && user.external_issuer !== 'ldap' ? user.external_issuer : label
    const roleSynced = context?.sso?.[ldap ? 'ldap_role_mode' : 'oidc_role_mode'] === 'sync'
    const title = [t('users.authSourceTitle', { issuer }), roleSynced ? t('users.roleManagedBySso') : '']
        .filter(Boolean).join(' – ')
    return (
        <span className="text-xs px-2 py-0.5 rounded-full border bg-violet-500/10 text-violet-300 border-violet-500/30" title={title}>
            {label}
        </span>
    )
}
