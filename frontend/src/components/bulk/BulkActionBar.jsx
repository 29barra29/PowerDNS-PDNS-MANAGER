import { useTranslation } from 'react-i18next'
import { AlertCircle, Clock, Eye, EyeOff, FileText, Loader2, Trash2, X } from 'lucide-react'

// Fixierte Aktionsleiste der Mehrfachauswahl (F1 2.1-2.4, 6.3.1). Fehler des Vorschau-Aufrufs erscheinen hier
// (ein Seitenbanner waere unten nicht sichtbar, ein Modal ist noch nicht offen).
const BTN = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border transition-colors disabled:opacity-40 disabled:cursor-not-allowed'
const SECONDARY = `${BTN} border-border bg-bg-secondary hover:bg-bg-hover text-text-primary`
const DANGER = `${BTN} border-danger/40 text-danger hover:bg-danger/10`

export default function BulkActionBar({
    count, busy, error, onClearError, onDelete, onSetTtl, onDisable, onEnable, onEditText, onClear,
}) {
    const { t } = useTranslation()
    return (
        <div
            role="toolbar"
            aria-label={t('bulk.toolbarLabel')}
            className="fixed bottom-4 inset-x-4 sm:inset-x-auto sm:left-1/2 sm:-translate-x-1/2 z-40 glass-card px-4 py-3 flex flex-col gap-2 shadow-xl max-w-[calc(100vw-2rem)]"
        >
            {error && (
                <div role="alert" className="flex items-start gap-2 p-2 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs">
                    <AlertCircle className="w-4 h-4 shrink-0" aria-hidden="true" />
                    <span className="flex-1 break-words">{error}</span>
                    <button type="button" onClick={onClearError} className="hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-text-primary mr-1 flex items-center gap-2" aria-live="polite">
                    {busy && <Loader2 className="w-4 h-4 animate-spin text-accent" aria-hidden="true" />}
                    {t('bulk.selectedCount', { count })}
                </span>
                <button type="button" onClick={onDelete} disabled={busy} className={DANGER}>
                    <Trash2 className="w-4 h-4" aria-hidden="true" /> {t('bulk.actionDelete')}
                </button>
                <button type="button" onClick={onSetTtl} disabled={busy} className={SECONDARY}>
                    <Clock className="w-4 h-4" aria-hidden="true" /> {t('bulk.actionSetTtl')}
                </button>
                <button type="button" onClick={onDisable} disabled={busy} className={SECONDARY}>
                    <EyeOff className="w-4 h-4" aria-hidden="true" /> {t('bulk.actionDisable')}
                </button>
                <button type="button" onClick={onEnable} disabled={busy} className={SECONDARY}>
                    <Eye className="w-4 h-4" aria-hidden="true" /> {t('bulk.actionEnable')}
                </button>
                <button type="button" onClick={onEditText} disabled={busy} className={SECONDARY}>
                    <FileText className="w-4 h-4" aria-hidden="true" /> {t('bulk.actionEditText')}
                </button>
                <button
                    type="button"
                    onClick={onClear}
                    disabled={busy}
                    className={`${BTN} border-transparent text-text-muted hover:text-text-primary hover:bg-bg-hover`}
                >
                    <X className="w-4 h-4" aria-hidden="true" /> {t('bulk.clearSelection')}
                </button>
            </div>
        </div>
    )
}
