import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Check, Copy, Info, X } from 'lucide-react'
import { useDialogFocus } from '../../lib/useDialogFocus'

// Gemeinsame Bausteine der DNSSEC-Dialoge (Plan WS-F4-B): Rahmen mit Fokusfuehrung, Kopier-Knopf,
// Schalter "Serial erhoehen + NOTIFY" [D10] und Anzeige des Ergebnisses (serial_bumped/notified/notify_error).

/**
 * Dialog-Rahmen (z-50): Overlay-Klick und ESC schliessen nur, wenn nicht `busy`; Strg/Cmd+Enter ruft `onSubmit`
 * (falls gesetzt und nicht busy). Fokus (lib/useDialogFocus): beim Oeffnen auf das Element mit `data-autofocus`,
 * sonst das erste Bedienelement; Tab bleibt im Dialog, beim Schliessen zurueck auf das zuvor fokussierte Element.
 */
export default function DnssecDialog({ title, icon: Icon, onClose, onSubmit, busy = false, wide = false, children }) {
    const { t } = useTranslation()
    const titleId = useId()
    const boxRef = useDialogFocus({ onClose, canClose: !busy })
    const latest = useRef({ onSubmit, busy })

    useEffect(() => {
        latest.current = { onSubmit, busy }
    })

    // Strg/Cmd+Enter ruft die Primaeraktion (ESC, Tab-Falle und Fokus uebernimmt der Hook)
    useEffect(() => {
        function onKey(e) {
            const cur = latest.current
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && cur.onSubmit && !cur.busy) {
                e.preventDefault()
                cur.onSubmit()
            }
        }
        document.addEventListener('keydown', onKey)
        return () => document.removeEventListener('keydown', onKey)
    }, [])

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={() => { if (!busy) onClose?.() }}
        >
            <div
                ref={boxRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                tabIndex={-1}
                className={`glass-card p-5 sm:p-6 w-full ${wide ? 'max-w-3xl' : 'max-w-2xl'} max-h-[90vh] overflow-y-auto shadow-2xl outline-none`}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-start justify-between gap-3 mb-4">
                    <h2 id={titleId} className="text-lg font-bold text-text-primary flex items-center gap-2 leading-snug break-words min-w-0">
                        {Icon && <Icon className="w-5 h-5 text-accent-light shrink-0" aria-hidden="true" />}
                        <span className="min-w-0">{title}</span>
                    </h2>
                    <button
                        type="button"
                        onClick={() => { if (!busy) onClose?.() }}
                        disabled={busy}
                        className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary shrink-0 disabled:opacity-50"
                        aria-label={t('common.close')}
                        title={t('common.close')}
                    >
                        <X className="w-5 h-5" />
                    </button>
                </div>
                {children}
            </div>
        </div>
    )
}

/** Fuss mit Tastatur-Hinweis, Abbrechen und Primaeraktion (Kinder = Primaerknopf). */
export function DnssecDialogFooter({ onCancel, busy, children, cancelLabel }) {
    const { t } = useTranslation()
    return (
        <div className="flex flex-col-reverse sm:flex-row sm:justify-between sm:items-center gap-3 pt-4 mt-4 border-t border-border">
            <p className="text-xs text-text-muted hidden sm:block">{t('zoneDetail.kbdHint')}</p>
            <div className="flex flex-wrap justify-end gap-2">
                <button
                    type="button"
                    onClick={onCancel}
                    disabled={busy}
                    className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
                >
                    {cancelLabel || t('common.cancel')}
                </button>
                {children}
            </div>
        </div>
    )
}

/**
 * Kopier-Knopf: `navigator.clipboard.writeText` mit await; Ergebnis an `onResult(ok, label)` (Meldung zeigt der
 * Dialog). Ohne Clipboard-API (http, alte Browser) -> ok=false.
 */
export function CopyButton({ value, label, onResult, text, className = '' }) {
    const { t } = useTranslation()
    const [done, setDone] = useState(false)
    useEffect(() => {
        if (!done) return undefined
        const timer = setTimeout(() => setDone(false), 1500)
        return () => clearTimeout(timer)
    }, [done])

    async function copy() {
        try {
            if (!navigator?.clipboard?.writeText) throw new Error('clipboard')
            await navigator.clipboard.writeText(String(value ?? ''))
            setDone(true)
            onResult?.(true, label)
        } catch {
            onResult?.(false, label)
        }
    }

    return (
        <button
            type="button"
            onClick={copy}
            disabled={value == null || value === ''}
            className={`inline-flex items-center gap-1.5 shrink-0 rounded text-accent-light hover:bg-accent/15 disabled:opacity-40 ${text ? 'px-2 py-1 text-xs font-medium' : 'p-1.5'} ${className}`}
            title={label ? `${t('common.copy')}: ${label}` : t('common.copy')}
            aria-label={label ? `${t('common.copy')}: ${label}` : t('common.copy')}
        >
            {done ? <Check className="w-3.5 h-3.5" aria-hidden="true" /> : <Copy className="w-3.5 h-3.5" aria-hidden="true" />}
            {text}
        </button>
    )
}

/** Schalter "Serial erhoehen und NOTIFY senden" – nur bei Zonen vom Typ Master/Producer sichtbar [D10]. */
export function SerialBumpOption({ visible, checked, onChange, disabled }) {
    const { t } = useTranslation()
    if (!visible) return null
    return (
        <label className="flex items-start gap-2 text-sm text-text-secondary cursor-pointer">
            <input
                type="checkbox"
                className="w-4 h-4 rounded mt-0.5"
                checked={!!checked}
                disabled={disabled}
                onChange={(e) => onChange(e.target.checked)}
            />
            <span>
                {t('dnssec.bumpSerialLabel')}
                <span className="block text-xs text-text-muted mt-0.5">{t('dnssec.bumpSerialHint')}</span>
            </span>
        </label>
    )
}

/** Ergebnis der Serial-Erhoehung/NOTIFY: Info-Zeilen neutral, Fehler gelb (blockieren nicht). */
export function FollowUpNotes({ info = [], warnings = [] }) {
    if (!info.length && !warnings.length) return null
    return (
        <div className="space-y-1.5" role="status">
            {info.map((line) => (
                <p key={line} className="text-xs text-text-secondary flex items-start gap-1.5">
                    <Info className="w-3.5 h-3.5 shrink-0 mt-0.5 text-accent-light" aria-hidden="true" />
                    {line}
                </p>
            ))}
            {warnings.map((line) => (
                <p key={line} className="text-xs text-warning flex items-start gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                    {line}
                </p>
            ))}
        </div>
    )
}
