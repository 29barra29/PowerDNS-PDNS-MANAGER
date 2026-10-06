import { useTranslation } from 'react-i18next'
import { Shield } from 'lucide-react'
import { DNSSEC_MODAL_KEY } from '../useZoneDnssec'
import { useZoneSlotState } from '../zoneDetailContext'

// Kopf-Aktion "DS / Registrar-Assistent" (F4 §2.3): oeffnet den DS-Dialog, den ZoneDnssecHost
// (zoneDetail/ZoneDnssecSection.jsx) rendert. Daten kommen aus dem DNSSEC-Status (kein eigener Request).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const action = { id: 'dnssec-ds', order: 10 }

export default function DnssecDsAction() {
    const { t } = useTranslation()
    const [, setModal] = useZoneSlotState(DNSSEC_MODAL_KEY, null)
    return (
        <button
            type="button"
            onClick={() => setModal('ds')}
            className="self-start sm:self-auto order-2 sm:order-1 flex items-center justify-center gap-2 px-4 py-2.5 border border-amber-500/40 bg-amber-500/10 hover:bg-amber-500/20 text-amber-200 rounded-lg font-medium text-sm transition-all"
        >
            <Shield className="w-4 h-4 shrink-0" aria-hidden="true" />
            {t('zoneDetail.dnssecModalOpenButton')}
        </button>
    )
}
