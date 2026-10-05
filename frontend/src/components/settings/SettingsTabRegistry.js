import { collectSlots } from './settingsContext'

// Tab-Registry der Einstellungsseite (Plan B.14). Jeder Tab ist eine Datei `tabs/<id>.tab.jsx` mit
//   export const tab = { id, order, labelKey, icon, adminOnly, hidden? }
//   export default function XyzTab({ active }) { ... }
// - id        = Dateiname ohne `.tab.jsx`, zugleich Wert fuer den Deep-Link `/settings?tab=<id>`
// - order     = Position in der Tab-Leiste (aufsteigend; Luecken lassen, siehe Tabelle unten)
// - labelKey  = i18n-Key der Beschriftung, icon = lucide-Komponente
// - adminOnly = nur fuer Admins sichtbar; alle anderen Tabs bilden NON_ADMIN_TABS
// - hidden    = optional; true blendet den Tab aus (z. B. dns ohne Karten), auch fuer den Deep-Link
// Die Komponente bleibt nach dem ersten Oeffnen gemountet (Zustand bleibt beim Tab-Wechsel erhalten, wie 2.4.1)
// und bekommt `active` (sichtbar ja/nein), um beim Aktivieren neu zu laden.
//
// Belegte order-Werte: profile 10, integrations 20, servers 30, dns 35, templates 40, smtp 50, welcome 60,
// security 70, acme 80, updates 90, about 100. Vorgesehen: sso 75 (F10), monitoring 85 (F12/F13).
const modules = import.meta.glob('./tabs/*.tab.jsx', { eager: true })

export const SETTINGS_TABS = collectSlots(modules, 'tab').filter((tab) => !tab.hidden)

// Die einzige Stelle, die festlegt, welche Tabs Nicht-Admins sehen (2.4.1: profile, integrations, about).
export const NON_ADMIN_TABS = SETTINGS_TABS.filter((tab) => !tab.adminOnly).map((tab) => tab.id)

export const DEFAULT_TAB = 'profile'

export function getVisibleTabs(isAdmin) {
    return isAdmin ? SETTINGS_TABS : SETTINGS_TABS.filter((tab) => NON_ADMIN_TABS.includes(tab.id))
}

// Liefert die erlaubte Tab-ID fuer einen (evtl. ungueltigen) Wunsch, z. B. aus ?tab=.
export function resolveTabId(requested, isAdmin) {
    const visible = getVisibleTabs(isAdmin)
    if (requested && visible.some((tab) => tab.id === requested)) return requested
    return DEFAULT_TAB
}
