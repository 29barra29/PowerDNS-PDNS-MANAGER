// LUA-Records (F15 6.2) - reine Hilfsfunktionen ohne React/i18n, per `node --test` pruefbar
// (tests/luaRecord.test.mjs). Die Listen und der Klammer-Tokenizer muessen zum Backend passen
// (backend/app/services/lua_records.py: LUA_TARGET_TYPES, LUA_MAX_CONTENT_LENGTH, GEO_FUNCTIONS,
// check_lua_brackets) - der Sync-Test in backend/tests/test_lua_records.py und tests/luaRecord.test.mjs prueft das.
//
// Format eines LUA-Inhalts (PowerDNS): <Ziel-Typ> "<Lua-Code>" (der Code kann aus mehreren "…"-Abschnitten bestehen,
// PowerDNS verbindet sie mit einem Leerzeichen). Das Panel speichert den Code immer einzeilig in einem Abschnitt.
//
// Ausserdem: Filter der Record-Tabelle (F15 2.5, allgemein fuer alle Typen) und Hilfen fuer den Server-Status.

export const LUA_TARGET_TYPES = ['A', 'AAAA', 'CNAME', 'TXT', 'MX', 'SRV', 'PTR', 'CAA', 'NAPTR', 'LOC', 'SPF', 'HTTPS', 'SVCB', 'SSHFP', 'TLSA']
export const LUA_MAX_CONTENT_LENGTH = 4000
export const GEO_FUNCTIONS = ['pickclosest', 'country', 'countryCode', 'continent', 'continentCode', 'region', 'regionCode', 'latlon', 'latlonloc', 'closestMagic', 'asnum']

// 403-Texte der LUA-Policy (Backend services/lua_records.MSG_DENIED_*, Sync-Test im Backend): die Oberflaeche zeigt
// statt des deutschen Backend-Texts den uebersetzten (Review-Fund W3-L8)
export const LUA_DENIED_MESSAGES = Object.freeze({
    admin: 'LUA-Records dürfen nur Administratoren anlegen oder ändern.',
    disabled: 'LUA-Records sind in diesem Panel deaktiviert (Einstellungen → DNS-Optionen).',
})

/** i18n-Key fuer einen 403-Text der LUA-Policy oder null (anderer Fehler). */
export function luaDeniedKey(message) {
    const m = String(message ?? '').trim()
    if (m === LUA_DENIED_MESSAGES.disabled) return 'lua.notAllowedDisabled'
    if (m === LUA_DENIED_MESSAGES.admin) return 'lua.notAllowedAdmin'
    return null
}

/** Fehlertext einer Record-Aktion: LUA-Policy-Texte uebersetzt, sonst die Meldung selbst. */
export function translateLuaDenied(message, t) {
    const key = luaDeniedKey(message)
    return key ? t(key) : message
}

const TYPE_RE = /^\s*([A-Za-z0-9]+)\s+([\s\S]*?)\s*$/
const CHUNK = '"[^"\\\\]*(?:\\\\.[^"\\\\]*)*"'
const CHUNKS_RE = new RegExp(`^${CHUNK}(?:\\s+${CHUNK})*$`)
const CHUNK_CONTENT_RE = /"((?:[^"\\]|\\.)*)"/g
const LONG_OPEN_RE = /\[(=*)\[/y
const PAIRS = { ')': '(', ']': '[', '}': '{' }
const GEO_RE = new RegExp(`\\b(${GEO_FUNCTIONS.join('|')})\\s*\\(`)

/**
 * LUA-Inhalt zerlegen: { rtype, code, parsed }. parsed=false, wenn der Rest nicht aus "…"-Abschnitten besteht
 * (dann ist code der Rohrest bzw. der ganze Inhalt). Mehrere Abschnitte werden mit Leerzeichen verbunden.
 */
export function parseLuaContent(content) {
    const s = String(content ?? '')
    const m = TYPE_RE.exec(s)
    if (m && CHUNKS_RE.test(m[2])) {
        const chunks = [...m[2].matchAll(CHUNK_CONTENT_RE)].map((x) => x[1])
        return { rtype: m[1].toUpperCase(), code: chunks.join(' '), parsed: true }
    }
    return { rtype: m?.[1]?.toUpperCase() || 'A', code: (m ? m[2] : '') || s, parsed: false }
}

/** Code einzeilig machen: Zeilen trimmen, Tabs -> Leerzeichen, leere Zeilen weglassen, mit ' ' verbinden. */
export function luaCodeOneLine(code) {
    return String(code ?? '')
        .split(/\r?\n|\r/)
        .map((line) => line.replace(/\t/g, ' ').trim())
        .filter(Boolean)
        .join(' ')
}

/** Inhalt fuer PowerDNS bauen: `${RTYPE} "${einzeiliger Code}"`. */
export function buildLuaContent(rtype, code) {
    return `${String(rtype || 'A').toUpperCase()} "${luaCodeOneLine(code)}"`
}

function matchLongOpen(code, at) {
    LONG_OPEN_RE.lastIndex = at
    return LONG_OPEN_RE.exec(code)
}

/**
 * Klammern/Strings/Kommentare pruefen (identisch zu check_lua_brackets im Backend).
 * Rueckgabe: null | 'unbalanced' | 'unterminated_string' | 'unterminated_comment'
 */
export function checkLuaBrackets(code) {
    const s = String(code ?? '')
    const stack = []
    let i = 0
    const n = s.length
    while (i < n) {
        const c = s[i]
        if (s.startsWith('--', i)) {
            const m = matchLongOpen(s, i + 2)
            if (m) {
                const close = `]${m[1]}]`
                const j = s.indexOf(close, i + 2 + m[0].length)
                if (j < 0) return 'unterminated_comment'
                i = j + close.length
                continue
            }
            // Zeilenkommentar: Rest ist Kommentar (Code ist einzeilig)
            return stack.length ? 'unbalanced' : null
        }
        if (c === "'" || c === '"') {
            const q = c
            i += 1
            while (i < n && s[i] !== q) i += s[i] === '\\' ? 2 : 1
            if (i >= n) return 'unterminated_string'
            i += 1
            continue
        }
        if (c === '[') {
            const m = matchLongOpen(s, i)
            if (m) {
                const close = `]${m[1]}]`
                const j = s.indexOf(close, i + m[0].length)
                if (j < 0) return 'unterminated_string'
                i = j + close.length
                continue
            }
        }
        if (c === '(' || c === '[' || c === '{') stack.push(c)
        else if (c === ')' || c === ']' || c === '}') {
            if (!stack.length || stack.pop() !== PAIRS[c]) return 'unbalanced'
        }
        i += 1
    }
    return stack.length ? 'unbalanced' : null
}

const BRACKET_ERROR_CODES = {
    unbalanced: 'unbalanced',
    unterminated_string: 'unterminatedString',
    unterminated_comment: 'unterminatedComment',
}

/**
 * Live-Pruefung im Editor. Rueckgabe { errors: string[], warnings: string[] } mit Codes
 * (i18n: lua.err.<code> / lua.warn.<code>):
 *   errors:   empty, doubleQuote, unbalanced, unterminatedString, unterminatedComment, tooLong, targetType
 *   warnings: lineComment (mehrzeiliger Code mit --), backslash
 */
export function analyzeLuaCode(rtype, code) {
    const errors = []
    const warnings = []
    const raw = String(code ?? '')
    const oneLine = luaCodeOneLine(raw)
    const t = String(rtype || '').toUpperCase()
    if (!LUA_TARGET_TYPES.includes(t)) errors.push('targetType')
    if (!oneLine.trim()) {
        errors.push('empty')
        return { errors, warnings }
    }
    if (raw.includes('"')) errors.push('doubleQuote')
    else {
        const problem = checkLuaBrackets(oneLine)
        if (problem) errors.push(BRACKET_ERROR_CODES[problem])
    }
    if (buildLuaContent(t || 'A', raw).length > LUA_MAX_CONTENT_LENGTH) errors.push('tooLong')
    const lines = raw.split(/\r?\n|\r/).filter((l) => l.trim())
    if (lines.length > 1 && raw.includes('--')) warnings.push('lineComment')
    if (raw.includes('\\')) warnings.push('backslash')
    return { errors, warnings }
}

/** True, wenn der Code Geo-Funktionen nutzt (brauchen das geoip-Backend). */
export function usesGeoFunctions(code) {
    return GEO_RE.test(String(code ?? ''))
}

// Vorlagen (F15 6.4). Texte: lua.templates.<id>.label / .desc, Gruppen: lua.templateGroup.<group>.
export const LUA_TEMPLATE_GROUPS = ['availability', 'distribution', 'geo']
export const LUA_TEMPLATES = [
    { id: 'ifportup', group: 'availability', rtype: 'A', geo: false, code: "ifportup(443, {'192.0.2.1', '192.0.2.2'})" },
    { id: 'ifportup6', group: 'availability', rtype: 'AAAA', geo: false, code: "ifportup(443, {'2001:db8::1', '2001:db8::2'})" },
    { id: 'ifurlup', group: 'availability', rtype: 'A', geo: false, code: "ifurlup('https://www.example.com/health', {{'192.0.2.1', '192.0.2.2'}, {'198.51.100.1'}}, {stringmatch='OK'})" },
    { id: 'pickwrandom', group: 'distribution', rtype: 'A', geo: false, code: "pickwrandom({{70, '192.0.2.1'}, {30, '192.0.2.2'}})" },
    { id: 'view', group: 'distribution', rtype: 'A', geo: false, code: "view({{{'10.0.0.0/8', '192.168.0.0/16'}, {'10.0.0.10'}}, {{'0.0.0.0/0', '::/0'}, {'203.0.113.10'}}})" },
    { id: 'pickclosest', group: 'geo', rtype: 'A', geo: true, code: "pickclosest({'192.0.2.1', '198.51.100.1', '203.0.113.1'})" },
    { id: 'country', group: 'geo', rtype: 'A', geo: true, code: ";if country({'DE', 'AT', 'CH'}) then return '192.0.2.10' end return '198.51.100.10'" },
    { id: 'continent', group: 'geo', rtype: 'A', geo: true, code: ";if continent('EU') then return {'192.0.2.10'} else return {'198.51.100.10'} end" },
    { id: 'latlon', group: 'geo', rtype: 'TXT', geo: true, code: 'latlon()' },
]

export function luaTemplateById(id) {
    return LUA_TEMPLATES.find((tpl) => tpl.id === id) || null
}

// ------------------------------------------------------------------ Server-Status (GET /lua/server-status)

/** Relevante Server fuer die Warnungen: aktueller Server + alle schreibbaren, erreichbaren Peers (F15 2.2-3). */
export function relevantLuaServers(server, allServers) {
    const out = server ? [server] : []
    for (const s of allServers || []) {
        if (!s || s.name === server) continue
        if (s.allow_writes !== false && s.is_reachable) out.push(s.name)
    }
    return out
}

/** Status-Eintrag eines Servers aus der Antwort (oder null). */
export function luaStatusFor(status, name) {
    return (status?.servers || []).find((s) => s?.name === name) || null
}

/**
 * Zeile der Warnbox fuer einen Server: { server, kind, mode, error }
 * kind: 'enabled' (yes/shared) | 'disabled' (no) | 'unknown' (kein Eintrag, Fehler oder Wert unbekannt).
 */
export function luaStatusLine(status, name) {
    const entry = luaStatusFor(status, name)
    if (!entry) return { server: name, kind: 'unknown', mode: null, error: null }
    if (entry.lua_records === 'yes' || entry.lua_records === 'shared') {
        return { server: name, kind: 'enabled', mode: entry.lua_records, error: null }
    }
    if (entry.lua_records === 'no') return { server: name, kind: 'disabled', mode: 'no', error: null }
    return { server: name, kind: 'unknown', mode: null, error: entry.error || null }
}

/** Relevante Server, die LUA ausgeschaltet melden (enable-lua-records=no). */
export function luaInactiveServers(status, relevant) {
    return (relevant || []).filter((name) => luaStatusFor(status, name)?.lua_records === 'no')
}

/** Relevante Server ohne geoip-Backend (nur geoip_backend === false zaehlt, unbekannt nicht). */
export function luaServersWithoutGeoip(status, relevant) {
    return (relevant || []).filter((name) => luaStatusFor(status, name)?.geoip_backend === false)
}

// ------------------------------------------------------------------ Filter der Record-Tabelle (F15 2.5)

/** Normalisierter Filter: { text (klein, getrimmt), type }; active = mindestens ein Kriterium. */
export function normalizeRecordFilter(filter) {
    const text = String(filter?.text ?? '').trim().toLowerCase()
    const type = String(filter?.type ?? '')
    return { text, type, active: !!(text || type) }
}

/** Treffer: Typ exakt (falls gewaehlt) UND Suchtext als Teilstring in Name, Typ oder Inhalt. */
export function recordMatchesFilter(record, filter) {
    const f = normalizeRecordFilter(filter)
    if (f.type && record?.type !== f.type) return false
    if (!f.text) return true
    return [record?.name, record?.type, record?.content].some((v) => String(v ?? '').toLowerCase().includes(f.text))
}

export function filterRecords(records, filter) {
    const f = normalizeRecordFilter(filter)
    if (!f.active) return records || []
    return (records || []).filter((r) => recordMatchesFilter(r, f))
}

/** Typ-Optionen des Filters: vorhandene Typen, bekannte in der Reihenfolge `order`, unbekannte alphabetisch hinten. */
export function recordFilterTypes(records, order = []) {
    const present = [...new Set((records || []).map((r) => r?.type).filter(Boolean))]
    const known = order.filter((k) => present.includes(k))
    const unknown = present.filter((k) => !order.includes(k)).sort()
    return [...known, ...unknown]
}
