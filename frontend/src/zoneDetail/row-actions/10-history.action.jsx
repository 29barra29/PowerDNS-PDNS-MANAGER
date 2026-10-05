import { useTranslation } from 'react-i18next'
import { History } from 'lucide-react'

// Zeilen-Aktion "Verlauf" (F7 §2.2, §6.4 [F4]): oeffnet den Tab "Verlauf" gefiltert auf dieses RRset.
// Fuer alle Nutzer sichtbar (Lesen genuegt). Neuer History-Eintrag (replace: false), damit "Zurueck" funktioniert.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const action = {
    id: 'history',
    order: 10,
    when: (record) => Boolean(record?.name && record?.type),
}

export default function HistoryRowAction({ record, ctx }) {
    const { t } = useTranslation()
    return (
        <button
            type="button"
            onClick={() => ctx.setTab('history', { hname: record.name, htype: record.type }, { replace: false })}
            className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors"
            title={t('zoneDetail.recordHistoryTitle')}
            aria-label={t('zoneDetail.recordHistoryTitle')}
        >
            <History className="w-3.5 h-3.5" />
        </button>
    )
}
