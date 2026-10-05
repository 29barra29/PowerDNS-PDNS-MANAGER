import { AlertCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

// Fehleranzeige INNERHALB eines Modals (F8 §6.3.2, Regel 6.8.1): Seiten-Banner liegen hinter dem Overlay.
// Rendert nichts bei leerer Meldung. `onClose` optional (ohne -> kein Schliessen-Button).
export default function ModalErrorBanner({ message, title, onClose }) {
    const { t } = useTranslation()
    if (!message) return null
    return (
        <div role="alert" className="mb-4 p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3">
            <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
            <div className="flex-1 min-w-0">
                {title && <p className="text-sm font-semibold">{title}</p>}
                <p className={`text-sm break-words whitespace-pre-line${title ? ' mt-1' : ''}`}>{message}</p>
            </div>
            {onClose && (
                <button
                    type="button"
                    onClick={onClose}
                    className="text-xs hover:underline shrink-0"
                    aria-label={t('common.close')}
                >
                    ×
                </button>
            )}
        </div>
    )
}
