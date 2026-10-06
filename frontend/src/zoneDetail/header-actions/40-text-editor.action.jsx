import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { FileText } from 'lucide-react'
import BulkEditorModal from '../../components/bulk/BulkEditorModal'
import { EMPTY_SELECTION, appliedBanners, nextModalId } from '../bulkModel.js'
import { useZoneSlotState } from '../zoneDetailContext'

// Kopf-Aktion "Text-Editor" (F1 2.4, Plan B.14) und Host des Bulk-Editor-Modals. Das Modal wird ueber den geteilten
// Slot-Zustand geoeffnet (Schluessel 'bulk.modal' = { id, flow, step, ... } | null), damit auch die Aktionsleiste
// der Record-Tabelle (RecordsTable.jsx) es oeffnen kann, ohne die Shell (ZoneDetailPage.jsx) zu aendern.
// Die Auswahl der Tabelle liegt unter 'bulk.selected' (Set von recordKey) und wird nach dem Anwenden geleert.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const action = { id: 'text-editor', order: 40, when: (ctx) => !!ctx.canEdit }

export default function TextEditorAction({ ctx }) {
    const { t } = useTranslation()
    const [modal, setModal] = useZoneSlotState('bulk.modal', null)
    const [, setSelected] = useZoneSlotState('bulk.selected', EMPTY_SELECTION)
    const { server, zoneId, zoneKey, records, setError, setSuccess, setWarning, loadZone } = ctx

    const close = useCallback(() => setModal(null), [setModal])

    function openEmpty() {
        setModal({ id: nextModalId(), flow: 'text', step: 'text', text: '', mode: 'merge', scope: [], defaultTtl: 3600 })
    }

    function handleApplied(res) {
        // Ergebnis inkl. PTR je IP mit Begruendung (F11 §2.6), Texte aus bulkModel.appliedBanners
        const banners = appliedBanners(t, res?.details)
        setModal(null)
        setSelected(EMPTY_SELECTION)
        setSuccess(banners.success)
        setError(banners.error)
        setWarning(banners.warning)
        loadZone({ silent: true })
    }

    return (
        <>
            <button
                type="button"
                onClick={openEmpty}
                title={t('bulk.openTextEditorTitle')}
                className="self-start sm:self-auto order-2 flex items-center justify-center gap-2 px-4 py-2.5 border border-border bg-bg-secondary hover:bg-bg-hover text-text-primary rounded-lg font-medium text-sm transition-all"
            >
                <FileText className="w-4 h-4 shrink-0" aria-hidden="true" />
                {t('bulk.openTextEditor')}
            </button>
            {modal && (
                <BulkEditorModal
                    key={modal.id}
                    server={server}
                    zoneId={zoneId}
                    zoneKey={zoneKey}
                    records={records}
                    initial={modal}
                    onClose={close}
                    onApplied={handleApplied}
                />
            )}
        </>
    )
}
