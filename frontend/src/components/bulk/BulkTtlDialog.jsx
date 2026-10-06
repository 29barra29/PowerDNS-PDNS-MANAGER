import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Clock, Info, Loader2, X } from 'lucide-react'
import ModalErrorBanner from '../ModalErrorBanner'
import TtlInput from '../TtlInput'
import { TTL_MAX, TTL_MIN, isValidBulkTtl } from '../../zoneDetail/bulkModel.js'
import { useDialogFocus } from '../../lib/useDialogFocus'
import ModalPortal from '../common/ModalPortal'

// TTL fuer die Auswahl setzen (F1 2.2, 6.3.2). Der Aufrufer mountet den Dialog je Oeffnen neu (key), daher
// kein Effekt zum Zuruecksetzen. Die TTL gilt fuer ganze RRsets – der Hinweis nennt die Zahl der nicht gewaehlten
// Werte, die mitgeaendert werden. Fehler des Vorschau-Aufrufs zeigt der Dialog selbst (error).
export default function BulkTtlDialog({ initialTtl, stats, busy, error, onCancel, onConfirm }) {
    const { t } = useTranslation()
    const titleId = useId()
    const [ttl, setTtl] = useState(String(initialTtl ?? 3600))
    const valid = isValidBulkTtl(ttl)

    // Fokus, Tab-Falle, ESC (nicht waehrend der Vorschau) und Fokus-Rueckgabe: lib/useDialogFocus
    const dialogRef = useDialogFocus({ onClose: onCancel, canClose: !busy })

    // Danach (Effekt-Reihenfolge) das TTL-Feld fokussieren; der Hook hat den Ausloeser schon gemerkt.
    useEffect(() => {
        const input = document.getElementById(`${titleId}-ttl`)
        if (input && !input.disabled) input.focus()
    }, [titleId])

    function submit(e) {
        e.preventDefault()
        if (!valid || busy) return
        onConfirm(parseInt(ttl, 10))
    }

    return (
        <ModalPortal>
            <div
                className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
                onClick={() => { if (!busy) onCancel() }}
            >
                <form
                    ref={dialogRef}
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby={titleId}
                    onSubmit={submit}
                    onClick={(e) => e.stopPropagation()}
                    className="glass-card p-6 w-full max-w-md max-h-[90vh] overflow-y-auto"
                >
                    <div className="flex items-center justify-between mb-4 gap-3">
                        <h2 id={titleId} className="text-lg font-bold text-text-primary flex items-center gap-2">
                            <Clock className="w-5 h-5 text-accent-light" aria-hidden="true" />
                            {t('bulk.ttlDialogTitle')}
                        </h2>
                        <button
                            type="button"
                            onClick={onCancel}
                            disabled={busy}
                            className="p-1 rounded-lg hover:bg-bg-hover text-text-muted disabled:opacity-50"
                            aria-label={t('common.close')}
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>

                    <ModalErrorBanner message={error} />

                    <label className="block text-sm font-medium text-text-secondary mb-1" htmlFor={`${titleId}-ttl`}>
                        {t('bulk.ttlLabel')}
                    </label>
                    <TtlInput id={`${titleId}-ttl`} value={ttl} onChange={setTtl} disabled={busy} min={TTL_MIN} max={TTL_MAX} />
                    {!valid && <p className="mt-1 text-xs text-danger" role="alert">{t('bulk.ttlRange')}</p>}

                    <div className="mt-4 p-3 rounded-lg bg-bg-secondary/60 border border-border text-text-secondary text-xs flex items-start gap-2">
                        <Info className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                        <span>{t('bulk.ttlWholeRrset', {
                            rrsets: stats?.rrsets ?? 0, values: stats?.values ?? 0, unselected: stats?.unselected ?? 0,
                        })}</span>
                    </div>

                    <div className="flex justify-end gap-2 mt-6">
                        <button
                            type="button"
                            onClick={onCancel}
                            disabled={busy}
                            className="px-4 py-2 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                        >
                            {t('common.cancel')}
                        </button>
                        <button
                            type="submit"
                            disabled={!valid || busy}
                            className="px-4 py-2 rounded-lg text-sm font-medium bg-accent text-white hover:bg-accent-hover disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center gap-2"
                        >
                            {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                            {t('bulk.continueToPreview')}
                        </button>
                    </div>
                </form>
            </div>
        </ModalPortal>
    )
}
