import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import api from '../../../api'
import { isLocalAccount } from '../userSecurityModel'

// Abschnitt A "Passwort selbst festlegen" (F3 §2.4). Nur lokale Konten (externe Konten: F10, Abschnitt 50).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const section = { id: 'password', order: 10, titleKey: 'users.sectionSetPassword', when: (user) => isLocalAccount(user) }

const MIN_LENGTH = 8

export default function PasswordSection({ user, ctx }) {
    const { t } = useTranslation()
    const [pw, setPw] = useState('')
    const [mustChange, setMustChange] = useState(true)
    const [revokeAll, setRevokeAll] = useState(false)

    async function handleSubmit(e) {
        e.preventDefault()
        if (ctx.busy || pw.length < MIN_LENGTH) return
        const res = await ctx.run('set', () => api.updateUser(user.id, {
            password: pw,
            must_change_password: mustChange,
            revoke_all_access: revokeAll,
        }))
        if (!res) return
        setPw('')
        ctx.finish(t('users.passwordChangedSuccess', { name: ctx.userName }))
    }

    return (
        <form onSubmit={handleSubmit} className="space-y-3">
            <div>
                <label htmlFor="usm-new-password" className="block text-sm font-medium text-text-secondary mb-1">{t('settings.newPassword')}</label>
                <input
                    id="usm-new-password"
                    type="password"
                    value={pw}
                    onChange={(e) => setPw(e.target.value)}
                    placeholder="••••••••"
                    className="w-full px-3 py-2 text-sm"
                    autoComplete="new-password"
                    minLength={MIN_LENGTH}
                    maxLength={128}
                    required
                    disabled={!!ctx.busy}
                />
                <p className="text-xs text-text-muted mt-1">{t('users.passwordPolicy')}</p>
            </div>
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
            <div className="flex justify-end">
                <button
                    type="submit"
                    disabled={!!ctx.busy || pw.length < MIN_LENGTH}
                    className="px-4 py-2 bg-gradient-to-r from-warning/80 to-orange-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                >
                    {ctx.busy === 'set' && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                    {t('common.save')}
                </button>
            </div>
        </form>
    )
}
