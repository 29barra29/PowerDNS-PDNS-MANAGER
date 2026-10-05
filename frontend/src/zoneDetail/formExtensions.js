// Erweiterungs-Vertrag des Record-Dialogs (Plan B.14 [F5]) - die reine Ablauflogik, ohne React/i18n,
// damit sie per `node --test` geprueft werden kann (tests/zoneDetailSlots.test.mjs). RecordFormModal.jsx ruft
// ausschliesslich diese Funktionen; die Beschreibung fuer Autoren steht in form-extensions/README.md.
//
// Slot-Datei `zoneDetail/form-extensions/<name>.ext.jsx`:
//   export const ext = {
//     id,                                   // eindeutig (Default: Dateiname)
//     when(type) -> bool,                   // fuer welchen Record-Typ die Erweiterung aktiv ist (Pflicht)
//     position: 'afterValues'|'beforeTtl'|'footer',
//     initialState({ record, mode, type, zone }) -> state,     // beim Oeffnen und bei jeder Schnellvorlage
//     collect(state, form) -> partialBody,  // vor dem Senden; wird in den Request-Body gemergt
//     validate?(state, form) -> errorKey | { key, values } | null,
//     onResult?(res, { state, form, ctx }) -> void,           // nach erfolgreicher Antwort
//   }
//   export default function MyExt({ type, form, setForm, record, zone, server, isEdit, canEdit, extState, setExtState })
//
// Ablauf im Dialog: Oeffnen -> initialState (fuer ALLE Erweiterungen, damit ein spaeterer Typwechsel einen
// Zustand vorfindet) -> Absenden: Kernpruefungen -> validate (nur aktive) -> collect (nur aktive, Merge) ->
// Request -> Fan-out-Auswertung + Erfolgsmeldung -> onResult (nur aktive) -> Zone neu laden.

export const EXT_POSITIONS = Object.freeze(['afterValues', 'beforeTtl', 'footer'])
export const DEFAULT_EXT_POSITION = 'afterValues'
export const EXT_MODES = Object.freeze(['add', 'edit', 'clone', 'template'])

// Felder, die der Dialog selbst setzt; eine Erweiterung kann sie per collect() nicht ueberschreiben.
export const CORE_BODY_KEYS = Object.freeze(['name', 'type', 'ttl', 'records', 'old_content', 'new_content', 'disabled'])

function report(onProblem, message) {
    if (onProblem) onProblem(message)
    else if (typeof console !== 'undefined') console.error(`[form-extensions] ${message}`)
}

// Slot-Eintraege (lib/slots.collectSlots mit exportName 'ext') -> geprueftes, normalisiertes Array.
// Ungueltige Erweiterungen (kein when/collect/initialState) werden mit Meldung verworfen.
export function normalizeExtensions(entries, { onProblem } = {}) {
    const out = []
    for (const entry of entries || []) {
        const name = entry?.file || entry?.id || '?'
        if (!entry || typeof entry.when !== 'function') {
            report(onProblem, `${name}: when(type) fehlt - Erweiterung ignoriert`)
            continue
        }
        if (typeof entry.initialState !== 'function' || typeof entry.collect !== 'function') {
            report(onProblem, `${name}: initialState() und collect() sind Pflicht - Erweiterung ignoriert`)
            continue
        }
        let position = entry.position || DEFAULT_EXT_POSITION
        if (!EXT_POSITIONS.includes(position)) {
            report(onProblem, `${name}: unbekannte position "${position}" - "${DEFAULT_EXT_POSITION}" verwendet`)
            position = DEFAULT_EXT_POSITION
        }
        out.push({ ...entry, position, Component: entry.Component || null })
    }
    return out
}

export function isExtActive(ext, type, { onProblem } = {}) {
    try {
        return !!ext.when(type)
    } catch (err) {
        report(onProblem, `${ext.id}: when() hat geworfen (${err?.message || err})`)
        return false
    }
}

export function activeExtensions(exts, type, opts) {
    return (exts || []).filter((ext) => isExtActive(ext, type, opts))
}

// { [ext.id]: state } fuer alle Erweiterungen. args = { record, mode, type, zone }.
export function initialExtStates(exts, args, { onProblem } = {}) {
    const states = {}
    for (const ext of exts || []) {
        try {
            states[ext.id] = ext.initialState(args)
        } catch (err) {
            report(onProblem, `${ext.id}: initialState() hat geworfen (${err?.message || err})`)
            states[ext.id] = null
        }
    }
    return states
}

// Erster Validierungsfehler der aktiven Erweiterungen oder null.
// Rueckgabe: { id, key, values, text } - key = i18n-Key (der Dialog uebersetzt ihn mit values), text = fertiger
// Text (nur wenn validate() geworfen hat; dann ist key null).
export function validateExtensions(exts, states, form, { onProblem } = {}) {
    for (const ext of activeExtensions(exts, form?.type, { onProblem })) {
        if (typeof ext.validate !== 'function') continue
        let res
        try {
            res = ext.validate(states?.[ext.id], form)
        } catch (err) {
            return { id: ext.id, key: null, values: {}, text: String(err?.message || err) }
        }
        if (!res) continue
        if (typeof res === 'string') return { id: ext.id, key: res, values: {}, text: null }
        if (typeof res === 'object' && typeof res.key === 'string') {
            return { id: ext.id, key: res.key, values: res.values || {}, text: null }
        }
        report(onProblem, `${ext.id}: validate() lieferte einen unbekannten Wert - ignoriert`)
    }
    return null
}

// Teil-Bodies der aktiven Erweiterungen einsammeln.
// Rueckgabe: { body, ignored: [{ id, key }] } - Kernfelder (CORE_BODY_KEYS) werden nie uebernommen.
// Wirft collect(), wird der Fehler mit der id weitergereicht (der Dialog zeigt ihn als Modal-Fehler).
export function collectExtensions(exts, states, form, { onProblem } = {}) {
    const body = {}
    const ignored = []
    for (const ext of activeExtensions(exts, form?.type, { onProblem })) {
        let part
        try {
            part = ext.collect(states?.[ext.id], form)
        } catch (err) {
            const e = new Error(String(err?.message || err))
            e.extId = ext.id
            throw e
        }
        if (part === null || part === undefined) continue
        if (typeof part !== 'object' || Array.isArray(part)) {
            report(onProblem, `${ext.id}: collect() muss ein Objekt liefern - ignoriert`)
            continue
        }
        for (const [key, value] of Object.entries(part)) {
            if (CORE_BODY_KEYS.includes(key)) {
                ignored.push({ id: ext.id, key })
                report(onProblem, `${ext.id}: collect() darf "${key}" nicht setzen - ignoriert`)
                continue
            }
            body[key] = value
        }
    }
    return { body, ignored }
}

// Request-Body des Dialogs + Teil-Body der Erweiterungen; die Kernfelder gewinnen immer.
export function mergeRequestBody(base, partial) {
    const out = { ...(partial || {}) }
    for (const [key, value] of Object.entries(base || {})) out[key] = value
    return out
}

// onResult der aktiven Erweiterungen; Fehler einer Erweiterung stoppen die anderen nicht.
// Liefert die Liste der Fehler (fuer Tests/Logging).
export function notifyExtensions(exts, states, res, form, ctx, { onProblem } = {}) {
    const errors = []
    for (const ext of activeExtensions(exts, form?.type, { onProblem })) {
        if (typeof ext.onResult !== 'function') continue
        try {
            ext.onResult(res, { state: states?.[ext.id], form, ctx })
        } catch (err) {
            errors.push({ id: ext.id, error: err })
            report(onProblem, `${ext.id}: onResult() hat geworfen (${err?.message || err})`)
        }
    }
    return errors
}

// Erweiterungen je Position (fuer das Rendern), nur aktive.
export function extensionsAt(exts, type, position, opts) {
    return activeExtensions(exts, type, opts).filter((ext) => ext.position === position)
}
