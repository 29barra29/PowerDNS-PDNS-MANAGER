import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeftRight, Loader2 } from 'lucide-react'
import api from '../../../api'
import { setDefaultInCache } from '../../../lib/ptrPreference.js'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'ptr', order: 10 }

// Karte "Reverse-DNS (PTR)" im Admin-Tab "DNS-Optionen" (F11 §2.7): Admin-Default der Checkbox
// "PTR mitpflegen" und Standard fuer API-Aufrufe ohne manage_ptr (system_settings.ptr_auto_default).
// Erfolg und Fehler zeigt die Karte selbst an (kein Seiten-Banner).
export default function PtrSettingsCard() {
    const { t } = useTranslation()
    const checkboxId = useId()
    const [loaded, setLoaded] = useState(false)
    const [autoDefault, setAutoDefault] = useState(false)
    const [saved, setSaved] = useState(false) // zuletzt gespeicherter Wert
    const [busy, setBusy] = useState(false)
    const [msg, setMsg] = useState('')
    const [err, setErr] = useState('')

    useEffect(() => {
        const ctrl = new AbortController()
        api.getPtrSettings({ signal: ctrl.signal })
            .then((d) => {
                setAutoDefault(!!d?.auto_default)
                setSaved(!!d?.auto_default)
                setLoaded(true)
            })
            .catch((e) => {
                if (e?.name === 'AbortError') return
                setErr(e.message)
                setLoaded(true)
            })
        return () => ctrl.abort()
    }, [])

    useEffect(() => {
        if (!msg) return undefined
        const timer = setTimeout(() => setMsg(''), 4000)
        return () => clearTimeout(timer)
    }, [msg])

    async function save(e) {
        e.preventDefault()
        if (busy) return
        setBusy(true)
        setErr('')
        setMsg('')
        try {
            const res = await api.updatePtrSettings({ auto_default: !!autoDefault })
            const value = res?.settings ? !!res.settings.auto_default : !!autoDefault
            setAutoDefault(value)
            setSaved(value)
            setDefaultInCache(value) // Record-Dialoge in diesem Browser sehen den neuen Default sofort
            setMsg(t('ptr.settingsSaved'))
        } catch (e2) {
            setErr(e2.message)
        } finally {
            setBusy(false)
        }
    }

    return (
        <form onSubmit={save} className="glass-card p-6 space-y-4">
            <h2 className="text-lg font-bold flex items-center gap-2">
                <ArrowLeftRight className="w-5 h-5" aria-hidden="true" />
                {t('ptr.settingsTitle')}
            </h2>
            <p className="text-sm text-text-muted">{t('ptr.settingsIntro')}</p>
            {err && (
                <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm break-words">{err}</div>
            )}
            {msg && (
                <div role="status" className="p-3 rounded-lg bg-success/10 border border-success/30 text-success text-sm">{msg}</div>
            )}
            {!loaded ? (
                <p className="text-sm text-text-muted flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> {t('common.loading')}
                </p>
            ) : (
                <>
                    <div className="space-y-1">
                        <label htmlFor={checkboxId} className="flex items-start gap-2 cursor-pointer text-sm text-text-secondary">
                            <input
                                id={checkboxId}
                                type="checkbox"
                                checked={autoDefault}
                                onChange={(e) => setAutoDefault(e.target.checked)}
                                className="w-4 h-4 rounded mt-0.5"
                            />
                            <span>{t('ptr.settingsAutoDefault')}</span>
                        </label>
                        <p className="text-xs text-text-muted pl-6">{t('ptr.settingsAutoDefaultHint')}</p>
                    </div>
                    <div className="flex justify-end">
                        <button
                            type="submit"
                            disabled={busy || autoDefault === saved}
                            className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                        >
                            {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                            {t('ptr.settingsSave')}
                        </button>
                    </div>
                </>
            )}
        </form>
    )
}
