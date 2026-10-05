// Gemeinsame Konstanten fuer Geheimnisse (F8-D06).
// SECRET_MASK muss exakt dem Backend-Wert in backend/app/core/secret_mask.py entsprechen:
// GET-Antworten liefern bei gesetztem Secret diese Maske, ein PUT mit der Maske (oder null) behaelt das
// gespeicherte Secret, ein leerer String loescht es.
export const SECRET_MASK = '••••••••'

// true, wenn der Wert die unveraenderte Maske ist (Formularfeld nicht angefasst)
export function isSecretMask(value) {
    return typeof value === 'string' && value.trim() === SECRET_MASK
}
