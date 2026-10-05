// Reine Hilfen fuer Record-Inhalte im Record-Dialog (F8-F03, F04, F09, F10), ohne React/i18n-Import,
// damit sie per `node --test` geprueft werden koennen (tests/lib-f8.test.mjs). zoneDetailModel.js baut
// RECORD_TYPES und die (uebersetzten) Validatoren darauf auf.

// CAA-Tags fuer die Auswahl (RFC 8659 + RFC 9495 contactemail/contactphone, issuemail RFC 9495)
export const CAA_TAGS = Object.freeze(['issue', 'issuewild', 'issuemail', 'iodef', 'contactemail', 'contactphone'])
// Im Warnhinweis fuer ungewoehnliche Tags genannte, gebraeuchliche Tags
export const CAA_COMMON_TAGS = Object.freeze(['issue', 'issuewild', 'iodef'])

/** CAA-Wert bauen: Flag, Tag und Wert in Anfuehrungszeichen; " und \ im Wert werden escaped. */
export function buildCaa({ flag, tag, val } = {}) {
    const escaped = String(val ?? '').replace(/\\/g, '\\\\').replace(/"/g, '\\"')
    return `${String(flag ?? '').trim()} ${String(tag ?? '').trim()} "${escaped}"`
}

/**
 * CAA-Wert lesen (Gegenstueck zu buildCaa): umschliessende " entfernen, \" -> ", \\ -> \.
 * Passt der Inhalt nicht zum Schema "<flag> <tag> <wert>", wird der ganze Inhalt als Wert mit Flag 0 / Tag issue
 * uebernommen (nichts geht verloren, der Nutzer sieht den Rohwert).
 */
export function parseCaa(content) {
    const c = String(content ?? '').trim()
    const m = /^(\d+)\s+(\S+)\s+(.*)$/.exec(c)
    if (!m) return { flag: '0', tag: 'issue', val: c }
    let val = m[3].trim()
    if (val.length >= 2 && val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1)
    // Escapes in einem Durchlauf aufloesen (\\ und \" sowie jedes andere \x -> x)
    val = val.replace(/\\(.)/g, '$1')
    return { flag: m[1], tag: m[2], val }
}

/** Startwerte eines Wert-Sets: { [feld.id]: feld.default } fuer alle Felder mit `default` (z. B. CAA flag/tag). */
export function defaultFieldSet(def) {
    const out = {}
    for (const f of def?.fields || []) {
        if (f && f.default !== undefined) out[f.id] = f.default
    }
    return out
}

/** Optionen eines Auswahlfelds; ein unbekannter aktueller Wert (z. B. CAA-Tag aus einem Import) steht vorne. */
export function selectOptions(options, current) {
    const list = Array.isArray(options) ? [...options] : []
    const cur = current === undefined || current === null ? '' : String(current)
    if (cur && !list.includes(cur)) list.unshift(cur)
    return list
}

// Hostname-Pruefung (F8-F10): Unterstriche (z. B. _dmarc, Service-Labels), Punycode-TLDs (xn--p1ai)
export const FQDN_RE = /^(?=.{1,253}\.?$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+([a-z]{2,63}|xn--[a-z0-9-]{1,59})\.?$/i
export const SINGLE_LABEL_RE = /^[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.?$/i

/**
 * Hostname einordnen: 'empty' | 'root' ('.' = Null-Ziel, nur mit allowRoot) | 'valid' | 'singleLabel' (nur Warnung) |
 * 'invalid'.
 */
export function classifyHostname(value, { allowRoot = false } = {}) {
    const raw = String(value ?? '').trim()
    if (allowRoot && raw === '.') return 'root'
    const s = raw.replace(/\.$/, '')
    if (!s) return 'empty'
    if (FQDN_RE.test(`${s}.`)) return 'valid'
    if (SINGLE_LABEL_RE.test(s)) return 'singleLabel'
    return 'invalid'
}

/** Wert fuer Rueckfragen kuerzen (Standard 80 Zeichen, mit …). */
export function truncateValue(value, max = 80) {
    const s = String(value ?? '')
    if (s.length <= max) return s
    return `${s.slice(0, Math.max(0, max - 1))}…`
}

/** Anzahl der Werte eines RRsets (Name ohne Gross-/Kleinschreibung, mit oder ohne Punkt am Ende). */
export function rrsetValueCount(records, name, type) {
    const key = String(name ?? '').trim().toLowerCase().replace(/\.$/, '')
    return (records || []).filter(
        (r) => r && r.type === type && String(r.name ?? '').toLowerCase().replace(/\.$/, '') === key,
    ).length
}

// Fehlercodes von lib/dnsName.normalizeRecordName -> i18n-Keys
export const NAME_ERROR_KEYS = Object.freeze({
    whitespace: 'zoneDetail.nameWhitespace',
    emptyLabel: 'zoneDetail.nameEmptyLabel',
    labelTooLong: 'zoneDetail.nameLabelTooLong',
    tooLong: 'zoneDetail.nameTooLong',
    outsideZone: 'zoneDetail.nameOutsideZone',
})
