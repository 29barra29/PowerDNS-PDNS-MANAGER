import { useTranslation } from 'react-i18next'
import { Loader2, Mail } from 'lucide-react'
import api from '../../../api'
import { isLocalAccount, resetLinkState } from '../userSecurityModel'

// Abschnitt C "Reset-Link per E-Mail senden" (F3 §2.4): aktiv nur mit SMTP + Basis-URL, E-Mail-Adresse und
// aktivem Konto. Der Dialog bleibt nach dem Senden offen (Erfolgsmeldung im Dialog).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const section = { id: 'reset-link', order: 30, titleKey: 'users.sectionResetLink', when: (user) => isLocalAccount(user) }

export default function ResetLinkSection({ user, ctx }) {
    const { t } = useTranslation()
    const state = resetLinkState(user, ctx.resetMailAvailable)

    async function handleSend() {
        if (ctx.busy || !state.enabled) return
        const res = await ctx.run('link', () => api.sendUserResetLink(user.id))
        if (!res) return
        ctx.setInfo(t('users.resetLinkSent', { email: res.email || user.email }))
    }

    return (
        <div className="space-y-3">
            {state.reason === 'no_email' && <p className="text-xs text-text-muted">{t('users.resetLinkUnavailableNoEmail')}</p>}
            {state.reason === 'no_smtp' && <p className="text-xs text-text-muted">{t('users.resetLinkUnavailableNoSmtp')}</p>}
            {state.enabled && <p className="text-xs text-text-muted break-all">{t('users.resetLinkHint', { email: user.email })}</p>}
            <button
                type="button"
                onClick={handleSend}
                disabled={!!ctx.busy || !state.enabled}
                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium border border-border bg-bg-secondary hover:bg-bg-hover text-text-primary disabled:opacity-40 disabled:cursor-not-allowed"
            >
                {ctx.busy === 'link'
                    ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    : <Mail className="w-4 h-4" aria-hidden="true" />}
                {t('users.sendResetLink')}
            </button>
        </div>
    )
}
