import { collectSlots, slotApplies } from '../../lib/slots.js'

// Badge-Slot der Benutzerliste (Plan B.14). Jede Datei `components/users/badges/NN-<name>.badge.jsx`:
//   export const badge = { id, order?, when?(user, context) }
//   export default function MyBadge({ user, context }) { ... }   // rendert ein einzelnes Badge (oder null)
// Gerendert in pages/UsersPage.jsx hinter Rollen- und "Deaktiviert"-Badge, sortiert nach NN.
// `context` reicht die Seite durch (z. B. eigene Benutzer-ID, SSO-Info); der Inhalt ist Sache der Seite.
// Belegt: 10-security (F2F3), 20-auth-source (F10-APP-FE), 30-token-count (F14-APP).
const BADGES = collectSlots(import.meta.glob('./badges/*.badge.jsx', { eager: true }), { exportName: 'badge' })

export default function UserBadges({ user, context = null }) {
    if (!user) return null
    return BADGES
        .filter((entry) => slotApplies(entry, [user, context]))
        .map((entry) => {
            const Badge = entry.Component
            return <Badge key={entry.id} user={user} context={context} />
        })
}
