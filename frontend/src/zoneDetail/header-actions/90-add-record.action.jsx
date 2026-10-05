import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'

// Kopf-Aktion "Record hinzufuegen" (2.4.1); in allen Tabs sichtbar (F12 §6.3).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const action = { id: 'add-record', order: 90 }

export default function AddRecordAction({ ctx }) {
    const { t } = useTranslation()
    return (
        <button
            type="button"
            onClick={ctx.openAdd}
            disabled={!ctx.canEdit}
            className="self-start sm:self-auto order-1 sm:order-2 flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed disabled:pointer-events-none"
        >
            <Plus className="w-4 h-4 shrink-0" /> {t('zoneDetail.addRecord')}
        </button>
    )
}
