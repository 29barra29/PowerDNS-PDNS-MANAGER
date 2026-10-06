// Zustand des Step-up-Dialogs (Plan B.3 [S8], WS-F10-APP-FE). Rein (kein React, kein i18n, kein api.js) und damit
// per `node --test` ladbar (frontend/tests/stepUpStore.test.mjs).
//
// Ablauf:
//   1. api.js loest bei 403 `stepup_required`/`reauth_required` das Window-Event STEP_UP_EVENT aus; der Dialog
//      (components/dialogs/10-stepup.dialog.jsx) ruft dann `openStepUp(detail)`.
//   2. Aufrufer, die eine geschuetzte Aktion ausfuehren, nutzen `withStepUp(fn, { proactive })`:
//        fn(stepUp) bekommt null (erster Versuch) bzw. { current_password, totp_code } und liefert das Promise
//        des API-Aufrufs. Bei `stepup_required`/`stepup_failed` wartet withStepUp auf den Dialog und wiederholt.
//        `proactive: true` fragt schon vor dem ersten Versuch (lokale Konten, sensible Aenderung).
//   3. `reauth_required` (externes Konto, Anmeldung aelter als 10 min): der Dialog bietet "Abmelden und erneut
//      anmelden" an; die aktuelle Seite wird gemerkt (saveReturnPath) und nach der Anmeldung wieder geoeffnet.
// Abbruch: withStepUp wirft einen Fehler mit `code === 'stepup_cancelled'` (isStepUpAbort) – Aufrufer zeigen nichts an.

export const STEP_UP_CANCELLED = 'stepup_cancelled'
export const STEP_UP_RETURN_KEY = 'pdns:stepup-return'
export const STEP_UP_RETURN_MAX_AGE_MS = 15 * 60 * 1000

const CLOSED = Object.freeze({ open: false, code: null, error: '', seq: 0 })

let state = CLOSED
let waiters = []
const listeners = new Set()

function setState(next) {
    state = Object.freeze({ ...next, seq: state.seq + 1 })
    for (const l of [...listeners]) {
        try { l() } catch { /* ein kaputter Listener stoppt die anderen nicht */ }
    }
}

export function getStepUpState() {
    return state
}

export function subscribeStepUp(listener) {
    listeners.add(listener)
    return () => listeners.delete(listener)
}

function normalizeCode(code) {
    return code === 'reauth_required' ? 'reauth_required' : 'stepup_required'
}

// Vom Dialog bei STEP_UP_EVENT aufgerufen (detail = { code, method, path, message }).
export function openStepUp(detail = {}) {
    setState({ open: true, code: normalizeCode(detail.code), error: '' })
}

// Dialog oeffnen (bzw. offen halten) und auf die Eingabe warten: Promise<{ current_password, totp_code } | null>.
export function requestStepUp({ code = 'stepup_required', error = '' } = {}) {
    return new Promise((resolve) => {
        waiters.push(resolve)
        setState({ open: true, code: normalizeCode(code), error: String(error || '') })
    })
}

function settle(value) {
    const pending = waiters
    waiters = []
    setState({ open: false, code: null, error: '' })
    for (const resolve of pending) resolve(value)
}

// Eingabe aus dem Dialog (lokales Konto). Leere Felder werden nicht mitgeschickt.
export function submitStepUp({ current_password, totp_code } = {}) {
    const creds = {}
    if (current_password) creds.current_password = String(current_password)
    const code = String(totp_code || '').replace(/\s/g, '')
    if (code) creds.totp_code = code
    settle(creds)
}

export function cancelStepUp() {
    settle(null)
}

export function hasStepUpWaiters() {
    return waiters.length > 0
}

export function stepUpAbortError() {
    const err = new Error(STEP_UP_CANCELLED)
    err.code = STEP_UP_CANCELLED
    err.stepUpAbort = true
    return err
}

export function isStepUpAbort(err) {
    return !!err && (err.stepUpAbort === true || err.code === STEP_UP_CANCELLED)
}

// Geschuetzte Aktion mit Step-up ausfuehren (siehe Kopfkommentar). maxAttempts begrenzt Wiederholungen nach
// falscher Eingabe (das Backend zaehlt Fehlversuche ohnehin ueber den Login-Limiter).
export async function withStepUp(fn, { proactive = false, maxAttempts = 3 } = {}) {
    let creds = null
    if (proactive) {
        creds = await requestStepUp({ code: 'stepup_required' })
        if (!creds) throw stepUpAbortError()
    }
    for (let attempt = 0; ; attempt++) {
        try {
            return await fn(creds)
        } catch (err) {
            const code = err?.code
            if (code === 'reauth_required') {
                // Dialog ist ueber das Event aus api.js offen; die Entscheidung (abmelden/abbrechen) faellt dort.
                if (!getStepUpState().open) openStepUp({ code })
                err.stepUpHandled = true
                throw err
            }
            if ((code !== 'stepup_required' && code !== 'stepup_failed') || attempt >= maxAttempts) throw err
            creds = await requestStepUp({ code: 'stepup_required', error: code === 'stepup_failed' ? err.message : '' })
            if (!creds) throw stepUpAbortError()
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Rueckkehr nach erneuter Anmeldung (externes Konto)

// Nur interne Pfade der SPA (kein Schema, kein //host, keine Steuerzeichen), nicht die Anmeldeseite selbst.
export function isSafeReturnPath(path) {
    if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') || path.length > 512) return false
    // eslint-disable-next-line no-control-regex -- Steuerzeichen bewusst ausschliessen
    if (/[\\\u0000-\u001f\u007f]/.test(path)) return false
    return !/^\/login(?:[/?#]|$)/.test(path)
}

export function saveReturnPath(storage, path, now = Date.now()) {
    if (!storage || !isSafeReturnPath(path)) return false
    try {
        storage.setItem(STEP_UP_RETURN_KEY, JSON.stringify({ path, ts: now }))
        return true
    } catch {
        return false
    }
}

// Gemerkten Pfad holen und loeschen; abgelaufen oder ungueltig -> null.
export function takeReturnPath(storage, now = Date.now(), maxAgeMs = STEP_UP_RETURN_MAX_AGE_MS) {
    if (!storage) return null
    let raw
    try {
        raw = storage.getItem(STEP_UP_RETURN_KEY)
        if (raw !== null) storage.removeItem(STEP_UP_RETURN_KEY)
    } catch {
        return null
    }
    if (!raw) return null
    try {
        const { path, ts } = JSON.parse(raw)
        if (!Number.isFinite(ts) || now - ts > maxAgeMs || now < ts - 60000) return null
        return isSafeReturnPath(path) ? path : null
    } catch {
        return null
    }
}

// Nur fuer Tests: Zustand zuruecksetzen.
export function _resetStepUpForTests() {
    waiters = []
    listeners.clear()
    state = CLOSED
}
