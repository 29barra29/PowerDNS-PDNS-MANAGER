import { useTranslation } from 'react-i18next'
import { Shield } from 'lucide-react'
import { useZoneSlotState } from '../zoneDetailContext'

// Kopf-Aktion "DS / Registrar-Assistent" (2.4.1). Oeffnet das DS-Modal, das ZoneDnssecHost
// (ZoneDnssecSection.jsx) rendert; F4-B ersetzt beide Dateien.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const action = { id: 'dnssec-ds', order: 10 }

export default function DnssecDsAction() {
    const { t } = useTranslation()
    const [, setShowDsModal] = useZoneSlotState('dnssec.dsModal', false)
    return (
        <button
            type="button"
            onClick={() => setShowDsModal(true)}
            className="self-start sm:self-auto order-2 sm:order-1 flex items-center justify-center gap-2 px-4 py-2.5 border border-amber-500/40 bg-amber-500/10 hover:bg-amber-500/20 text-amber-200 rounded-lg font-medium text-sm transition-all"
        >
            <Shield className="w-4 h-4 shrink-0" />
            {t('zoneDetail.dnssecModalOpenButton')}
        </button>
    )
}
