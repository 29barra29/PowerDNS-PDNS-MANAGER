import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, EyeOff, KeyRound, Loader2, LogOut } from 'lucide-react'
import api from '../api'
import LanguageDropdown from './LanguageDropdown'

const MIN_LENGTH = 8

/*
 * Erzwungener Passwortwechsel (F3 §2.6/§6.3). Wird von ProtectedRoute statt des Layouts gerendert, solange
 * `must_change_password` gesetzt ist – es laufen keine Seiten-Requests. Nach Erfolg: getMe() und onDone(user).
 * Fehler erkennt der Dialog am Maschinencode der Antwort (`current_password_wrong`, `password_unchanged`).
 */
export default function ForcePasswordChange({ onDone }) {
    const { t } = useTranslation()
    const [current, setCurrent] = useState('')
    const [next, setNext] = useState('')
    const [confirm, setConfirm] = useState('')
    const [showPw, setShowPw] = useState(false)
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState('')

    const handleSubmit = async (e) => {
        e.preventDefault()
        if (busy) return
        setError('')
        if (next !== confirm) return setError(t('settings.passwordsDoNotMatch'))
        if (next.length < MIN_LENGTH) return setError(t('settings.passwordMinLength'))
        if (next === current) return setError(t('forcePassword.mustDiffer'))
        setBusy(true)
        try {
            await api.changePassword({ current_password: current, new_password: next })
            const user = await api.getMe()
            api.setUser(user)
            onDone?.(user)
        } catch (err) {
            if (err?.name === 'AbortError') return
            // Backend-Codes (routers/auth.py change_password): Text uebersetzt anzeigen, nicht per Regex raten
            if (err?.code === 'password_unchanged') setError(t('forcePassword.mustDiffer'))
            else if (err?.code === 'current_password_wrong') setError(t('forcePassword.currentWrong'))
            else setError(err?.message || t('apiErrors.requestFailed'))
            // Falsches aktuelles Passwort: nur dieses Feld leeren (F3 §2.6 Nr. 5)
            if (err?.code === 'current_password_wrong') setCurrent('')
        } finally {
            setBusy(false)
        }
    }

    const inputType = showPw ? 'text' : 'password'
    const toggleLabel = t('forcePassword.togglePassword')

    return (
        <div className="min-h-screen flex items-center justify-center bg-bg-primary relative overflow-hidden p-4">
            <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-accent/10 rounded-full blur-3xl" />
            <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-600/10 rounded-full blur-3xl" />

            <div className="glass-card p-8 w-full max-w-md relative z-10">
                <div className="absolute top-4 right-4">
                    <LanguageDropdown />
                </div>
                <div className="text-center mb-6">
                    <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center mx-auto mb-4 shadow-lg shadow-accent/20">
                        <KeyRound className="w-8 h-8 text-white" aria-hidden="true" />
                    </div>
                    <h1 className="text-2xl font-bold text-text-primary">{t('forcePassword.title')}</h1>
                    <p className="text-text-muted text-sm mt-2">{t('forcePassword.intro')}</p>
                </div>

                {error && (
                    <div className="mb-4 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm" role="alert">
                        {error}
                    </div>
                )}

                <form onSubmit={handleSubmit} className="space-y-4">
                    <div>
                        <label htmlFor="fpc-current" className="block text-sm font-medium text-text-secondary mb-1.5">
                            {t('settings.currentPassword')}
                        </label>
                        <div className="relative">
                            <input
                                id="fpc-current"
                                type={inputType}
                                value={current}
                                onChange={(e) => setCurrent(e.target.value)}
                                className="w-full px-4 py-2.5 pr-10 text-sm"
                                autoComplete="current-password"
                                autoFocus
                                required
                            />
                            <button
                                type="button"
                                onClick={() => setShowPw((v) => !v)}
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary transition-colors"
                                aria-label={toggleLabel}
                                title={toggleLabel}
                                aria-pressed={showPw}
                            >
                                {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                            </button>
                        </div>
                        <p className="text-xs text-text-muted mt-1">{t('forcePassword.currentHint')}</p>
                    </div>

                    <div>
                        <label htmlFor="fpc-new" className="block text-sm font-medium text-text-secondary mb-1.5">
                            {t('settings.newPassword')}
                        </label>
                        <input
                            id="fpc-new"
                            type={inputType}
                            value={next}
                            onChange={(e) => setNext(e.target.value)}
                            className="w-full px-4 py-2.5 text-sm"
                            autoComplete="new-password"
                            minLength={MIN_LENGTH}
                            maxLength={128}
                            required
                        />
                        <p className="text-xs text-text-muted mt-1">{t('users.passwordPolicy')}</p>
                    </div>

                    <div>
                        <label htmlFor="fpc-confirm" className="block text-sm font-medium text-text-secondary mb-1.5">
                            {t('settings.newPasswordConfirm')}
                        </label>
                        <input
                            id="fpc-confirm"
                            type={inputType}
                            value={confirm}
                            onChange={(e) => setConfirm(e.target.value)}
                            className="w-full px-4 py-2.5 text-sm"
                            autoComplete="new-password"
                            maxLength={128}
                            required
                        />
                    </div>

                    <button
                        type="submit"
                        disabled={busy}
                        className="w-full py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50"
                    >
                        {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                        {t('forcePassword.submit')}
                    </button>
                </form>

                <button
                    type="button"
                    onClick={() => api.logout()}
                    disabled={busy}
                    className="mt-4 w-full py-2 rounded-lg text-sm text-text-muted hover:text-danger hover:bg-danger/10 transition-colors inline-flex items-center justify-center gap-2"
                >
                    <LogOut className="w-4 h-4" aria-hidden="true" />
                    {t('forcePassword.logout')}
                </button>
            </div>
        </div>
    )
}
