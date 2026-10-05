import { useCallback, useEffect } from 'react'
import api from '../api'
import { mergeStatusKeepPeers } from './dnssecModel.js'
import { useZoneDetail } from './zoneDetailContext'

// DNSSEC-Status der Zonenansicht (F4 §6.1, Plan WS-F4-B [F4]).
//
// Der Status liegt im geteilten Slot-State (Schluessel 'dnssec.status'), damit Karte (Records-Tab), Kopf-Aktion
// und die immer gemounteten Dialoge (ZoneDnssecHost) denselben Stand sehen. Geladen wird
//   - nach jedem loadZone() ueber subscribeZoneLoaded (useZoneDnssecAutoload, einmal im Host gemountet) und
//   - nach Aktionen per reload({ peers: false }) (ohne Peer-Vergleich; Peers des letzten Stands bleiben stehen).
// Ein Sequenzzaehler je Zonenansicht verwirft veraltete Antworten. Kein setState direkt im Effekt-Koerper.
//
// useZoneDnssec() -> { status, loading, error, reload({ peers }) -> Promise<status|null> }

export const DNSSEC_STATUS_KEY = 'dnssec.status'
export const DNSSEC_MODAL_KEY = 'dnssec.modal'

const INITIAL = Object.freeze({ status: null, loading: false, error: '' })

// Zaehler je Zonenansicht: setSlotState ist pro useZoneData-Instanz stabil (useCallback ohne Abhaengigkeiten)
const sequences = new WeakMap()

function nextSeq(owner) {
    const n = (sequences.get(owner) || 0) + 1
    sequences.set(owner, n)
    return n
}

function currentSeq(owner) {
    return sequences.get(owner) || 0
}

export default function useZoneDnssec() {
    const { server, zoneId, slotState, setSlotState } = useZoneDetail()
    const state = slotState[DNSSEC_STATUS_KEY] || INITIAL

    const reload = useCallback(async ({ peers = true } = {}) => {
        const seq = nextSeq(setSlotState)
        setSlotState(DNSSEC_STATUS_KEY, (prev) => ({ ...INITIAL, ...prev, loading: true }))
        try {
            const next = await api.getDnssecStatus(server, zoneId, { peers })
            if (seq !== currentSeq(setSlotState)) return null
            setSlotState(DNSSEC_STATUS_KEY, (prev) => ({
                status: peers ? next : mergeStatusKeepPeers(next, prev?.status),
                loading: false,
                error: '',
            }))
            return next
        } catch (err) {
            if (seq !== currentSeq(setSlotState)) return null
            const message = err?.message || String(err)
            // Status eines frueheren Ladens bleibt sichtbar; der Fehler steht daneben (Karte zeigt beides)
            setSlotState(DNSSEC_STATUS_KEY, (prev) => ({ ...INITIAL, ...prev, loading: false, error: message }))
            return null
        }
    }, [server, zoneId, setSlotState])

    return { status: state.status, loading: state.loading, error: state.error, reload }
}

/** Laedt den Status nach jedem erfolgreichen loadZone() (auch silent); einmal je Zonenansicht mounten. */
export function useZoneDnssecAutoload() {
    const { subscribeZoneLoaded } = useZoneDetail()
    const { reload } = useZoneDnssec()
    useEffect(() => subscribeZoneLoaded((event) => {
        if (!event?.ok) return undefined
        return reload({ peers: true })
    }), [subscribeZoneLoaded, reload])
}
