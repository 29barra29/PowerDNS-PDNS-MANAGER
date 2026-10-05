import { createContext, useContext } from 'react'

// Gemeinsamer Zustand der Einstellungsseite (Plan B.14, W0-INT-FE1b).
// Die Shell `pages/SettingsPage.jsx` stellt den Wert bereit, Tabs und Karten lesen ihn mit `useSettings()`.
//
// Wert:
//   profile        – Ergebnis von api.getMe() (null bis geladen; die Shell rendert Tabs erst danach)
//   setProfile     – Profil im Zustand ersetzen (z. B. nach dem Speichern, ohne neuen Request)
//   isAdmin        – profile?.role === 'admin'
//   adminInfo      – Ergebnis von api.getAdminInfo() fuer Admins (install_path, app_base_url), sonst null
//   reloadProfile  – async () => profile; laedt api.getMe() neu und setzt den Zustand
//   notify         – Seiten-Banner: notify.error(msg), notify.success(msg) (Erfolg verschwindet nach 4 s),
//                    notify.clear(); leerer Text blendet das jeweilige Banner aus
//   activeTab      – ID des sichtbaren Tabs
//   setActiveTab   – (id) => void; schreibt auch den Deep-Link ?tab=<id>
export const SettingsContext = createContext(null)

export function useSettings() {
    const ctx = useContext(SettingsContext)
    if (!ctx) {
        throw new Error('useSettings muss innerhalb von SettingsPage verwendet werden')
    }
    return ctx
}

// Slot-Module (Ergebnis von import.meta.glob(..., { eager: true })) in eine sortierte Liste umwandeln.
// Jedes Modul exportiert `export const <exportName> = { id, order, ... }` und eine Default-Komponente.
// Ein kaputter Slot (fehlender Export, doppelte ID) wird geloggt und uebersprungen, damit nicht die ganze
// Seite ausfaellt; der Vertragstest frontend/tests/settings-slots.test.mjs faengt solche Fehler vorher ab.
export function collectSlots(modules, exportName) {
    const seen = new Set()
    const out = []
    for (const [file, mod] of Object.entries(modules)) {
        const meta = mod?.[exportName]
        const Component = mod?.default
        if (!meta || typeof meta.id !== 'string' || !meta.id || typeof Component !== 'function') {
            console.error(`[settings] Slot ${file} ohne gueltiges "${exportName}"/Default-Export – ignoriert`)
            continue
        }
        if (seen.has(meta.id)) {
            console.error(`[settings] Slot-ID "${meta.id}" doppelt (${file}) – ignoriert`)
            continue
        }
        seen.add(meta.id)
        out.push({ ...meta, order: Number.isFinite(meta.order) ? meta.order : 1000, Component, file })
    }
    out.sort((a, b) => (a.order - b.order) || a.id.localeCompare(b.id))
    return out
}
