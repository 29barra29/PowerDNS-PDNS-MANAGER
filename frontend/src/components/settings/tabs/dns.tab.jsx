import { Globe } from 'lucide-react'
import { collectSlots } from '../settingsContext'

// Tab "DNS-Optionen" (nur Admins). Inhalt sind die Karten dns-cards/NN-<name>.card.jsx (Vertrag siehe
// dns-cards/README.md). Solange keine Karte existiert, ist der Tab ausgeblendet (hidden) – Stand Welle 0b.
const CARDS = collectSlots(import.meta.glob('../dns-cards/*.card.jsx', { eager: true }), 'card')

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'dns', order: 35, labelKey: 'settings.dnsTab', icon: Globe, adminOnly: true, hidden: CARDS.length === 0 }

export default function DnsTab() {
    return (
        <div className="space-y-6">
            {CARDS.map(({ id, Component }) => <Component key={id} />)}
        </div>
    )
}
