// DNSSEC-Modell der Oberflaeche (F4 §6.2, Plan WS-F4-B): Konstanten und reine Helfer ohne React/i18n-Import
// (per `node --test` ladbar, frontend/tests/dnssecModel.test.mjs). Uebersetzungen kommen als Funktion `t` herein.
//
// Status-Vertrag: GET /dnssec/{server}/{zone}/status (WS-F4-A, F4 §3.2). Mutationsantworten enthalten
// details.serial_bumped/serial/serial_error/notified/notify_error (Plan [D10]).

export const DNSSEC_ALGORITHMS = Object.freeze([
    { name: 'ECDSAP256SHA256', number: 13, label: 'ECDSA P-256 / SHA-256', rsa: false, recommended: true },
    { name: 'ECDSAP384SHA384', number: 14, label: 'ECDSA P-384 / SHA-384', rsa: false },
    { name: 'ED25519', number: 15, label: 'Ed25519', rsa: false, buildDependent: true },
    { name: 'ED448', number: 16, label: 'Ed448', rsa: false, buildDependent: true },
    { name: 'RSASHA256', number: 8, label: 'RSA / SHA-256', rsa: true },
    { name: 'RSASHA512', number: 10, label: 'RSA / SHA-512', rsa: true },
])
export const RSA_BITS = Object.freeze([2048, 3072, 4096])
export const RSA_BITS_DEFAULT = 2048
export const NSEC3_MAX_ITERATIONS = 50
export const NSEC3_WARN_ITERATIONS = 10
export const MAX_KEYS_DEFAULT = 6
export const SALT_MAX_LENGTH = 510

export const DEFAULT_DNSSEC_OPTIONS = Object.freeze({
    key_model: 'csk', algorithm: 'ECDSAP256SHA256', bits: null, nsec_mode: 'nsec3',
    nsec3_iterations: 0, nsec3_salt: '', nsec3_optout: false, nsec3narrow: false,
})

// Alle 22 Hinweis-Codes aus F4 §3.2 (Backend build_hints) -> i18n-Keys
export const HINT_KEYS = Object.freeze({
    presigned_zone: 'dnssec.hint.presignedZone',
    keys_inactive_only: 'dnssec.hint.keysInactiveOnly',
    no_active_sep: 'dnssec.hint.noActiveSep',
    multiple_active_sep: 'dnssec.hint.multipleActiveSep',
    rollover_in_progress: 'dnssec.hint.rolloverInProgress',
    algorithm_rollover: 'dnssec.hint.algorithmRollover',
    active_unpublished: 'dnssec.hint.activeUnpublished',
    deprecated_algorithm: 'dnssec.hint.deprecatedAlgorithm',
    nsec3_iterations_nonzero: 'dnssec.hint.nsec3IterationsNonzero',
    nsec3_iterations_high: 'dnssec.hint.nsec3IterationsHigh',
    nsec3_salt: 'dnssec.hint.nsec3Salt',
    nsec3_optout: 'dnssec.hint.nsec3OptOut',
    published_unsupported: 'dnssec.hint.publishedUnsupported',
    version_unknown: 'dnssec.hint.versionUnknown',
    api_rectify_off: 'dnssec.hint.apiRectifyOff',
    peers_divergent: 'dnssec.hint.peersDivergent',
    peers_unsigned: 'dnssec.hint.peersUnsigned',
    peers_unreachable: 'dnssec.hint.peersUnreachable',
    server_read_only: 'dnssec.hint.serverReadOnly',
    secondaries_serial: 'dnssec.hint.secondariesSerial',
    secondary_zone_signing: 'dnssec.hint.secondaryZoneSigning',
    sha1_ds: 'dnssec.hint.sha1Ds',
})

// Hinweise, die nur in einem Dialog erscheinen (nicht in der Karte)
export const DIALOG_ONLY_HINTS = Object.freeze(['sha1_ds'])

export const PROTECTION_KEYS = Object.freeze({
    last_active_key: 'dnssec.protect.lastActiveKey',
    last_active_sep: 'dnssec.protect.lastActiveSep',
    last_published_sep: 'dnssec.protect.lastPublishedSep',
    parent_ds_present: 'dnssec.protect.parentDsPresent',
})

export const PHASE_KEYS = Object.freeze({
    idle: 'dnssec.phase.idle',
    new_prepublished: 'dnssec.phase.newPrepublished',
    both_active: 'dnssec.phase.bothActive',
    old_retired: 'dnssec.phase.oldRetired',
    no_active: 'dnssec.phase.noActive',
    complex: 'dnssec.phase.complex',
})

export const PEER_STATE_KEYS = Object.freeze({
    same_keys: 'dnssec.peerState.sameKeys',
    different_keys: 'dnssec.peerState.differentKeys',
    unsigned: 'dnssec.peerState.unsigned',
    both_unsigned: 'dnssec.peerState.bothUnsigned',
    secondary: 'dnssec.peerState.secondary',
    unreachable: 'dnssec.peerState.unreachable',
    error: 'dnssec.peerState.error',
})

// Farbstufe je Peer-Zustand (Badge): ok | warning | danger | neutral
export const PEER_STATE_LEVEL = Object.freeze({
    same_keys: 'ok', both_unsigned: 'neutral', secondary: 'neutral',
    different_keys: 'danger', unsigned: 'danger', unreachable: 'warning', error: 'warning',
})

export const DS_STATUS_KEYS = Object.freeze({
    current: 'dnssec.dsStatusCurrent',
    new: 'dnssec.dsStatusNew',
    retired: 'dnssec.dsStatusRetired',
    inactive: 'dnssec.dsStatusInactive',
    unpublished_active: 'dnssec.dsStatusUnpublishedActive',
})

const LEVEL_ORDER = { danger: 0, warning: 1, info: 2 }
const PRIMARY_KINDS = new Set(['master', 'primary', 'producer'])

// ---------------------------------------------------------------------------------------------
// Algorithmen und Optionen

/** Algorithmus-Liste fuer Auswahlfelder: aus status.capabilities.algorithms, sonst die Konstante. */
export function algorithmOptions(capabilities) {
    const list = Array.isArray(capabilities?.algorithms) && capabilities.algorithms.length
        ? capabilities.algorithms
        : null
    if (!list) return DNSSEC_ALGORITHMS.map((a) => ({ ...a }))
    return list.map((a) => {
        const known = DNSSEC_ALGORITHMS.find((k) => k.name === String(a.name).toUpperCase())
        return {
            name: String(a.name).toUpperCase(),
            number: Number(a.number),
            label: known?.label || String(a.name),
            rsa: !!a.rsa,
            recommended: !!a.recommended,
            buildDependent: !!(a.build_dependent ?? known?.buildDependent),
        }
    })
}

export function findAlgorithm(name, algorithms = DNSSEC_ALGORITHMS) {
    const n = String(name ?? '').toUpperCase()
    return algorithms.find((a) => a.name === n || String(a.number) === n) || null
}

export function isRsa(name, algorithms = DNSSEC_ALGORITHMS) {
    const a = findAlgorithm(name, algorithms)
    return a ? !!a.rsa : /^RSA/i.test(String(name ?? ''))
}

/** "ECDSAP256SHA256 (13)" – Name und Nummer (eines davon darf fehlen). */
export function algorithmLabel(name, number) {
    const n = name ? String(name).toUpperCase() : ''
    const num = number ?? findAlgorithm(n)?.number ?? null
    if (n && num != null) return `${n} (${num})`
    if (n) return n
    return num != null ? String(num) : '—'
}

function isHexEven(s) {
    return /^[0-9a-fA-F]+$/.test(s) && s.length % 2 === 0
}

/** Client-Validierung (F4 §2.2): { nsec3_iterations?: key, nsec3_salt?: key } – leer = ok. */
export function validateDnssecOptions(o) {
    const errors = {}
    const opts = o || {}
    if ((opts.nsec_mode || 'nsec3') === 'nsec3') {
        const raw = opts.nsec3_iterations
        const s = String(raw ?? '').trim()
        const n = Number(s)
        if (s === '' || !/^\d+$/.test(s) || !Number.isInteger(n) || n < 0 || n > NSEC3_MAX_ITERATIONS) {
            errors.nsec3_iterations = 'dnssec.errIterations'
        }
        const salt = String(opts.nsec3_salt ?? '').trim()
        if (salt !== '' && salt !== '-' && (!isHexEven(salt) || salt.length > SALT_MAX_LENGTH)) {
            errors.nsec3_salt = 'dnssec.errSalt'
        }
    }
    return errors
}

/** Nur der NSEC/NSEC3-Teil (fuer PUT …/nsec3 und als Bestandteil von buildDnssecPayload). */
export function buildNsecPayload(o) {
    const opts = { ...DEFAULT_DNSSEC_OPTIONS, ...(o || {}) }
    if (opts.nsec_mode === 'nsec') {
        return { nsec_mode: 'nsec', nsec3_iterations: 0, nsec3_salt: '-', nsec3_optout: false, nsec3narrow: false }
    }
    const salt = String(opts.nsec3_salt ?? '').trim()
    return {
        nsec_mode: 'nsec3',
        nsec3_iterations: Number.parseInt(String(opts.nsec3_iterations ?? 0), 10) || 0,
        nsec3_salt: salt === '' ? '-' : salt.toLowerCase(),
        nsec3_optout: !!opts.nsec3_optout,
        nsec3narrow: !!opts.nsec3narrow,
    }
}

/** Body fuer POST …/enable bzw. ZoneCreate.dnssec_options: bits nur bei RSA (Default 2048). */
export function buildDnssecPayload(o, algorithms = DNSSEC_ALGORITHMS) {
    const opts = { ...DEFAULT_DNSSEC_OPTIONS, ...(o || {}) }
    const algorithm = String(opts.algorithm || DEFAULT_DNSSEC_OPTIONS.algorithm).toUpperCase()
    const rsa = isRsa(algorithm, algorithms)
    const bits = rsa ? (RSA_BITS.includes(Number(opts.bits)) ? Number(opts.bits) : RSA_BITS_DEFAULT) : null
    return {
        key_model: opts.key_model === 'ksk_zsk' ? 'ksk_zsk' : 'csk',
        algorithm,
        bits,
        ...buildNsecPayload(opts),
    }
}

/** Vorbelegung des NSEC-Dialogs aus dem Status (Salt "-" -> leer). */
export function optionsFromStatus(status) {
    const nsec = status?.nsec || {}
    if (nsec.mode !== 'nsec3') {
        return { ...DEFAULT_DNSSEC_OPTIONS, nsec_mode: 'nsec', nsec3narrow: false }
    }
    const salt = nsec.salt && nsec.salt !== '-' ? String(nsec.salt) : ''
    return {
        ...DEFAULT_DNSSEC_OPTIONS,
        nsec_mode: 'nsec3',
        nsec3_iterations: Number.isInteger(nsec.iterations) ? nsec.iterations : 0,
        nsec3_salt: salt,
        nsec3_optout: !!nsec.opt_out,
        nsec3narrow: !!nsec.narrow,
    }
}

/** 'NSEC' bzw. "NSEC3 – Parameter 1 0 0 -" (Rohwert, wenn PowerDNS etwas Unparsebares liefert). */
export function formatNsec(nsec, t) {
    if (!nsec || !nsec.mode) return '—'
    if (nsec.mode === 'nsec') return t('dnssec.denialNsec')
    return t('dnssec.denialNsec3', { params: nsec.nsec3param || '—' })
}

// ---------------------------------------------------------------------------------------------
// Status, Schluessel, Hinweise

/** 'presigned' | 'off' | 'keysInactive' | 'signed' | null (kein Status). */
export function cardState(status) {
    if (!status) return null
    if (status.presigned) return 'presigned'
    const keys = status.keys || []
    if (keys.length === 0) return 'off'
    return status.signed ? 'signed' : 'keysInactive'
}

/** Frontend-Recht (F4 §2): Zonenrecht + Server "Speichern: Ja" (ctx.canEdit) und Backend-Urteil can_write. */
export function canManageDnssec(canEdit, status) {
    return !!canEdit && status?.can_write === true
}

export function isPrimaryKind(kind) {
    return PRIMARY_KINDS.has(String(kind || '').trim().toLowerCase())
}

/** 'CSK' | 'KSK' | 'ZSK' (PowerDNS-keytype; Fallback ueber die Rolle). */
export function keyTypeLabel(key) {
    const kt = String(key?.keytype || '').toLowerCase()
    if (kt === 'csk' || kt === 'ksk' || kt === 'zsk') return kt.toUpperCase()
    return key?.role === 'zsk' ? 'ZSK' : 'KSK'
}

/** Schluesselmodell der aktiven Schluessel: 'csk' | 'ksk_zsk' | 'mixed' | null. */
export function keyModelOf(keys) {
    const active = (keys || []).filter((k) => k.active)
    if (!active.length) return null
    const roles = new Set(active.map((k) => k.role))
    const types = new Set(active.map((k) => String(k.keytype || '').toLowerCase()))
    if (roles.size === 1 && roles.has('sep') && !types.has('ksk')) return 'csk'
    if (roles.has('sep') && roles.has('zsk')) return 'ksk_zsk'
    return 'mixed'
}

/** Algorithmen der aktiven Schluessel als Liste "NAME (n)". */
export function activeAlgorithms(keys) {
    const seen = new Map()
    for (const k of keys || []) {
        if (!k.active) continue
        const label = algorithmLabel(k.algorithm, k.algorithm_number)
        if (!seen.has(label)) seen.set(label, true)
    }
    return Array.from(seen.keys())
}

/** Hinweise nach Stufe sortiert (danger, warning, info); `exclude` = Codes, die hier nicht erscheinen. */
export function sortedHints(hints, { exclude = DIALOG_ONLY_HINTS } = {}) {
    return (hints || [])
        .filter((h) => h && h.code && !exclude.includes(h.code))
        .map((h, i) => ({ h, i }))
        .sort((a, b) => (LEVEL_ORDER[a.h.level] ?? 3) - (LEVEL_ORDER[b.h.level] ?? 3) || a.i - b.i)
        .map(({ h }) => h)
}

/** Text eines Hinweises; unbekannte Codes erscheinen als Code (kein stilles Verschlucken). */
export function hintText(hint, t) {
    const key = HINT_KEYS[hint?.code]
    if (!key) return String(hint?.code || '')
    const params = { ...(hint.params || {}) }
    if (hint.code === 'rollover_in_progress') {
        const phaseKey = PHASE_KEYS[params.phase]
        params.phase = phaseKey ? t(phaseKey) : String(params.phase || '')
    }
    return t(key, params)
}

/** Peers, die angezeigt werden (zone_missing ausgeblendet). */
export function visiblePeers(peers) {
    return (peers || []).filter((p) => p && p.state !== 'zone_missing')
}

/** Zeigt der Status eine laufende Schluesselwechsel-Phase? (Tabelle dann aufgeklappt) */
export function rolloverRunning(status) {
    const r = status?.rollover || {}
    return ['sep', 'zsk'].some((k) => ['new_prepublished', 'both_active', 'old_retired'].includes(r[k]?.phase))
}

/** Peer-Ergebnis beim Neuladen ohne Peer-Vergleich erhalten (peers=false liefert null und keine peers_*-Hinweise). */
export function mergeStatusKeepPeers(next, prev) {
    if (!next || next.peers != null || !prev || prev.peers == null) return next
    const peerHints = (prev.hints || []).filter((h) => String(h.code).startsWith('peers_'))
    return { ...next, peers: prev.peers, hints: [...(next.hints || []), ...peerHints] }
}

/** Key-Tags der aktuell beim Registrar erwarteten SEP-Schluessel (ds_status current). */
export function currentSepTags(keys) {
    return (keys || [])
        .filter((k) => k.role === 'sep' && k.ds_status === 'current' && k.key_tag != null)
        .map((k) => k.key_tag)
}

// ---------------------------------------------------------------------------------------------
// DS-Assistent

function dsLines(key) {
    return (key?.ds || []).filter((d) => d && d.ds)
}

/** Empfohlene DS-Zeile eines Schluessels: Digest-Typ 2 (SHA-256), sonst die erste lesbare. */
export function recommendedDs(key) {
    const lines = dsLines(key).filter((d) => d.parsed && !d.parsed.error)
    return lines.find((d) => (d.digest_type ?? d.parsed.digest_type) === 2) || lines[0] || null
}

/** Gruppen fuer den DS-Assistenten: je SEP-Schluessel mit DS, sortiert nach ID; `recommended` nur bei current. */
export function dsGroups(keys, { showAll = false } = {}) {
    return (keys || [])
        .filter((k) => k.role === 'sep' && dsLines(k).length > 0)
        .sort((a, b) => a.id - b.id)
        .map((k) => {
            const lines = dsLines(k)
            const visible = showAll ? lines : lines.filter((d) => (d.digest_type ?? d.parsed?.digest_type) === 2)
            return {
                key: k,
                status: k.ds_status,
                lines: visible.length ? visible : lines,
                hiddenCount: showAll ? 0 : Math.max(0, lines.length - (visible.length || lines.length)),
                hasSha1: lines.some((d) => (d.digest_type ?? d.parsed?.digest_type) === 1),
                recommended: k.ds_status === 'current' ? recommendedDs(k) : null,
            }
        })
}

/** Veroeffentlichte SEP-Schluessel mit DNSKEY (Block "Falls der Registrar DNSKEY verlangt"). */
export function publishedSepKeys(keys) {
    return (keys || []).filter((k) => k.role === 'sep' && k.published_effective !== false && k.dnskey)
}

/** DNSKEY-String in Felder zerlegen: { flags, protocol, algorithm, publicKey } oder null. */
export function parseDnskey(dnskey) {
    const m = /^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+)$/s.exec(String(dnskey || ''))
    if (!m) return null
    return { flags: Number(m[1]), protocol: Number(m[2]), algorithm: Number(m[3]), publicKey: m[4].replace(/\s+/g, '') }
}

// ---------------------------------------------------------------------------------------------
// Zeiten (Rollover-Wartezeiten)

/** SOA-Minimum und SOA-TTL aus der flachen Record-Liste (listRecords); null ohne SOA. */
export function soaTimings(records) {
    const soa = (records || []).find((r) => r && String(r.type).toUpperCase() === 'SOA')
    if (!soa) return null
    const parts = String(soa.content || '').trim().split(/\s+/)
    const minimum = parts.length >= 7 ? Number.parseInt(parts[6], 10) : NaN
    return {
        minimum: Number.isFinite(minimum) ? minimum : null,
        ttl: Number.isFinite(Number(soa.ttl)) ? Number(soa.ttl) : null,
    }
}

const SIGNATURE_TYPES = new Set(['RRSIG', 'NSEC', 'NSEC3', 'NSEC3PARAM'])

/** Groesste TTL aller Records (ohne RRSIG/NSEC*); null ohne Records. */
export function maxRecordTtl(records) {
    let max = null
    for (const r of records || []) {
        if (!r || SIGNATURE_TYPES.has(String(r.type).toUpperCase())) continue
        const ttl = Number(r.ttl)
        if (Number.isFinite(ttl) && (max === null || ttl > max)) max = ttl
    }
    return max
}

/** Dauer in Worten: >= 1 d Tage+Stunden, >= 1 h Stunden+Minuten, >= 1 min Minuten, sonst Sekunden. */
export function formatDuration(seconds, t) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0))
    const d = Math.floor(s / 86400)
    const h = Math.floor((s % 86400) / 3600)
    const m = Math.floor((s % 3600) / 60)
    if (d >= 1) return t('dnssec.durationDays', { d, h })
    if (s >= 3600) return t('dnssec.durationHours', { h: Math.floor(s / 3600), m })
    if (s >= 60) return t('dnssec.durationMinutes', { m: Math.floor(s / 60) })
    return t('dnssec.durationSeconds', { s })
}

/** Sekunden seit einem ISO-Zeitpunkt; null bei ungueltigem Wert. */
export function sinceSeconds(isoString, now = Date.now()) {
    if (!isoString) return null
    const ms = Date.parse(isoString)
    if (!Number.isFinite(ms)) return null
    return Math.max(0, (now - ms) / 1000)
}

// ---------------------------------------------------------------------------------------------
// Schutzregeln (409) und Folgeaktionen (Serial/NOTIFY)

/** Schutzregel-Detail { message, code, force_possible } bei 409 mit force_possible, sonst null. */
export function forceInfo(err) {
    const detail = err?.payload?.detail
    if (err?.status === 409 && detail && typeof detail === 'object' && detail.force_possible) return detail
    return null
}

/** Grund einer Schutzregel (uebersetzt, Fallback Backend-Text). */
export function protectionReason(detail, t) {
    const key = PROTECTION_KEYS[detail?.code]
    return key ? t(key) : String(detail?.message || detail?.code || '')
}

/**
 * Fuehrt op(force) aus; bei einer Schutzregel (409 force_possible) Rueckfrage und Wiederholung mit force=true.
 * Abbruch der Rueckfrage -> null. Andere Fehler werden geworfen.
 */
export async function withForce(op, t, confirmFn = (msg) => globalThis.window?.confirm(msg)) {
    try {
        return await op(false)
    } catch (err) {
        const info = forceInfo(err)
        if (!info) throw err
        if (!confirmFn(t('dnssec.forceConfirm', { reason: protectionReason(info, t) }))) return null
        return await op(true)
    }
}

/** Ergebnis der Serial-Erhoehung/NOTIFY einer Mutation: { info: [text], warnings: [text] }. */
export function followUpMessages(details, t) {
    const d = details || {}
    const info = []
    const warnings = []
    if (d.serial_bumped) info.push(t('dnssec.followSerialBumped', { serial: d.serial ?? '?' }))
    if (d.serial_error) warnings.push(t('dnssec.followSerialError', { error: d.serial_error }))
    if (d.notified) info.push(t('dnssec.followNotified'))
    if (d.notify_error) warnings.push(t('dnssec.followNotifyError', { error: d.notify_error }))
    return { info, warnings }
}

/** bump_serial fuer den Request: bei Master/Producer der Schalter, sonst weglassen (Backend ignoriert). */
export function bumpSerialValue(kind, enabled) {
    return isPrimaryKind(kind) ? !!enabled : undefined
}

// ---------------------------------------------------------------------------------------------
// Rollover-Assistent (F4 §2.5/2.6 mit Plan [D10]: DNSKEY-Pruefung vor DS-Tausch und vor dem Loeschen)

export const ROLLOVER_STEPS = Object.freeze({
    sep: ['prepublish', 'dnskeyCheck', 'dsAdd', 'switch', 'dsRemove', 'dnskeyCheckDelete', 'delete'],
    zsk: ['prepublish', 'waitDnskey', 'dnskeyCheck', 'switch', 'waitMaxTtl', 'dnskeyCheckDelete', 'delete'],
})

export const ROLLOVER_STEP_KEYS = Object.freeze({
    prepublish: 'dnssec.rolloverStepPrepublish',
    dnskeyCheck: 'dnssec.rolloverStepDnskeyCheck',
    dnskeyCheckDelete: 'dnssec.rolloverStepDnskeyCheck',
    dsAdd: 'dnssec.rolloverStepDsAdd',
    switch: 'dnssec.rolloverStepSwitch',
    dsRemove: 'dnssec.rolloverStepDsRemove',
    delete: 'dnssec.rolloverStepDelete',
    waitDnskey: 'dnssec.rolloverStepWaitDnskey',
    waitMaxTtl: 'dnssec.rolloverStepWaitMaxTtl',
})

/**
 * Aktueller Schritt (Index in ROLLOVER_STEPS[kind]) aus Phase und den Haken des Nutzers.
 * checks = { dnskey, ds, waited, dnskeyDelete, dsRemoved }. -1 = kein Assistentenschritt (no_active/complex/kein Track).
 * `dnskey`/`dnskeyDelete` kommen bei WS-F4-C aus effectiveRolloverChecks (Pruefergebnis statt Checkbox).
 */
export function rolloverStepIndex(kind, track, checks = {}) {
    const steps = ROLLOVER_STEPS[kind] || ROLLOVER_STEPS.sep
    const at = (name) => steps.indexOf(name)
    switch (track?.phase) {
    case 'idle':
        return at('prepublish')
    case 'new_prepublished':
        if (kind === 'zsk') {
            if (!checks.waited) return at('waitDnskey')
            return checks.dnskey ? at('switch') : at('dnskeyCheck')
        }
        if (!checks.dnskey) return at('dnskeyCheck')
        return checks.ds ? at('switch') : at('dsAdd')
    case 'both_active':
        return at('switch')
    case 'old_retired':
        if (kind === 'zsk') {
            if (!checks.waited) return at('waitMaxTtl')
            return checks.dnskeyDelete ? at('delete') : at('dnskeyCheckDelete')
        }
        if (!checks.dsRemoved) return at('dsRemove')
        return checks.dnskeyDelete ? at('delete') : at('dnskeyCheckDelete')
    default:
        return -1
    }
}

/** Welche Art steht zur Wahl? { sep: bool, zsk: bool, manual: bool } (manual = Algorithmuswechsel). */
export function rolloverKinds(status) {
    const r = status?.rollover || {}
    return { sep: !!r.sep, zsk: !!r.zsk, manual: !!r.algorithm_rollover }
}

/** Darf die Aktion der aktuellen Phase ausgefuehrt werden? (Pflicht-Haken laut Phase) */
export function rolloverActionAllowed(kind, track, checks = {}) {
    switch (track?.phase) {
    case 'idle':
        return true
    case 'new_prepublished':
        return kind === 'zsk' ? !!(checks.waited && checks.dnskey) : !!(checks.dnskey && checks.ds)
    case 'both_active':
        // KSK/CSK: dieselbe Bestaetigung wie vor dem Umschalten (neuer DS steht beim Registrar)
        return kind === 'zsk' ? true : !!checks.ds
    case 'old_retired':
        return kind === 'zsk' ? !!(checks.waited && checks.dnskeyDelete) : !!(checks.dsRemoved && checks.dnskeyDelete)
    default:
        return false
    }
}

// ---------------------------------------------------------------------------------------------
// Teil B (WS-F4-C): DNSKEY-Pruefung auf den autoritativen NS und DS in der Elternzone
//
// Freigabe der Rollover-Schritte "DS tauschen"/"Umschalten" und "alten Schluessel loeschen" [D10]:
//  - Pruefung eingeschaltet (status.capabilities.dnskey_check): frei erst, wenn ALLE Nameserver den neuen Schluessel
//    liefern (Ergebnis von GET …/dnskey-check); sonst nur mit bestaetigtem "Trotzdem fortfahren" (force).
//  - Pruefung ausgeschaltet: Schritt zeigt "nicht geprueft" und verlangt die Checkbox "Ich habe geprueft".
// Haken je Pruefschritt: checks[name] = manuelle Bestaetigung (nur bei ausgeschalteter Pruefung),
// checks[`${name}Force`] = bestaetigtes Fortfahren trotz Fehlschlag.

export const DNSKEY_CHECK_STEPS = Object.freeze(['dnskey', 'dnskeyDelete'])

/**
 * Zustand einer DNSKEY-Pruefung fuer den Schluessel `tag`.
 * result = Antwort von getDnskeyCheck oder null; requestError = Fehlertext der letzten Anfrage.
 * -> { state: 'disabled'|'unchecked'|'ok'|'failed'|'error', rows: [{ ns, ok, missing, serves, error }], truncated }
 */
export function dnskeyCheckOutcome(result, { capability = false, tag = null, requestError = '' } = {}) {
    if (!capability || result?.enabled === false) return { state: 'disabled', rows: [], truncated: false }
    if (requestError) return { state: 'error', rows: [], truncated: false, error: requestError }
    if (!result) return { state: 'unchecked', rows: [], truncated: false }
    const want = tag === null || tag === undefined || tag === '' ? null : Number(tag)
    const rows = Object.entries(result.nameservers || {}).map(([ns, r]) => {
        const serves = Array.isArray(r?.serves_key_tags) ? r.serves_key_tags : []
        const ok = !!r?.ok && (want === null || serves.includes(want))
        const missing = want === null ? (r?.missing_tags || []) : (serves.includes(want) ? [] : [want])
        return { ns, ok, missing, serves, error: r?.error || null }
    })
    const allOk = rows.length > 0 && rows.every((r) => r.ok) && !result.truncated
    return { state: allOk ? 'ok' : 'failed', rows, truncated: !!result.truncated }
}

/** Ist der DNSKEY-Schritt erfuellt? disabled -> manuelle Checkbox; ok -> automatisch; failed/error -> nur force. */
export function dnskeyStepPassed(outcome, { confirmed = false, forced = false } = {}) {
    switch (outcome?.state) {
    case 'disabled':
        return !!confirmed
    case 'ok':
        return true
    case 'failed':
    case 'error':
        return !!forced
    default:
        return false
    }
}

/**
 * Haken fuer rolloverStepIndex/rolloverActionAllowed: `dnskey`/`dnskeyDelete` ergeben sich aus dem Pruefergebnis
 * (outcomes[name]); ohne Ergebnis (Altaufrufer) gilt die manuelle Checkbox wie bei ausgeschalteter Pruefung.
 */
export function effectiveRolloverChecks(checks = {}, outcomes = {}) {
    const out = { ...checks }
    for (const name of DNSKEY_CHECK_STEPS) {
        out[name] = dnskeyStepPassed(outcomes[name] || { state: 'disabled' }, {
            confirmed: checks[name], forced: checks[`${name}Force`],
        })
    }
    return out
}

/** Anzeigename eines Resolvers: "Cloudflare (1.1.1.1)" bzw. die IP. */
export function resolverDisplay(row) {
    const ip = String(row?.resolver ?? row ?? '')
    return row?.label ? `${row.label} (${ip})` : ip
}

/** Resolver-Zeilen der Elternzonen-Pruefung -> [{ resolver, kind: 'ds'|'none'|'error', tags, error }]. */
export function parentDsRows(result) {
    return (result?.resolvers || []).map((r) => {
        const resolver = resolverDisplay(r)
        if (r.status === 'ok') return { resolver, kind: 'ds', tags: (r.key_tags || []).join(', ') || '?', error: null }
        if (r.status === 'nodata' || r.status === 'nxdomain') return { resolver, kind: 'none', tags: '', error: null }
        return { resolver, kind: 'error', tags: '', error: r.error || r.status }
    })
}

/**
 * Sichtbarkeit des DS eines eigenen Schluessels bei den Resolvern.
 * -> { tag, visibleOn: [Anzeige], missingOn: [Anzeige], visibleAll: bool, answered: bool } oder null.
 */
export function parentDsKeySummary(result, keyId) {
    if (!result?.enabled || keyId === null || keyId === undefined) return null
    const entry = result.keys?.[String(keyId)]
    const byIp = Object.fromEntries((result.resolvers || []).map((r) => [r.resolver, resolverDisplay(r)]))
    const show = (ips) => (ips || []).map((ip) => byIp[ip] || ip)
    const visibleOn = show(entry?.visible_on)
    const missingOn = show(entry?.missing_on)
    return {
        tag: entry?.key_tag ?? null,
        visibleOn,
        missingOn,
        visibleAll: visibleOn.length > 0 && missingOn.length === 0,
        answered: visibleOn.length + missingOn.length > 0,
    }
}

/** Neuer Schluessel fuer den Start eines Wechsels: gleicher Typ/Algorithmus wie der alte, veroeffentlicht, inaktiv. */
export function rolloverNewKeyBody(oldKey) {
    const algorithm = oldKey?.algorithm ? String(oldKey.algorithm).toUpperCase() : DEFAULT_DNSSEC_OPTIONS.algorithm
    const body = {
        keytype: ['csk', 'ksk', 'zsk'].includes(String(oldKey?.keytype || '').toLowerCase())
            ? String(oldKey.keytype).toLowerCase()
            : (oldKey?.role === 'zsk' ? 'zsk' : 'csk'),
        algorithm,
        active: false,
        published: true,
    }
    if (isRsa(algorithm)) body.bits = RSA_BITS.includes(Number(oldKey?.bits)) ? Number(oldKey.bits) : RSA_BITS_DEFAULT
    return body
}

// ---------------------------------------------------------------------------------------------
// Zone anlegen (ZonesPage): Ergebnis je Server

export const DNSSEC_ERROR_PREFIX = 'created; dnssec-error: '
export const DNSSEC_SKIPPED = 'created; dnssec-skipped'

/** Ergebnis-Wert von POST /zones je Server -> { kind, detail }. kind: created|synced|error|dnssecError|dnssecSkipped|other */
export function parseCreateResult(value) {
    const v = String(value ?? '')
    if (v === 'created') return { kind: 'created', detail: '' }
    if (v === 'synced') return { kind: 'synced', detail: '' }
    if (v.startsWith(DNSSEC_ERROR_PREFIX)) return { kind: 'dnssecError', detail: v.slice(DNSSEC_ERROR_PREFIX.length) }
    if (v === DNSSEC_SKIPPED) return { kind: 'dnssecSkipped', detail: '' }
    if (v.startsWith('error:')) return { kind: 'error', detail: v.replace(/^error:\s*/, '') }
    return { kind: 'other', detail: v }
}

/** Server, auf dem DNSSEC eingerichtet wurde (erster mit created bzw. created; dnssec-error). */
export function dnssecServerOf(details) {
    for (const [srv, value] of Object.entries(details || {})) {
        const k = parseCreateResult(value).kind
        if (k === 'created' || k === 'dnssecError') return srv
    }
    return null
}

export function hasDnssecWarning(details) {
    return Object.values(details || {}).some((v) => {
        const k = parseCreateResult(v).kind
        return k === 'dnssecError' || k === 'dnssecSkipped'
    })
}
