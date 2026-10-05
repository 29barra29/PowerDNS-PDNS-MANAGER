import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

// Platzhalter waehrend Seiten-Chunks nachgeladen werden (Suspense-Fallback, F8 B02) oder eine Seite laedt.
export default function PageSpinner({ className = 'h-64' }) {
    const { t } = useTranslation()
    const label = t('pageSpinner.loading')
    return (
        <div className={`flex items-center justify-center ${className}`} role="status" aria-live="polite" aria-label={label}>
            <Loader2 className="w-8 h-8 text-accent animate-spin" aria-hidden="true" />
            <span className="sr-only">{label}</span>
        </div>
    )
}
