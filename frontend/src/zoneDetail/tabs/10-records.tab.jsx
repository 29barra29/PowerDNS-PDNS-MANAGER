import { List } from 'lucide-react'
import ZoneDnssecSection from '../ZoneDnssecSection'
import RecordsTable from '../RecordsTable'

// Tab "Records" (Standard-Tab, URL ohne ?tab=): DNSSEC-Karte, Leer-Zustand und Record-Tabellen wie 2.4.1.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'records', order: 10, labelKey: 'zoneDetail.tabRecords', icon: List }

export default function RecordsTab() {
    return (
        <div className="space-y-6">
            <ZoneDnssecSection />
            <RecordsTable />
        </div>
    )
}
