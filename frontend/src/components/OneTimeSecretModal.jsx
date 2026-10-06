import { useEffect, useId, useRef, useState } from 'react'
import { AlertTriangle, Check, Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useDialogFocus } from '../lib/useDialogFocus'
import ModalPortal from './common/ModalPortal'

// Einmal-Anzeige eines Geheimnisses (Panel-Token, Webhook-Secret, DynDNS-Token, Zufallspasswort ...),
// Plan B.14 / F8-D12 (f76):
// - liegt ueber anderen Modals (z-[60]); KEIN Schliessen per Overlay-Klick oder ESC. Fokus, Tab-Falle und
//   Fokus-Rueckgabe macht lib/useDialogFocus (ohne onClose: ESC bewirkt nichts). Weil dieser Dialog zuletzt
//   geoeffnet wurde, liegt er oben auf dem Dialog-Stapel des Hooks – darunterliegende Dialoge reagieren dann
//   weder auf ESC noch auf Tab und schliessen also auch nicht.
// - Kopieren wartet auf die Zwischenablage; Erfolg und Fehler werden angezeigt, das Secret bleibt sichtbar
// - geschlossen wird nur ueber den Bestaetigungs-Button (onDone)
// Props: { title, body, secret, children, doneLabel, onDone }. body === undefined -> Standard-Warnung,
// body === null -> kein Text. children erscheinen unter dem Secret (z. B. Nutzungshinweise).
export default function OneTimeSecretModal({ title, body, secret, children, doneLabel, onDone }) {
    const { t } = useTranslation()
    const [copyState, setCopyState] = useState('idle') // idle | copied | failed
    const titleId = useId()
    // Fokus auf "Kopieren" (nicht auf "Fertig"), damit ein versehentliches Enter das Secret nicht wegklickt
    const copyRef = useRef(null)
    const resetTimer = useRef(null)

    const dialogRef = useDialogFocus({ canClose: false, initialFocusRef: copyRef })

    useEffect(() => () => clearTimeout(resetTimer.current), [])

    async function handleCopy() {
        clearTimeout(resetTimer.current)
        try {
            if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
            await navigator.clipboard.writeText(String(secret ?? ''))
            setCopyState('copied')
            resetTimer.current = setTimeout(() => setCopyState('idle'), 3000)
        } catch {
            // Fehler sichtbar machen; das Secret bleibt zum manuellen Kopieren stehen
            setCopyState('failed')
        }
    }

    const bodyText = body === undefined ? t('secretModal.warning') : body

    return (
        <ModalPortal>
            <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
                <div
                    ref={dialogRef}
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby={titleId}
                    className="glass-card p-6 w-full max-w-lg space-y-4"
                >
                    <h2 id={titleId} className="text-lg font-bold text-text-primary">{title}</h2>
                    {bodyText && (
                        <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-sm text-amber-200">
                            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                            <p className="break-words">{bodyText}</p>
                        </div>
                    )}
                    <div className="space-y-2">
                        <code
                            className="block break-all select-all font-mono text-xs bg-bg-primary/80 border border-border p-3 rounded-lg text-text-primary"
                            aria-label={t('secretModal.secretLabel')}
                        >
                            {secret}
                        </code>
                        <div className="flex flex-wrap items-center gap-3">
                            <button
                                ref={copyRef}
                                type="button"
                                onClick={handleCopy}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-accent/20 text-accent-light hover:bg-accent/30 transition-colors"
                            >
                                {copyState === 'copied'
                                    ? <Check className="w-4 h-4" aria-hidden="true" />
                                    : <Copy className="w-4 h-4" aria-hidden="true" />}
                                {t('secretModal.copy')}
                            </button>
                            <span aria-live="polite" className="text-xs">
                                {copyState === 'copied' && <span className="text-success">{t('secretModal.copied')}</span>}
                                {copyState === 'failed' && <span className="text-danger">{t('secretModal.copyFailed')}</span>}
                            </span>
                        </div>
                    </div>
                    {children}
                    <div className="flex justify-end pt-2">
                        <button
                            type="button"
                            onClick={onDone}
                            className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium"
                        >
                            {doneLabel || t('secretModal.done')}
                        </button>
                    </div>
                </div>
            </div>
        </ModalPortal>
    )
}
