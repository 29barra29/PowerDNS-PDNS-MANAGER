import { createContext, useCallback, useContext } from 'react'

// Context der Zonenansicht (Plan B.14). Die Shell `pages/ZoneDetailPage.jsx` fuellt ihn mit dem Ergebnis von
// `useZoneData()` plus Tab-Steuerung und Slot-Listen; Tabs, Kopf-/Zeilen-Aktionen, Formular-Erweiterungen und
// Wert-Renderer lesen ihn ueber `useZoneDetail()` (oder bekommen ihn als Prop `ctx`).
//
// Inhalt (stabile Schnittstelle fuer alle Slots):
//   Identitaet   server, zoneId, zoneName (ohne Punkt), zoneKey (klein, mit Punkt)
//   Daten        records, zoneMeta, loading, zoneLoadSeq (Zaehler erfolgreicher Ladevorgaenge)
//                me, isAdmin, allServers, currentServerInfo, otherWritableServers
//                userCanEdit, serverCanWrite, canEdit
//   Banner       error/setError, success/setSuccess (4 s Auto-Hide), warning/setWarning (gelb, bleibt stehen)
//                - Setter akzeptieren auch Updater-Funktionen (prev => next), z. B. zum Anhaengen.
//   Laden        loadZone({ silent }) -> Promise<event>; subscribeZoneLoaded(cb) -> unsubscribe
//                event = { seq, ok, silent, records, zoneMeta, error }; cb laeuft nach dem Rendern des neuen
//                Stands (auch fuer Abonnenten, die im selben Render-Durchlauf erst gemountet wurden).
//   Records      openAdd(), openEdit(record), openClone(record), closeRecordForm(), recordForm
//                handleDelete(record, extra?) -> Promise<res|null>  (extra = zusaetzliche Body-Felder)
//                reportFanout(details) -> { errors, warnings, hasErrors, hasWarnings } (setzt Fehler/Warnung)
//                resolveName(name) -> FQDN mit Punkt (klein, normalisiert wie der Record-Dialog); '' bei ungueltigem
//                Namen (Fehlertext zeigt der Dialog), relativeName(fqdn) -> '@' bzw. relativer Name
//   Tabs         tabs, activeTab, setTab(id, params?, { replace }?), searchParams, setSearchParams
//   Slots        slots = { tabs, headerActions, rowActions, formExtensions, valueRenderers }
//   Slot-State   slotState, setSlotState(key, valueOrUpdater) bzw. Hook useZoneSlotState(key, initial)
//                - geteilter Zustand zwischen Slots (z. B. Kopf-Aktion oeffnet einen Dialog eines Abschnitts);
//                  Schluessel mit Praefix des Workstreams, z. B. 'dnssec.dsModal'. Wird beim Zonenwechsel geleert.
export const ZoneDetailContext = createContext(null)

export function useZoneDetail() {
    const ctx = useContext(ZoneDetailContext)
    if (!ctx) throw new Error('useZoneDetail() ausserhalb von ZoneDetailPage')
    return ctx
}

// Geteilter Slot-Zustand: [wert, setWert]. `initial` gilt, solange niemand einen Wert gesetzt hat.
export function useZoneSlotState(key, initial) {
    const { slotState, setSlotState } = useZoneDetail()
    const value = Object.prototype.hasOwnProperty.call(slotState, key) ? slotState[key] : initial
    const set = useCallback(
        (next) => setSlotState(key, (prev) => (typeof next === 'function' ? next(prev === undefined ? initial : prev) : next)),
        [key, initial, setSlotState],
    )
    return [value, set]
}
