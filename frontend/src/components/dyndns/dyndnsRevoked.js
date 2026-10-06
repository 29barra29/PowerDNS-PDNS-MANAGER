// Gesperrte DynDNS-Secrets in der Oberflaeche (WS-W3-NACHARBEIT, Antrag A2 aus WS-F9F11-BE-fix2).
// Rein (kein React, kein i18n-Import) und damit per `node --test` pruefbar (tests/dyndnsRevoked.test.mjs).
//
// Das Backend sperrt das Secret eines Tokens endgueltig, wenn der Token in einer URL stand oder "Alle Zugaenge
// widerrufen" lief (`secret_revoked: true`, Token zugleich deaktiviert). Aktivieren beantwortet es dann mit 409;
// nutzbar wird der Token nur ueber ein neues Secret (rotate, nur der Besitzer).

/** Ist das Secret dieses Tokens gesperrt? */
export function isSecretRevoked(token) {
    return token?.secret_revoked === true
}

/**
 * Darf die Oberflaeche den Schalter Aktivieren/Deaktivieren anbieten? Deaktivieren immer; Aktivieren nicht, solange
 * das Secret gesperrt ist (das Backend antwortet sonst 409).
 */
export function canToggleActive(token) {
    if (!token) return false
    if (token.is_active !== false) return true
    return !isSecretRevoked(token)
}

/**
 * Fehlertext einer Token-Aktion: 409 (gesperrtes Secret) uebersetzt, sonst die Meldung des Backends.
 * `t` ist die i18n-Funktion des Aufrufers.
 */
export function dyndnsActionError(err, t) {
    if (err && err.status === 409) return t('dyndns.secretRevokedActivate')
    return err?.message || ''
}
