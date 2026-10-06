// Gemeinsame Fokus-Verwaltung fuer modale Dialoge (role="dialog" + aria-modal="true"), WAI-ARIA Dialog Pattern.
//
//   const dialogRef = useDialogFocus({ onClose, canClose: !busy, initialFocusRef, onSubmitShortcut })
//   <div role="dialog" aria-modal="true" ref={dialogRef} ...>
//
// Leistet:
// - initialer Fokus beim Oeffnen: `initialFocusRef.current`, sonst das erste Element mit `data-autofocus`, sonst
//   das erste fokussierbare Element im Dialog, sonst der Dialog selbst (bekommt dafuer tabindex="-1"). Liegt der
//   Fokus schon im Dialog (z. B. per `autoFocus`), bleibt er dort.
// - Tab-Falle: Tab/Umschalt+Tab wandern nur innerhalb des Dialogs (vom letzten zum ersten Element und umgekehrt).
// - ESC ruft `onClose()` auf, solange `canClose` wahr ist (z. B. nicht waehrend des Speicherns).
// - Strg/Cmd+Enter ruft `onSubmitShortcut()` auf (falls angegeben) – nur im obersten Dialog. Frueher hingen diese
//   Listener global am window/document: lag ein Step-up- oder Einmal-Dialog darueber, sendete Strg+Enter den
//   darunterliegenden Dialog ab (WS-W2-NACHARBEIT 4.4, behoben in WS-W3-NACHARBEIT). Dialoge haengen deshalb
//   keine eigenen keydown-Listener mehr an window/document (statische Pruefung tests/a11y-dialogs.test.mjs).
// - Fokus-Rueckgabe: Beim Schliessen (Unmount bzw. `active` -> false) bekommt das zuvor fokussierte Element den
//   Fokus zurueck, sofern es noch im Dokument ist und der Fokus nicht inzwischen woanders gesetzt wurde.
//   Ist der Ausloeser beim Schliessen (noch) deaktiviert – z. B. ein Speichern-Knopf, der waehrend eines Step-ups
//   `disabled` ist –, wartet die Rueckgabe, bis er wieder aktiv ist (hoechstens RESTORE_WAIT_MS; UI-SMOKE-3).
//   War beim Oeffnen kein Element fokussiert, weil der Browser dem gerade deaktivierten Ausloeser den Fokus genommen
//   hat, gilt dieses zuletzt fokussierte, jetzt deaktivierte Element als Ausloeser (nur innerhalb OPENER_GRACE_MS).
// - Verschachtelung: Nur der zuletzt geoeffnete Dialog (Stapel) reagiert auf ESC/Tab. Tastendruecke aus einem
//   anderen aria-modal-Dialog, der den Hook (noch) nicht nutzt und darueber liegt (z. B. OneTimeSecretModal),
//   werden ignoriert – der Hook stiehlt dort keinen Fokus und schliesst nichts darunter.
//
// Die reinen Helfer (ohne React) sind exportiert und per `node --test` pruefbar (tests/a11y-dialogs.test.mjs).
import { useEffect, useRef } from 'react'

export const FOCUSABLE_SELECTOR = [
    'a[href]',
    'area[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    'iframe',
    'audio[controls]',
    'video[controls]',
    'summary',
    '[contenteditable]:not([contenteditable="false"])',
    '[tabindex]:not([tabindex="-1"])',
].join(',')

// Stapel der offenen Dialoge (zuletzt geoeffnet = oben)
const dialogStack = []

export function pushDialog(token) {
    dialogStack.push(token)
    return () => {
        const idx = dialogStack.lastIndexOf(token)
        if (idx >= 0) dialogStack.splice(idx, 1)
    }
}

export function isTopDialog(token) {
    return dialogStack.length > 0 && dialogStack[dialogStack.length - 1] === token
}

export function openDialogCount() {
    return dialogStack.length
}

// Sichtbar, aktiviert, nicht per tabindex="-1"/inert/aria-hidden ausgenommen.
export function isFocusable(el) {
    if (!el || typeof el.focus !== 'function') return false
    if (el.disabled) return false
    const tabindex = typeof el.getAttribute === 'function' ? el.getAttribute('tabindex') : null
    if (tabindex !== null && Number(tabindex) < 0) return false
    if (typeof el.closest === 'function' && el.closest('[inert],[aria-hidden="true"]')) return false
    if (typeof el.getClientRects === 'function' && el.getClientRects().length === 0) return false
    return true
}

export function getFocusableElements(container) {
    if (!container || typeof container.querySelectorAll !== 'function') return []
    return Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR)).filter(isFocusable)
}

// Ziel fuer Tab bzw. Umschalt+Tab oder null (= Browser-Standard reicht, Fokus bleibt ohnehin im Dialog).
export function nextTrapTarget(items, active, { shiftKey = false } = {}) {
    if (!Array.isArray(items) || items.length === 0) return null
    const first = items[0]
    const last = items[items.length - 1]
    if (!items.includes(active)) return shiftKey ? last : first
    if (shiftKey && active === first) return last
    if (!shiftKey && active === last) return first
    return null
}

export function pickInitialFocus(container, preferred) {
    if (preferred && isFocusable(preferred)) return preferred
    const marked = container && typeof container.querySelector === 'function'
        ? container.querySelector('[data-autofocus]')
        : null
    if (marked && isFocusable(marked)) return marked
    return getFocusableElements(container)[0] || container || null
}

// Liegt das Ereignisziel in einem anderen aria-modal-Dialog (nicht diesem, nicht in diesem verschachtelt)?
export function belongsToOtherModal(target, container) {
    if (!target || typeof target.closest !== 'function' || !container) return false
    const modal = target.closest('[aria-modal="true"]')
    return Boolean(modal && modal !== container && !container.contains(modal))
}

// Soll beim Schliessen der Fokus zurueck? Nur wenn das Element noch existiert und der Fokus nicht inzwischen
// bewusst woanders hingesetzt wurde (z. B. in einen neu geoeffneten Dialog).
export function shouldRestoreFocus(previous, current, container, body) {
    if (!previous || previous === body || typeof previous.focus !== 'function') return false
    if (previous.isConnected === false) return false
    if (!current || current === body) return true
    if (current.isConnected === false) return true
    return Boolean(container && typeof container.contains === 'function' && container.contains(current))
}

function focusElement(el) {
    try {
        el.focus({ preventScroll: false })
    } catch {
        // Fokus ist eine Komfortfunktion – ein Fehler darf den Dialog nicht verhindern
    }
}

// Wie lange die Fokus-Rueckgabe auf einen deaktivierten Ausloeser wartet bzw. wie frisch der zuletzt fokussierte
// Ausloeser sein muss, wenn beim Oeffnen nichts fokussiert ist.
export const RESTORE_WAIT_MS = 5000
export const OPENER_GRACE_MS = 10000

// Ausloeser beim Oeffnen: das fokussierte Element. Ist nichts fokussiert (body), das zuletzt fokussierte Element –
// aber nur, wenn es noch im Dokument ist, jetzt deaktiviert ist (der Browser hat ihm deshalb den Fokus genommen) und
// den Fokus erst vor hoechstens OPENER_GRACE_MS verloren hat. Sonst bleibt es beim bisherigen Verhalten (keine
// Rueckgabe), damit der Fokus nicht zu einem alten Element springt und die Seite verschiebt.
export function resolveOpener(active, body, last, now = Date.now()) {
    if (active && active !== body) return active
    const el = last && last.el
    if (!el || el === body || el.isConnected === false || el.disabled !== true) return active || null
    if (typeof last.blurredAt !== 'number' || now - last.blurredAt > OPENER_GRACE_MS) return active || null
    return el
}

// Soll die Rueckgabe warten? Nur fuer einen Ausloeser, der noch im Dokument ist und gerade deaktiviert ist.
export function shouldDeferRestore(previous) {
    return Boolean(previous && previous.isConnected !== false && previous.disabled === true)
}

// zuletzt fokussiertes Element (ein Listener-Paar fuer alle Dialoge, beim Laden des Moduls im Browser angemeldet,
// damit auch der allererste Dialog seinen Ausloeser kennt)
const lastFocus = { el: null, blurredAt: null }
let focusTracking = false

function trackFocus() {
    if (focusTracking || typeof document === 'undefined' || typeof document.addEventListener !== 'function') return
    focusTracking = true
    document.addEventListener('focusin', (e) => {
        lastFocus.el = e.target
        lastFocus.blurredAt = null
    }, true)
    document.addEventListener('focusout', (e) => {
        if (e.target === lastFocus.el) lastFocus.blurredAt = Date.now()
    }, true)
}

trackFocus()

// Gibt den Fokus an `previous` zurueck – sofort oder, wenn der Ausloeser gerade deaktiviert ist, sobald er wieder
// aktiv wird (MutationObserver auf `disabled`, sonst kurzes Nachsehen). Bedingung bleibt jeweils shouldRestoreFocus.
function restoreFocus(previous, node) {
    const attempt = () => {
        if (shouldRestoreFocus(previous, document.activeElement, node, document.body)) focusElement(previous)
    }
    if (!shouldDeferRestore(previous)) {
        attempt()
        return
    }
    let done = false
    let observer = null
    let poll = null
    let timer = null
    const finish = () => {
        done = true
        if (observer) observer.disconnect()
        clearInterval(poll)
        clearTimeout(timer)
    }
    const check = () => {
        if (done) return
        if (previous.isConnected === false) { finish(); return }
        if (previous.disabled) return
        finish()
        attempt()
    }
    if (typeof MutationObserver === 'function') {
        observer = new MutationObserver(check)
        observer.observe(previous, { attributes: true, attributeFilter: ['disabled'] })
    } else {
        poll = setInterval(check, 50)
    }
    timer = setTimeout(finish, RESTORE_WAIT_MS)
}

/** Strg/Cmd+Enter (ohne Alt, nicht waehrend einer IME-Eingabe)? */
export function isSubmitShortcut(e) {
    return Boolean(e && (e.key === 'Enter') && (e.ctrlKey || e.metaKey) && !e.altKey && !e.isComposing)
}

/**
 * Fokus-Management fuer einen modalen Dialog. Liefert den Ref fuer das Element mit role="dialog".
 * @param {{ onClose?: () => void, canClose?: boolean, initialFocusRef?: { current: any }, active?: boolean,
 *           onSubmitShortcut?: () => void }} [options]
 */
export function useDialogFocus({
    onClose, canClose = true, initialFocusRef = null, active = true, onSubmitShortcut = null,
} = {}) {
    const dialogRef = useRef(null)
    const latest = useRef({ onClose, canClose, onSubmitShortcut })

    // immer die aktuellen Callbacks/Flags verwenden, ohne den Fokus-Effekt neu zu starten
    useEffect(() => {
        latest.current = { onClose, canClose, onSubmitShortcut }
    })

    useEffect(() => {
        if (!active || typeof document === 'undefined') return undefined
        const node = dialogRef.current
        if (!node) return undefined
        trackFocus()
        const previous = resolveOpener(document.activeElement, document.body, lastFocus)
        const token = {}
        const pop = pushDialog(token)

        if (!node.contains(document.activeElement)) {
            const target = pickInitialFocus(node, initialFocusRef?.current)
            if (target === node && !node.hasAttribute('tabindex')) node.setAttribute('tabindex', '-1')
            if (target) focusElement(target)
        }

        function onKeyDown(e) {
            if (!isTopDialog(token) || belongsToOtherModal(e.target, node)) return
            if (isSubmitShortcut(e)) {
                const { onSubmitShortcut: submit } = latest.current
                if (e.defaultPrevented || typeof submit !== 'function') return
                e.preventDefault()
                submit()
                return
            }
            if (e.key === 'Escape' || e.key === 'Esc') {
                if (e.defaultPrevented || e.isComposing) return
                const { onClose: close, canClose: allowed } = latest.current
                if (!allowed || typeof close !== 'function') return
                e.preventDefault()
                close()
                return
            }
            if (e.key !== 'Tab' || e.altKey || e.ctrlKey || e.metaKey) return
            const items = getFocusableElements(node)
            if (items.length === 0) {
                e.preventDefault()
                if (!node.hasAttribute('tabindex')) node.setAttribute('tabindex', '-1')
                focusElement(node)
                return
            }
            const target = nextTrapTarget(items, document.activeElement, { shiftKey: e.shiftKey })
            if (target) {
                e.preventDefault()
                focusElement(target)
            }
        }

        document.addEventListener('keydown', onKeyDown)
        return () => {
            document.removeEventListener('keydown', onKeyDown)
            pop()
            restoreFocus(previous, node)
        }
    }, [active, initialFocusRef])

    return dialogRef
}

export default useDialogFocus
