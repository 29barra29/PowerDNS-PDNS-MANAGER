import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link2, Loader2, LogIn } from 'lucide-react'
import api from '../../api'
import { authSourceKey, isExternalAccount, safeAuthorizationUrl } from './ssoModel'

// Karte "Anmeldung ueber Firmenkonto" (F10 §2.6/§6.5): eigenes lokales Konto mit OIDC oder LDAP verknuepfen.
// Props: user (Profil aus /auth/me), providers (normalisiertes /auth/sso/providers), onLinked(message)
// Fehler bleiben in der Karte (kein Modal). OIDC: Weiterleitung zum Anbieter, Rueckkehr auf
// /settings?tab=integrations&sso_linked=oidc bzw. &sso_error=<code> (auswertet 05-sso-link.card.jsx).
export default function SsoAccountLinkCard({ user, providers, onLinked }) {
    const { t } = useTranslation()
    const [pw, setPw] = useState('')
    const [totp, setTotp] = useState('')
    const [confirm, setConfirm] = useState(false)
    const [ldapUser, setLdapUser] = useState('')
    const [ldapPw, setLdapPw] = useState('')
    const [busy, setBusy] = useState('')
    const [error, setError] = useState('')

    if (!user || !providers) return null
    const external = isExternalAccount(user)
    const oidcProvider = providers.providers?.find((p) => p.type === 'oidc' || p.id === 'oidc') || providers.providers?.[0]
    const oidcName = oidcProvider?.label || t('settings.authSource.oidc')
    const ldapName = providers.ldap?.label || t('settings.authSource.ldap')

    if (external) {
        const source = user.auth_source === 'ldap'
            ? (providers.ldap?.label || t(authSourceKey('ldap')))
            : (oidcProvider?.label || t(authSourceKey(user.auth_source)))
        return (
            <CardFrame title={t('settings.integrations.ssoTitle')}>
                <p className="text-sm text-text-secondary">{t('settings.integrations.ssoLinkedAs', { source })}</p>
            </CardFrame>
        )
    }

    const canOidc = !!providers.linking?.oidc
    const canLdap = !!providers.linking?.ldap
    if (!canOidc && !canLdap) return null

    const needTotp = !!user.totp_enabled
    const reauthOk = pw.length > 0 && confirm && (!needTotp || totp.length >= 6)
    const reauth = () => ({ current_password: pw, ...(needTotp && totp ? { totp_code: totp } : {}) })

    async function linkOidc() {
        if (!reauthOk || busy) return
        setBusy('oidc')
        setError('')
        try {
            const res = await api.startOidcLink(reauth())
            const url = safeAuthorizationUrl(res?.authorization_url)
            if (!url) throw new Error(t('settings.integrations.ssoLinkBadUrl'))
            setPw('')
            setTotp('')
            window.location.assign(url)
        } catch (err) {
            setError(err?.message || '')
            setBusy('')
        }
    }

    async function linkLdap() {
        if (!reauthOk || busy || !ldapUser.trim() || !ldapPw) return
        setBusy('ldap')
        setError('')
        try {
            await api.linkLdapAccount({ ...reauth(), ldap_username: ldapUser.trim(), ldap_password: ldapPw })
            setPw('')
            setTotp('')
            setLdapPw('')
            setConfirm(false)
            onLinked?.(t('settings.ssoLinkedSuccess', { source: ldapName }))
        } catch (err) {
            setError(err?.message || '')
        } finally {
            setLdapPw('')
            setBusy('')
        }
    }

    return (
        <CardFrame title={t('settings.integrations.ssoTitle')}>
            {error && <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">{error}</div>}
            <p className="text-sm text-text-muted">{t('settings.integrations.ssoLinkHint')}</p>
            <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm">
                {t('settings.integrations.ssoLinkWarning')}
            </div>

            <div className="grid gap-3 md:grid-cols-2 max-w-2xl">
                <div>
                    <label htmlFor="sso-link-password" className="block text-xs text-text-muted mb-1">{t('settings.currentPassword')}</label>
                    <input id="sso-link-password" type="password" value={pw} onChange={(e) => setPw(e.target.value)}
                        className="w-full px-3 py-2 text-sm" autoComplete="current-password" maxLength={128} disabled={!!busy} />
                </div>
                {needTotp && (
                    <div>
                        <label htmlFor="sso-link-totp" className="block text-xs text-text-muted mb-1">{t('settings.integrations.ssoTotpCode')}</label>
                        <input id="sso-link-totp" type="text" inputMode="numeric" autoComplete="one-time-code" value={totp}
                            onChange={(e) => setTotp(e.target.value.replace(/\D/g, '').slice(0, 8))}
                            className="w-full px-3 py-2 text-sm font-mono" placeholder="123456" maxLength={8} disabled={!!busy} />
                    </div>
                )}
            </div>
            <label className="flex items-start gap-2 text-sm text-text-secondary max-w-2xl">
                <input type="checkbox" className="mt-0.5" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} disabled={!!busy} />
                <span>{t('settings.integrations.ssoLinkConfirm')}</span>
            </label>

            {canOidc && (
                <button type="button" onClick={linkOidc} disabled={!reauthOk || !!busy}
                    className="px-4 py-2 rounded-lg bg-gradient-to-r from-accent to-purple-600 text-white text-sm font-medium flex items-center gap-2 disabled:opacity-50">
                    {busy === 'oidc' ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <LogIn className="w-4 h-4" aria-hidden="true" />}
                    {t('settings.integrations.ssoLinkOidc', { name: oidcName })}
                </button>
            )}

            {canLdap && (
                <div className="rounded-xl border border-border/60 p-4 space-y-3 max-w-2xl">
                    <h3 className="text-sm font-semibold text-text-primary">{t('settings.integrations.ssoLinkLdapTitle', { name: ldapName })}</h3>
                    <div className="grid gap-3 md:grid-cols-2">
                        <div>
                            <label htmlFor="sso-link-ldap-user" className="block text-xs text-text-muted mb-1">{t('settings.integrations.ssoLdapUsername')}</label>
                            <input id="sso-link-ldap-user" type="text" value={ldapUser} onChange={(e) => setLdapUser(e.target.value)}
                                className="w-full px-3 py-2 text-sm" autoComplete="off" maxLength={256} disabled={!!busy} />
                        </div>
                        <div>
                            <label htmlFor="sso-link-ldap-pw" className="block text-xs text-text-muted mb-1">{t('settings.integrations.ssoLdapPassword')}</label>
                            <input id="sso-link-ldap-pw" type="password" value={ldapPw} onChange={(e) => setLdapPw(e.target.value)}
                                className="w-full px-3 py-2 text-sm" autoComplete="off" maxLength={1024} disabled={!!busy} />
                        </div>
                    </div>
                    <button type="button" onClick={linkLdap} disabled={!reauthOk || !!busy || !ldapUser.trim() || !ldapPw}
                        className="px-4 py-2 rounded-lg bg-accent/20 text-sm font-medium flex items-center gap-2 disabled:opacity-50">
                        {busy === 'ldap' ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Link2 className="w-4 h-4" aria-hidden="true" />}
                        {t('settings.integrations.ssoLinkLdap')}
                    </button>
                </div>
            )}
        </CardFrame>
    )
}

function CardFrame({ title, children }) {
    return (
        <div className="glass-card p-6 space-y-4">
            <h2 className="text-lg font-bold flex items-center gap-2"><LogIn className="w-5 h-5" aria-hidden="true" />{title}</h2>
            {children}
        </div>
    )
}
