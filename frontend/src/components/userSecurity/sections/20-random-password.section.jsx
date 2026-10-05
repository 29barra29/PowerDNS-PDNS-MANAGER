import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dices, Loader2 } from 'lucide-react'
import api from '../../../api'
import { isLocalAccount } from '../userSecurityModel'

// Abschnitt B "Zufallspasswort erzeugen" (F3 §2.4): Rueckfrage, dann Einmalanzeige im Dialog (OneTimeSecretModal).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const section = { id: 'random-password', order: 20, titleKey: 'users.sectionRandomPassword', when: (user) => isLocalAccount(user) }

export default function RandomPasswordSection({ user, ctx }) {
    const { t } = useTranslation()
    const [mustChange, setMustChange] = useState(true)
    const [revokeAll, setRevokeAll] = useState(false)

    async function handleGenerate() {
        if (ctx.busy) return
        if (!window.confirm(t('users.randomPasswordConfirm', { name: ctx.userName }))) return
        const res = await ctx.run('random', () => api.generateUserPassword(user.id, {
            must_change_password: mustChange,
            revoke_all_access: revokeAll,
        }))
        if (!res?.new_password) return
        ctx.showOneTime({ password: res.new_password, name: ctx.userName })
    }

    return (
        <div className="space-y-3">
            <p className="text-xs text-text-muted">{t('users.randomPasswordHint')}</p>
            <label className="flex items-center gap-2 text-sm text-text-secondary">
                <input type="checkbox" checked={mustChange} onChange={(e) => setMustChange(e.target.checked)} disabled={!!ctx.busy} />
                {t('users.mustChangeOnNextLogin')}
            </label>
            <label className="flex items-start gap-2 text-sm text-text-secondary">
                <input type="checkbox" className="mt-0.5" checked={revokeAll} onChange={(e) => setRevokeAll(e.target.checked)} disabled={!!ctx.busy} />
                <span>
                    {t('users.revokeAllAccess')}
                    <span className="block text-xs text-text-muted">{t('users.revokeAllAccessHint')}</span>
                </span>
            </label>
            <button
                type="button"
                onClick={handleGenerate}
                disabled={!!ctx.busy}
                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium border border-border bg-bg-secondary hover:bg-bg-hover text-text-primary disabled:opacity-50"
            >
                {ctx.busy === 'random'
                    ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    : <Dices className="w-4 h-4" aria-hidden="true" />}
                {t('users.generateButton')}
            </button>
        </div>
    )
}
