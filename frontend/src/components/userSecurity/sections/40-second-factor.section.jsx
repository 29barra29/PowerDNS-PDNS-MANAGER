import { useTranslation } from 'react-i18next'
import { Fingerprint, Loader2, ShieldOff } from 'lucide-react'
import api from '../../../api'

// Abschnitt D "Zweiter Faktor" (F3 §2.4): 2FA (TOTP) zuruecksetzen und alle Passkeys entfernen (Lockout-Recovery).
// Auch der Recovery-Weg fuer ein nicht entschluesselbares 2FA-Geheimnis (F5 §5.13, `totp_unreadable`).
// Gilt fuer alle Konten (auch externe: ein lokaler zweiter Faktor kann trotzdem eingerichtet sein).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const section = { id: 'second-factor', order: 40, titleKey: 'users.sectionSecondFactor' }

const DANGER_BTN = 'inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium bg-danger/15 text-danger border border-danger/40 hover:bg-danger/25 disabled:opacity-40 disabled:cursor-not-allowed'

export default function SecondFactorSection({ user, ctx }) {
    const { t } = useTranslation()
    const passkeys = Number(user.passkey_count || 0)

    async function handleReset2fa() {
        if (ctx.busy) return
        if (!window.confirm(t('users.reset2faConfirm', { name: ctx.userName }))) return
        const res = await ctx.run('2fa', () => api.resetUser2fa(user.id))
        if (!res) return
        if (res.user) ctx.setUser(res.user)
        ctx.setInfo(t('users.reset2faDone', { name: ctx.userName }))
        ctx.changed()
    }

    async function handleRemovePasskeys() {
        if (ctx.busy) return
        if (!window.confirm(t('users.removePasskeysConfirm', { name: ctx.userName }))) return
        const res = await ctx.run('passkeys', () => api.deleteUserPasskeys(user.id))
        if (!res) return
        ctx.setUser({ passkey_count: 0 })
        ctx.setInfo(t('users.passkeysRemoved', { count: res.deleted ?? 0 }))
        ctx.changed()
    }

    return (
        <div className="space-y-4">
            <div className="space-y-2">
                {user.totp_enabled ? (
                    <>
                        <p className="text-xs text-text-muted">{t('users.reset2faHint')}</p>
                        {user.totp_unreadable && <p className="text-xs text-warning">{t('users.totpUnreadable')}</p>}
                        <button type="button" onClick={handleReset2fa} disabled={!!ctx.busy} className={DANGER_BTN}>
                            {ctx.busy === '2fa'
                                ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                                : <ShieldOff className="w-4 h-4" aria-hidden="true" />}
                            {t('users.reset2fa')}
                        </button>
                    </>
                ) : (
                    <p className="text-sm text-text-muted">{t('users.totpNotActive')}</p>
                )}
            </div>
            <div className="space-y-2">
                {passkeys > 0 ? (
                    <>
                        <p className="text-xs text-text-muted">{t('users.removePasskeysHint', { count: passkeys })}</p>
                        <button type="button" onClick={handleRemovePasskeys} disabled={!!ctx.busy} className={DANGER_BTN}>
                            {ctx.busy === 'passkeys'
                                ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                                : <Fingerprint className="w-4 h-4" aria-hidden="true" />}
                            {t('users.removePasskeys')}
                        </button>
                    </>
                ) : (
                    <p className="text-sm text-text-muted">{t('users.noPasskeys')}</p>
                )}
            </div>
        </div>
    )
}
