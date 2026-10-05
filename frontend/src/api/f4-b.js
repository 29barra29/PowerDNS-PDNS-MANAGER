// DNSSEC-Endpunkte (F4 §6.3, Plan WS-F4-B). Backend: routers/dnssec.py (WS-F4-A), Prefix /dnssec.
//
// Dateiname = Schluessel in ALLOWED_OVERRIDES (frontend/tests/api-modules.test.mjs, Orchestrator-Entscheidung
// nach Welle 1; der Plan nennt api/dnssec.js). Ueberschreibungen laut Plan B.14: enableDNSSEC (Body mit Optionen,
// Default {}) und disableDNSSEC (Body neu, z. B. { force, bump_serial }). listKeys/getDsRecords bleiben die
// Kernmethoden aus api.js (die Oberflaeche nutzt sie nicht mehr; Status liefert alles).
//
// Mutationen nehmen optional bump_serial (Body bzw. Query bei DELETE): undefined = Backend-Standard (bei
// Zonen vom Typ Master/Producer Serial erhoehen + NOTIFY), false = nicht erhoehen. Antworten enthalten
// details.serial_bumped/serial/serial_error/notified/notify_error.
// Fehler: err.message (lesbar), err.status, err.payload (bei Schutzregeln detail = { message, code, force_possible }).
import { buildQuery } from '../lib/buildQuery.js'

export const overrides = ['enableDNSSEC', 'disableDNSSEC']

const seg = (v) => encodeURIComponent(String(v))
const base = (server, zone) => `/dnssec/${seg(server)}/${seg(zone)}`

export default {
    // -> DnssecStatusResponse (F4 §3.2); peers=false spart den Vergleich mit anderen Servern (Reload nach Aktionen)
    getDnssecStatus(server, zone, { peers = true, signal } = {}) {
        return this.request('GET', `${base(server, zone)}/status${buildQuery({ peers: peers ? 'true' : 'false' })}`,
            null, { signal })
    },

    // data: { key_model, algorithm, bits, nsec_mode, nsec3_iterations, nsec3_salt, nsec3_optout, nsec3narrow,
    //         bump_serial? } – leerer Body = Standardwerte; 200 auch bei already_enabled
    enableDNSSEC(server, zone, data = {}) {
        return this.request('POST', `${base(server, zone)}/enable`, data || {})
    },

    // data: { force?, bump_serial? } -> details { already_disabled, deleted_keys, rectify_error, ... }
    disableDNSSEC(server, zone, data = {}) {
        return this.request('POST', `${base(server, zone)}/disable`, data || {})
    },

    // data: { keytype: 'csk'|'ksk'|'zsk', algorithm, bits?, active, published, bump_serial? } -> details { key, warnings }
    createDnssecKey(server, zone, data) {
        return this.request('POST', `${base(server, zone)}/keys`, data)
    },

    // data: { active?, published?, force?, bump_serial? } -> details { unchanged, overridden, serial_bumped, ... }
    updateDnssecKey(server, zone, keyId, data) {
        return this.request('PUT', `${base(server, zone)}/keys/${seg(keyId)}`, data)
    },

    deleteDnssecKey(server, zone, keyId, { force = false, bumpSerial } = {}) {
        const query = buildQuery({
            force: force ? 'true' : undefined,
            bump_serial: bumpSerial === undefined || bumpSerial === null ? undefined : String(!!bumpSerial),
        })
        return this.request('DELETE', `${base(server, zone)}/keys/${seg(keyId)}${query}`)
    },

    // data: { nsec_mode, nsec3_iterations, nsec3_salt, nsec3_optout, nsec3narrow, bump_serial? }
    updateNsec3(server, zone, data) {
        return this.request('PUT', `${base(server, zone)}/nsec3`, data)
    },
}
