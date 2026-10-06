import { createPortal } from 'react-dom'

// Haengt das Overlay eines modalen Dialogs direkt an document.body (UI-SMOKE-1).
//
// Grund: Ein Vorfahre mit backdrop-filter, filter, transform, perspective, contain oder will-change (z. B. jede
// .glass-card) wird zum Bezugsrahmen fuer position:fixed und bildet einen eigenen Stapelkontext. Ein Overlay
// `fixed inset-0`, das innerhalb einer Karte gerendert wird, bedeckt dann nur die Karte; spaetere Karten liegen
// darueber und verdecken Knoepfe des Dialogs. Im Portal gilt wieder das Fenster.
//
// Nutzung (das Overlay-Element ist das einzige Kind):
//   <ModalPortal>
//       <div className="fixed inset-0 z-50 ...">
//           <div ref={dialogRef} ...Dialog-Element mit Rolle und aria-modal...>...</div>
//       </div>
//   </ModalPortal>
//
// Das Portal aendert nur den Ort im DOM: React-Ereignisse (onClick, onSubmit, onKeyDown) und Context laufen weiter
// ueber den React-Baum; useDialogFocus arbeitet unveraendert (Ref am Dialog-Element, Tasten-Listener am document,
// Fokus-Rueckgabe an den Ausloeser). Dialoge, die spaeter geoeffnet werden, haengen hinter den frueheren im body
// und liegen bei gleichem z-index darueber.
export default function ModalPortal({ children }) {
    if (typeof document === 'undefined' || !document.body) return children
    return createPortal(children, document.body)
}
