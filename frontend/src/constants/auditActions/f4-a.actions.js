/**
 * Audit-Aktionen aus WS-F4-A (DNSSEC-Backend, F4 3.8/3.9/3.12): Schluessel anlegen, veroeffentlichen/zurueckziehen,
 * active+published gemeinsam aendern, NSEC/NSEC3 aendern. Gruppe "dnssec" (erscheint im Zonenverlauf).
 * Die Bestandsaktionen DNSSEC_ENABLE/DNSSEC_DISABLE/KEY_ACTIVATE/KEY_DEACTIVATE/KEY_DELETE stehen in base.actions.js;
 * die Serial-Erhoehung nach Schluesselaenderungen schreibt UPDATE (records) und ZONE_NOTIFY (zone) – ebenfalls Basis.
 * Labels: `audit.actions.<ACTION>` im Fragment f4-a.
 */
export default [
    { action: 'KEY_CREATE', group: 'dnssec' },
    { action: 'KEY_PUBLISH', group: 'dnssec' },
    { action: 'KEY_UNPUBLISH', group: 'dnssec' },
    { action: 'KEY_UPDATE', group: 'dnssec' },
    { action: 'DNSSEC_NSEC3_UPDATE', group: 'dnssec' },
]
