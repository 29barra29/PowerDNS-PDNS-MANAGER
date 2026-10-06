import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Copy, Loader2, UserCog } from 'lucide-react'
import api from '../../../api'
import { isLocalAccount } from '../userSecurityModel'
import { authSourceKey, isExternalAccount } from '../../sso/ssoModel'
import { isStepUpAbort, withStepUp } from '../../sso/stepUpStore'

// Abschnitt E "Externe Anmeldung" (F10 §2.8/§3.2.10, WS-F10-APP-FE) im Dialog "Passwort & Sicherheit".
// Nur fuer Konten mit SSO-/LDAP-Anmeldung (Abschnitte 10-30 blenden sich dort aus). Zeigt Anmeldedienst,
// Aussteller und externe ID; "In lokales Konto umwandeln" verlangt immer einen Step-up des Admins [S8]
// (lokales Admin-Konto: Passwort/2FA vorab, externes Admin-Konto: frische Anmeldung) und zeigt danach das
// Zufallspasswort einmalig (F3-Mechanik ctx.showOneTime).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const section = { id: 'external', order: 50, titleKey: 'users.sectionExternal', when: (user) => !isLocalAccount(user) }

export default function ExternalSection({ user, ctx }) {
    const { t } = useTranslation()
    const [mustChange, setMustChange] = useState(true)
    const [copied, setCopied] = useState(false)
    const source = t(authSourceKey(user.auth_source))

    async function copyId() {
        try {
            await navigator.clipboard.writeText(String(user.external_id || ''))
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        } catch {
            setCopied(false)
        }
    }

    async function handleConvert() {
        if (ctx.busy) return
        if (!window.confirm(`${t('users.convertToLocalConfirm', { name: ctx.userName })}\n\n${t('users.convertToLocalHint')}`)) return
        // Der Admin bestaetigt sich selbst; lokale Admins vorab, externe ueber reauth_required
        const proactive = !isExternalAccount(api.getUser())
        const res = await ctx.run('convert', async () => {
            try {
                return await withStepUp(
                    (stepUp) => api.convertUserToLocal(user.id, { must_change_password: mustChange }, stepUp),
                    { proactive },
                )
            } catch (err) {
                if (isStepUpAbort(err)) return undefined
                if (err?.stepUpHandled) {
                    ctx.setError(t('users.convertReauthNeeded'))
                    return undefined
                }
                throw err
            }
        })
        if (!res) return
        ctx.setUser({ auth_source: 'local', external_issuer: null, external_id: null, must_change_password: !!res.must_change_password })
        ctx.changed(t('users.convertedToLocal', { name: ctx.userName }))
        if (res.new_password) ctx.showOneTime({ password: res.new_password, name: ctx.userName })
    }

    return (
        <div className="space-y-3">
            <p className="text-xs text-text-muted">{t('users.externalManagedHint')}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                <dt className="text-text-muted">{t('users.externalSource')}</dt>
                <dd className="text-text-primary">{source}</dd>
                <dt className="text-text-muted">{t('users.externalIssuer')}</dt>
                <dd className="text-text-primary break-all">{user.external_issuer || '–'}</dd>
                <dt className="text-text-muted">{t('users.externalId')}</dt>
                <dd className="flex items-center gap-2 min-w-0">
                    <code className="font-mono text-xs break-all">{user.external_id || '–'}</code>
                    {user.external_id && (
                        <button type="button" onClick={copyId} className="p-1 rounded hover:bg-bg-hover text-text-muted shrink-0" aria-label={t('common.copy')} title={t('common.copy')}>
                            {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
                        </button>
                    )}
                </dd>
            </dl>
            <p className="text-xs text-text-muted">{t('users.convertToLocalHint')}</p>
            <label className="flex items-center gap-2 text-sm text-text-secondary">
                <input type="checkbox" checked={mustChange} onChange={(e) => setMustChange(e.target.checked)} disabled={!!ctx.busy} />
                {t('users.mustChangeOnNextLogin')}
            </label>
            <button
                type="button"
                onClick={handleConvert}
                disabled={!!ctx.busy}
                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium bg-danger/15 text-danger border border-danger/40 hover:bg-danger/25 disabled:opacity-40"
            >
                {ctx.busy === 'convert' ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <UserCog className="w-4 h-4" aria-hidden="true" />}
                {t('users.convertToLocal')}
            </button>
        </div>
    )
}
