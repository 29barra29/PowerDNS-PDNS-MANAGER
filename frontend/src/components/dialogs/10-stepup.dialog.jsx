import { useEffect, useState, useSyncExternalStore } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Loader2, LogIn, ShieldCheck } from 'lucide-react'
import api, { STEP_UP_EVENT } from '../../api'
import {
    cancelStepUp, getStepUpState, openStepUp, saveReturnPath, submitStepUp, subscribeStepUp, takeReturnPath,
} from '../sso/stepUpStore'
import { useDialogFocus } from '../../lib/useDialogFocus'
import ModalPortal from '../common/ModalPortal'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (components/dialogs/Dialogs.jsx)
export const dialog = { id: 'stepup' }

function sessionStore() {
    try {
        return typeof window !== 'undefined' ? window.sessionStorage : null
    } catch {
        return null
    }
}

/*
 * Step-up-Dialog (Plan B.3/B.14 [S8], WS-F10-APP-FE). Dauerhaft gemountet (Slot components/dialogs), horcht auf
 * STEP_UP_EVENT aus api.js und auf Anfragen ueber components/sso/stepUpStore (withStepUp).
 *   - lokales Konto: aktuelles Passwort (+ 2FA-Code, wenn TOTP aktiv) -> Aufrufer wiederholt die Aktion
 *   - externes Konto (SSO/LDAP) bzw. Code reauth_required: Abmelden und erneut anmelden; die aktuelle Seite wird
 *     gemerkt (sessionStorage, 15 min) und nach der Anmeldung wieder geoeffnet.
 */
export default function StepUpDialog() {
    const navigate = useNavigate()
    const st = useSyncExternalStore(subscribeStepUp, getStepUpState, getStepUpState)

    // Ereignis aus api.js -> Dialog oeffnen
    useEffect(() => {
        function onStepUp(e) {
            openStepUp(e?.detail || {})
        }
        window.addEventListener(STEP_UP_EVENT, onStepUp)
        return () => window.removeEventListener(STEP_UP_EVENT, onStepUp)
    }, [])

    // Rueckkehr nach erneuter Anmeldung (nur, wenn der Eintrag frisch ist)
    useEffect(() => {
        const path = takeReturnPath(sessionStore())
        if (path && path !== `${window.location.pathname}${window.location.search}`) navigate(path, { replace: true })
    }, [navigate])

    if (!st.open) return null
    const user = api.getUser()
    const external = (user?.auth_source || 'local') !== 'local'
    const reauth = st.code === 'reauth_required' || external
    return reauth
        ? <ReauthPanel key={st.seq} />
        : <PasswordPanel key={st.seq} error={st.error} needTotp={user ? !!user.totp_enabled : null} />
}

function Frame({ titleId, title, icon: Icon, onCancel, children }) {
    // Fokus (Element mit data-autofocus), Tab-Falle, ESC = Abbrechen, Fokus-Rueckgabe an das ausloesende Element.
    // Der Dialog liegt ueber anderen Dialogen (z-[70]); der Hook-Stapel sorgt dafuer, dass ESC nur ihn schliesst.
    const dialogRef = useDialogFocus({ onClose: onCancel })

    return (
        <ModalPortal>
            <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
                <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} className="glass-card p-6 w-full max-w-md space-y-4">
                    <div className="flex items-start gap-3">
                        <div className="w-10 h-10 rounded-xl bg-warning/20 flex items-center justify-center shrink-0">
                            <Icon className="w-5 h-5 text-warning" aria-hidden="true" />
                        </div>
                        <h2 id={titleId} className="text-lg font-semibold text-text-primary min-w-0 pt-1.5">{title}</h2>
                    </div>
                    {children}
                </div>
            </div>
        </ModalPortal>
    )
}

function PasswordPanel({ error, needTotp }) {
    const { t } = useTranslation()
    const [pw, setPw] = useState('')
    const [code, setCode] = useState('')
    // Das Code-Feld ist immer da (der Benutzer-Cache kann veraltet sein, z. B. 2FA erst in dieser Sitzung
    // eingerichtet); Pflicht nur, wenn 2FA laut Profil aktiv ist.
    const showTotp = true
    const totpRequired = needTotp === true
    const canSubmit = pw.length > 0 && (!totpRequired || code.length >= 6)

    function handleSubmit(e) {
        e.preventDefault()
        if (!canSubmit) return
        submitStepUp({ current_password: pw, totp_code: code })
        setPw('')
        setCode('')
    }

    return (
        <Frame titleId="stepup-title" title={t('stepUp.title')} icon={ShieldCheck} onCancel={cancelStepUp}>
            <p className="text-sm text-text-muted">{totpRequired ? t('stepUp.bodyTotp') : t('stepUp.body')}</p>
            {error && (
                <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">{error}</div>
            )}
            <form onSubmit={handleSubmit} className="space-y-3">
                <div>
                    <label htmlFor="stepup-password" className="block text-sm font-medium text-text-secondary mb-1">{t('settings.currentPassword')}</label>
                    <input
                        id="stepup-password"
                        type="password"
                        value={pw}
                        onChange={(e) => setPw(e.target.value)}
                        className="w-full px-3 py-2 text-sm"
                        autoComplete="current-password"
                        maxLength={128}
                        data-autofocus
                        required
                    />
                </div>
                {showTotp && (
                    <div>
                        <label htmlFor="stepup-totp" className="block text-sm font-medium text-text-secondary mb-1">
                            {t('auth.totpCode')}{totpRequired ? '' : ` ${t('stepUp.totpOptional')}`}
                        </label>
                        <input
                            id="stepup-totp"
                            type="text"
                            inputMode="numeric"
                            autoComplete="one-time-code"
                            value={code}
                            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                            className="w-full px-3 py-2 text-sm font-mono tracking-widest"
                            placeholder="123456"
                            maxLength={8}
                            required={totpRequired}
                        />
                    </div>
                )}
                <div className="flex justify-end gap-2 pt-2">
                    <button type="button" onClick={cancelStepUp} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary">
                        {t('common.cancel')}
                    </button>
                    <button
                        type="submit"
                        disabled={!canSubmit}
                        className="px-4 py-2 rounded-lg bg-gradient-to-r from-accent to-purple-600 text-white text-sm font-medium disabled:opacity-50"
                    >
                        {t('stepUp.confirm')}
                    </button>
                </div>
            </form>
        </Frame>
    )
}

function ReauthPanel() {
    const { t } = useTranslation()
    const [busy, setBusy] = useState(false)

    async function handleReauth() {
        setBusy(true)
        saveReturnPath(sessionStore(), `${window.location.pathname}${window.location.search}`)
        cancelStepUp()
        await api.logout()
    }

    return (
        <Frame titleId="stepup-reauth-title" title={t('stepUp.reauthTitle')} icon={LogIn} onCancel={cancelStepUp}>
            <p className="text-sm text-text-muted">{t('stepUp.reauthBody')}</p>
            <div className="flex justify-end gap-2 pt-2">
                <button type="button" onClick={cancelStepUp} disabled={busy} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">
                    {t('common.cancel')}
                </button>
                <button
                    type="button"
                    onClick={handleReauth}
                    disabled={busy}
                    data-autofocus
                    className="px-4 py-2 rounded-lg bg-gradient-to-r from-accent to-purple-600 text-white text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                >
                    {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <LogIn className="w-4 h-4" aria-hidden="true" />}
                    {t('stepUp.reauthButton')}
                </button>
            </div>
        </Frame>
    )
}
