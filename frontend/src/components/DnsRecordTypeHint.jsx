import { useTranslation } from 'react-i18next'

/**
 * Zeigt Format-Hinweis & Beispiel für einen DNS-Record-Typ (Zone + Vorlagen).
 */
export default function DnsRecordTypeHint({ recordType, compact = false }) {
    const { t } = useTranslation()
    if (!recordType) return null
    const text = t(`zoneDetail.hint${recordType}`, { defaultValue: '' })
    if (!text) return null
    return (
        <div
            className={
                compact
                    ? 'mt-2 rounded-md border border-border bg-bg-primary/80 px-2.5 py-2 text-[11px] leading-relaxed text-text-muted'
                    : 'mt-3 rounded-lg border border-accent/20 bg-accent/5 p-3 text-xs leading-relaxed text-text-secondary'
            }
        >
            <p className={`font-medium text-accent-light ${compact ? 'mb-0.5 text-[11px]' : 'mb-1.5'}`}>
                {t('zoneDetail.hintTitle')}
            </p>
            <p className={`whitespace-pre-line ${compact ? 'text-text-muted' : ''}`}>{text}</p>
        </div>
    )
}
