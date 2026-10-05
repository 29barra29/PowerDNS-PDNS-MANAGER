// Abschnitte des Dialogs "Passwort & Sicherheit" (UserSecurityModal, F2F3) als Slot (Plan B.14).
// Jede Datei `components/userSecurity/sections/NN-<name>.section.jsx`:
//   export const section = { id, order?, titleKey?, when?(user, ctx) }
//   export default function MySection({ user, ctx }) { ... }
// `ctx` legt der Dialog fest (F2F3: z. B. resetMailAvailable, onChanged, setError/setSuccess); Abschnitte lesen nur,
// was sie brauchen. Reihenfolge nach NN: 10-40 F3 (Passwort, Zufallspasswort, Reset-Link, zweiter Faktor),
// 50 F10 (externes Konto), 60 F14 (API-Tokens).
import { collectSlots, slotApplies } from '../../lib/slots.js'

export const USER_SECURITY_SECTIONS = collectSlots(
    import.meta.glob('./sections/*.section.jsx', { eager: true }),
    { exportName: 'section' },
)

// Fuer einen Benutzer sichtbare Abschnitte (when(user, ctx) fehlt -> sichtbar).
export function visibleSecuritySections(user, ctx) {
    return USER_SECURITY_SECTIONS.filter((entry) => slotApplies(entry, [user, ctx]))
}
