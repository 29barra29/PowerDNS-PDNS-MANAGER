// PTR-Pflege: gemerkte Auswahl der Checkbox "PTR in Reverse-Zone mitpflegen" und Admin-Default (F11 §2.5, Plan B.14).
// Rein (kein React, kein i18n, kein api-Import) und damit per `node --test` ladbar (tests/ptrResults.test.mjs).
//
// Reihenfolge der Entscheidung (effectiveManagePtr):
//   1. Auswahl fuer genau diese Zone (zuletzt in diesem Browser gesetzt)
//   2. letzte Auswahl in diesem Browser (irgendeine Zone)
//   3. Admin-Default `ptr_auto_default` aus GET /ptr/config (Cache, siehe loadPtrConfig)
// Die UI sendet immer explizit `manage_ptr: true|false` (Spec F11 §12 Nr. 14).
//
// localStorage-Key `dns_manager_ptr_manage`: JSON { "last": bool, "zones": { "<zone.>": bool } }.
// Zugriffe stehen in try/catch (Privatmodus, gesperrter Speicher) - dann gilt nur der Admin-Default.

export const PTR_PREF_KEY = 'dns_manager_ptr_manage'
export const PTR_TYPES = Object.freeze(['A', 'AAAA'])
// Begrenzt die gemerkten Zonen, damit der Eintrag nicht unbegrenzt waechst (aelteste fliegen raus).
export const MAX_REMEMBERED_ZONES = 200
// Wie lange GET /ptr/config als frisch gilt (ms)
export const PTR_CONFIG_TTL_MS = 60_000

const DEFAULT_CONFIG = Object.freeze({ auto_default: false, reverse_zones_available: null })

export function isPtrType(type) {
    return PTR_TYPES.includes(String(type || '').toUpperCase())
}

// Zone als String ('example.com', 'example.com.') oder Objekt ({ key } | { name } | { id }) -> 'example.com.' (klein)
export function zoneKeyOf(zone) {
    let raw = zone
    if (zone && typeof zone === 'object') raw = zone.key || zone.name || zone.id || ''
    const z = String(raw || '').trim().toLowerCase()
    if (!z) return ''
    return z.endsWith('.') ? z : `${z}.`
}

function storage() {
    try {
        const s = globalThis.localStorage
        return s && typeof s.getItem === 'function' ? s : null
    } catch {
        return null
    }
}

function readStore() {
    const s = storage()
    if (!s) return { last: null, zones: {} }
    try {
        const raw = s.getItem(PTR_PREF_KEY)
        if (!raw) return { last: null, zones: {} }
        // Kompatibel mit dem Spec-Format '1' | '0' (nur globale Auswahl)
        if (raw === '1' || raw === '0') return { last: raw === '1', zones: {} }
        const data = JSON.parse(raw)
        const zones = {}
        if (data && typeof data.zones === 'object' && data.zones) {
            for (const [k, v] of Object.entries(data.zones)) if (typeof v === 'boolean') zones[k] = v
        }
        return { last: typeof data?.last === 'boolean' ? data.last : null, zones }
    } catch {
        return { last: null, zones: {} }
    }
}

function writeStore(store) {
    const s = storage()
    if (!s) return false
    try {
        s.setItem(PTR_PREF_KEY, JSON.stringify(store))
        return true
    } catch {
        return false
    }
}

// Gemerkte Auswahl (Zone, sonst letzte Auswahl im Browser) oder null, wenn nichts gemerkt ist.
export function getManagePtr(zone) {
    const store = readStore()
    const key = zoneKeyOf(zone)
    if (key && typeof store.zones[key] === 'boolean') return store.zones[key]
    return typeof store.last === 'boolean' ? store.last : null
}

// Auswahl merken (fuer die Zone und als letzte Auswahl im Browser). Liefert false, wenn der Speicher fehlt.
export function setManagePtr(zone, value) {
    const v = !!value
    const store = readStore()
    const key = zoneKeyOf(zone)
    const zones = { ...store.zones }
    if (key) {
        delete zones[key] // neu einfuegen -> steht hinten (juengster Eintrag)
        zones[key] = v
        const keys = Object.keys(zones)
        for (let i = 0; i < keys.length - MAX_REMEMBERED_ZONES; i++) delete zones[keys[i]]
    }
    return writeStore({ last: v, zones })
}

// Gemerkte Auswahl verwerfen (Zone oder - ohne Argument - alles).
export function clearManagePtr(zone) {
    if (zone === undefined) {
        const s = storage()
        if (!s) return false
        try { s.removeItem(PTR_PREF_KEY); return true } catch { return false }
    }
    const store = readStore()
    const key = zoneKeyOf(zone)
    if (!key || !(key in store.zones)) return true
    const zones = { ...store.zones }
    delete zones[key]
    return writeStore({ last: store.last, zones })
}

/* ----- Admin-Default (GET /ptr/config) ------------------------------------------------------------ */

let config = { ...DEFAULT_CONFIG }
let configLoadedAt = 0
let inflight = null
const listeners = new Set()

function emit() {
    for (const cb of Array.from(listeners)) {
        try { cb(config) } catch (err) { if (typeof console !== 'undefined') console.error('[ptrPreference] Abonnent hat geworfen', err) }
    }
}

// Admin-Default der Checkbox (false, solange /ptr/config nicht geladen ist oder fehlschlug).
export function getDefault() {
    return !!config.auto_default
}

// Aktueller Stand von /ptr/config: { auto_default, reverse_zones_available (Zahl | null) }
export function getPtrConfig() {
    return config
}

// Ergebnis von GET /ptr/config uebernehmen (auch nach dem Speichern des Admin-Defaults).
export function setPtrConfig(next, { now = Date.now() } = {}) {
    const n = Number(next?.reverse_zones_available)
    config = Object.freeze({
        auto_default: !!next?.auto_default,
        reverse_zones_available: Number.isFinite(n) && next?.reverse_zones_available !== null ? n : null,
    })
    configLoadedAt = now
    emit()
    return config
}

// Admin-Default geaendert (Karte "Reverse-DNS"): Cache anpassen, ohne die Zonenzahl zu verlieren.
export function setDefaultInCache(autoDefault) {
    return setPtrConfig({ ...config, auto_default: !!autoDefault }, { now: configLoadedAt || Date.now() })
}

// Laedt /ptr/config ueber `fetcher` (z. B. () => api.getPtrConfig()), hoechstens einmal gleichzeitig und
// hoechstens alle PTR_CONFIG_TTL_MS (force: immer). Fehler -> Default { auto_default: false,
// reverse_zones_available: null } ohne Meldung (Spec F11 §2.8). Liefert immer den Konfig-Stand.
export function loadPtrConfig(fetcher, { force = false, now = Date.now() } = {}) {
    if (!force && configLoadedAt && now - configLoadedAt < PTR_CONFIG_TTL_MS) return Promise.resolve(config)
    if (inflight) return inflight
    if (typeof fetcher !== 'function') return Promise.resolve(config)
    inflight = Promise.resolve()
        .then(() => fetcher())
        .then((res) => setPtrConfig(res || DEFAULT_CONFIG))
        .catch(() => {
            // Fehler merken (kein Dauer-Neuversuch bei jedem Dialog), Werte bleiben beim letzten Stand bzw. Default
            configLoadedAt = Date.now()
            return config
        })
        .finally(() => { inflight = null })
    return inflight
}

export function subscribePtrConfig(cb) {
    if (typeof cb !== 'function') return () => {}
    listeners.add(cb)
    return () => { listeners.delete(cb) }
}

// Nur fuer Tests: Cache zuruecksetzen.
export function resetPtrConfigForTests() {
    config = { ...DEFAULT_CONFIG }
    configLoadedAt = 0
    inflight = null
    listeners.clear()
}

// Wirksame Auswahl: gemerkt (Zone, Browser) oder Admin-Default.
export function effectiveManagePtr(zone) {
    const stored = getManagePtr(zone)
    return stored === null ? getDefault() : stored
}
