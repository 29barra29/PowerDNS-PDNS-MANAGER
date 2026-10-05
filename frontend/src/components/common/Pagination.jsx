import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'

// Blaettern ueber offset/limit-Listen (Plan B.14; Audit-Log, Zonenverlauf, Webhook-Zustellprotokoll).
// onPage(newOffset) bekommt den neuen Offset; die Seitengroessen-Auswahl erscheint nur mit pageSizes + onPageSize
// (der Aufrufer setzt dann den Offset selbst zurueck).
export default function Pagination({ offset = 0, limit, total = 0, onPage, pageSizes, onPageSize }) {
    const { t } = useTranslation()
    const size = Math.max(1, Number(limit) || 1)
    const count = Math.max(0, Number(total) || 0)
    const start = Math.max(0, Number(offset) || 0)
    const from = count === 0 ? 0 : Math.min(start + 1, count)
    const to = Math.min(start + size, count)
    const canPrev = start > 0
    const canNext = start + size < count
    const btn = 'inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover hover:text-text-primary disabled:opacity-40 disabled:pointer-events-none transition-colors'

    return (
        <nav className="flex flex-wrap items-center justify-between gap-3 text-sm" aria-label={t('common.pageRange', { from, to, total: count })}>
            <span className="text-text-muted" aria-live="polite">{t('common.pageRange', { from, to, total: count })}</span>
            <div className="flex items-center gap-2">
                {Array.isArray(pageSizes) && pageSizes.length > 0 && onPageSize && (
                    <label className="flex items-center gap-2 text-text-muted">
                        <span>{t('common.pageSize')}</span>
                        <select
                            value={String(size)}
                            onChange={(e) => onPageSize(Number(e.target.value))}
                            className="h-8 px-2 text-sm rounded-lg border border-border bg-bg-primary text-text-primary"
                        >
                            {pageSizes.map((s) => <option key={s} value={String(s)}>{s}</option>)}
                        </select>
                    </label>
                )}
                <button type="button" className={btn} disabled={!canPrev} onClick={() => onPage(Math.max(0, start - size))}>
                    <ChevronLeft className="w-4 h-4" aria-hidden="true" /> {t('common.prev')}
                </button>
                <button type="button" className={btn} disabled={!canNext} onClick={() => onPage(start + size)}>
                    {t('common.next')} <ChevronRight className="w-4 h-4" aria-hidden="true" />
                </button>
            </div>
        </nav>
    )
}
