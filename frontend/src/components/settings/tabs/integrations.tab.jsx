import { Link2 } from 'lucide-react'
import { collectSlots, useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'integrations', order: 20, labelKey: 'settings.integrationsTab', icon: Link2, adminOnly: false }

// Karten des Tabs "API & Sicherheit" (fuer alle angemeldeten Benutzer). Jede Karte ist eine Datei
// integrations-cards/NN-<name>.card.jsx mit
//   export const card = { id, order, adminOnly? }
//   export default function XyzCard() { ... }   // Daten/Fehler verwaltet jede Karte selbst
// Belegt: 10-totp, 20-passkeys, 30-panel-tokens, 40-webhooks. Vorgesehen: 05-sso-link (F10), 50-dyndns (F9).
const CARDS = collectSlots(import.meta.glob('../integrations-cards/*.card.jsx', { eager: true }), 'card')

export default function IntegrationsTab() {
    const { isAdmin } = useSettings()
    return (
        <div className="space-y-6">
            {CARDS.filter((c) => isAdmin || !c.adminOnly).map(({ id, Component }) => <Component key={id} />)}
        </div>
    )
}
