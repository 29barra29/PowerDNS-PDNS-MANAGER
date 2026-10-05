import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, Loader2 } from 'lucide-react'
import api from '../../api'

// Kopf-Aktion "Export" (F2 §2.1): Zone als BIND-Zonendatei herunterladen. Immer sichtbar und aktiv – auch fuer
// Leser und auf Servern mit "Speichern: Nein" (Export ist Lesen); das Backend prueft die Zonenrechte.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const action = { id: 'export', order: 20 }

export default function ExportAction({ ctx }) {
    const { t } = useTranslation()
    const [exporting, setExporting] = useState(false)

    async function handleExport() {
        if (exporting) return
        setExporting(true)
        try {
            const { filename } = await api.downloadZoneExport(ctx.server, ctx.zoneId)
            ctx.setSuccess(t('zoneDetail.exportSuccess', { file: filename, server: ctx.server }))
        } catch (err) {
            if (err?.name === 'AbortError') return
            ctx.setError(err?.code === 'EMPTY_EXPORT' ? t('zoneDetail.exportEmpty') : err.message)
        } finally {
            setExporting(false)
        }
    }

    return (
        <button
            type="button"
            onClick={handleExport}
            disabled={exporting}
            title={t('zoneDetail.exportTitle')}
            className="self-start sm:self-auto order-2 flex items-center justify-center gap-2 px-4 py-2.5 border border-border bg-bg-secondary hover:bg-bg-hover text-text-primary rounded-lg font-medium text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed"
        >
            {exporting
                ? <Loader2 className="w-4 h-4 shrink-0 animate-spin" aria-hidden="true" />
                : <Download className="w-4 h-4 shrink-0" aria-hidden="true" />}
            {t('zoneDetail.exportButton')}
        </button>
    )
}
