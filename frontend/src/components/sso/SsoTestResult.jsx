import { useTranslation } from 'react-i18next'
import { CheckCircle2, XCircle } from 'lucide-react'

// Ergebnis von POST /settings/sso/test (F10 §3.3.3): { success, message, error, warnings, details }.
// OIDC-details: issuer, *_endpoint, jwks_uri, signing_algs, pkce_s256, jwks_keys, redirect_uri.
// LDAP-details: server, security, service_bind, user: { dn, username, email, display_name, unique_id, groups,
// groups_total, is_allowed, is_admin, password_checked, password_ok } | null.
export default function SsoTestResult({ result, target }) {
    const { t } = useTranslation()
    if (!result) return null
    const ok = !!result.success
    const d = result.details && typeof result.details === 'object' ? result.details : {}
    const yesNo = (v) => (v ? t('settings.sso.yes') : t('settings.sso.no'))
    const endpoints = target === 'oidc'
        ? [
            ['issuer', d.issuer],
            ['authorization_endpoint', d.authorization_endpoint],
            ['token_endpoint', d.token_endpoint],
            ['userinfo_endpoint', d.userinfo_endpoint],
            ['jwks_uri', d.jwks_uri],
            ['end_session_endpoint', d.end_session_endpoint],
        ].filter(([, v]) => v)
        : []
    const user = target === 'ldap' && d.user && typeof d.user === 'object' ? d.user : null
    const groups = Array.isArray(user?.groups) ? user.groups : []

    return (
        <div
            role="status"
            className={`rounded-lg border p-3 text-sm space-y-2 ${ok ? 'border-success/30 bg-success/5' : 'border-danger/30 bg-danger/5'}`}
        >
            <p className={`font-medium flex items-center gap-2 ${ok ? 'text-success' : 'text-danger'}`}>
                {ok ? <CheckCircle2 className="w-4 h-4" aria-hidden="true" /> : <XCircle className="w-4 h-4" aria-hidden="true" />}
                {ok ? t('settings.sso.testOk') : t('settings.sso.testFailed')}
            </p>
            {result.message && <p className="text-text-secondary">{result.message}</p>}
            {result.error && <p className="text-danger break-words">{result.error}</p>}

            {endpoints.length > 0 && (
                <div>
                    <p className="text-xs font-medium text-text-secondary">{t('settings.sso.testResultEndpoints')}</p>
                    <dl className="text-xs font-mono break-all space-y-0.5">
                        {endpoints.map(([k, v]) => (
                            <div key={k}><dt className="inline text-text-muted">{k}: </dt><dd className="inline">{String(v)}</dd></div>
                        ))}
                    </dl>
                </div>
            )}
            {target === 'oidc' && Number.isFinite(d.jwks_keys) && (
                <p className="text-xs text-text-secondary">{t('settings.sso.testResultKeys', { count: d.jwks_keys })}</p>
            )}
            {target === 'oidc' && Array.isArray(d.signing_algs) && d.signing_algs.length > 0 && (
                <p className="text-xs text-text-secondary">{t('settings.sso.testResultAlgs', { algs: d.signing_algs.join(', ') })}</p>
            )}

            {target === 'ldap' && d.server && (
                <p className="text-xs text-text-secondary font-mono break-all">
                    {t('settings.sso.testResultServer', { server: d.server, security: d.security || '–', bind: d.service_bind || '–' })}
                </p>
            )}
            {user && (
                <div className="text-xs text-text-secondary space-y-0.5">
                    <p className="break-all">{t('settings.sso.testResultUser', { dn: user.dn || '–' })}</p>
                    <p className="break-all">{t('settings.sso.testResultMapped', {
                        username: user.username || '–',
                        email: user.email || '–',
                        name: user.display_name || '–',
                        id: user.unique_id || '–',
                    })}</p>
                    <p className="break-all">{t('settings.sso.testResultGroups', {
                        count: Number.isFinite(user.groups_total) ? user.groups_total : groups.length,
                        groups: groups.length ? groups.join('; ') : '–',
                    })}</p>
                    <p>{t('settings.sso.testResultAllowed', { value: yesNo(user.is_allowed) })}</p>
                    <p>{t('settings.sso.testResultAdmin', { value: yesNo(user.is_admin) })}</p>
                    <p>{t('settings.sso.testResultPassword', {
                        value: user.password_checked ? yesNo(user.password_ok) : t('settings.sso.testPasswordNotChecked'),
                    })}</p>
                </div>
            )}

            {Array.isArray(result.warnings) && result.warnings.length > 0 && (
                <ul className="list-disc pl-4 text-xs text-amber-300 space-y-0.5">
                    {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
            )}
        </div>
    )
}
