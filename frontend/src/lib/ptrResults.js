// Auswertung der PTR-Ergebnisse `details.ptr` (F11 §2.5 Schritt 4, §3.8) und der Live-Pruefung GET /ptr/lookup.
// Rein (kein React, kein i18n-Import): Texte entstehen erst mit der uebergebenen Funktion `t`, damit die Regeln
// per `node --test` pruefbar sind (tests/ptrResults.test.mjs). Nutzer: Record-Dialog (form-extensions/ptr.ext.jsx),
// Papierkorb (zoneDetail/useZoneData.js), Bulk-Editor (F1).
//
// PtrResult (Backend): { ip, ptr, zone, target, op: 'set'|'remove',
//   action: 'set'|'removed'|'unchanged'|'skipped'|'error',
//   reason: null|'no_reverse_zone'|'classless'|'forbidden'|'conflict'|'other_target'|'disabled'|'wildcard'|'limit'|'error',
//   existing: [..], classless_zone, fanout: {..}, detail }
//
// Regeln:
//   set / removed                                         -> ok      (an die Erfolgsmeldung angehaengt)
//   skipped mit conflict|classless|forbidden|other_target|limit -> warning (gelbes Banner, bleibt stehen)
//   error (action oder reason)                            -> error   (ebenfalls im gelben Banner, der Forward-Write war ok)
//   unchanged, skipped mit no_reverse_zone|disabled|wildcard -> still
//   unbekannte Aktion/Begruendung                         -> warning mit Rohwert (nie verschlucken)

export const PTR_WARN_REASONS = Object.freeze(['conflict', 'classless', 'forbidden', 'other_target', 'limit'])
export const PTR_SILENT_REASONS = Object.freeze(['no_reverse_zone', 'disabled', 'wildcard'])
export const PTR_KNOWN_REASONS = Object.freeze([...PTR_WARN_REASONS, ...PTR_SILENT_REASONS, 'error'])

const stripDot = (s) => String(s || '').replace(/\.$/, '')

function joinExisting(existing) {
    if (!Array.isArray(existing)) return ''
    return existing.map(stripDot).filter(Boolean).join(', ')
}

// details.ptr aus einer Antwort (MessageResponse.details) oder direkt die Liste.
export function extractPtrList(detailsOrList) {
    if (Array.isArray(detailsOrList)) return detailsOrList
    const list = detailsOrList && typeof detailsOrList === 'object' ? detailsOrList.ptr : null
    return Array.isArray(list) ? list : []
}

// i18n-Key + Werte fuer die Begruendung eines uebersprungenen PTR. Fehlt der Zonen- bzw. Zielname
// (z. B. weil der Aufrufer die Reverse-Zone nicht lesen darf, Plan [S5]), gilt die Variante `<reason>_plain`.
export function ptrReasonKey(entry) {
    const reason = entry?.reason
    if (!reason || !PTR_KNOWN_REASONS.includes(reason)) return null
    if (reason === 'classless' || reason === 'forbidden') {
        const zone = stripDot(reason === 'classless' ? (entry.classless_zone || entry.zone) : entry.zone)
        return zone ? { key: `ptr.reason.${reason}`, values: { zone } } : { key: `ptr.reason.${reason}_plain`, values: {} }
    }
    if (reason === 'conflict' || reason === 'other_target') {
        const existing = joinExisting(entry.existing)
        return existing
            ? { key: `ptr.reason.${reason}`, values: { existing } }
            : { key: `ptr.reason.${reason}_plain`, values: {} }
    }
    return { key: `ptr.reason.${reason}`, values: {} }
}

function classify(entry) {
    const action = entry?.action
    const reason = entry?.reason
    if (action === 'set' || action === 'removed') return 'ok'
    if (action === 'unchanged') return 'silent'
    if (action === 'error' || reason === 'error') return 'error'
    if (action === 'skipped') {
        if (PTR_SILENT_REASONS.includes(reason)) return 'silent'
        return 'warning' // bekannte Warn-Gruende und unbekannte Gruende
    }
    return 'warning' // unbekannte Aktion
}

// Eine Zeile (ohne Text): { level: 'ok'|'warning'|'error', key, values, reason?, entry }
function toLine(entry, level) {
    const ip = entry?.ip || ''
    const ptr = stripDot(entry?.ptr)
    const target = stripDot(entry?.target)
    if (level === 'ok') {
        return entry.action === 'set'
            ? { level, key: 'ptr.resultSet', values: { ptr, target, ip }, reason: null, entry }
            : { level, key: 'ptr.resultRemoved', values: { ptr, ip }, reason: null, entry }
    }
    if (level === 'error') {
        return { level, key: 'ptr.resultError', values: { ip, detail: entry?.detail || '' }, reason: null, entry }
    }
    const reasonKey = ptrReasonKey(entry)
    return {
        level,
        key: 'ptr.resultSkipped',
        values: { ip },
        // Text der Begruendung: i18n-Key oder Rohwert (unbekannte Begruendung/Aktion)
        reason: reasonKey || { text: String(entry?.reason || entry?.action || '?') },
        entry,
    }
}

// summarizePtr(ptrList | details) -> { ok, warnings, errors, lines, hasOutput, hasProblems }
//   ok/warnings/errors: Zeilen je Stufe; lines: alle sichtbaren Zeilen in Eingangsreihenfolge.
export function summarizePtr(ptrList) {
    const list = extractPtrList(ptrList)
    const ok = []
    const warnings = []
    const errors = []
    const lines = []
    for (const entry of list) {
        if (!entry || typeof entry !== 'object') continue
        const level = classify(entry)
        if (level === 'silent') continue
        const line = toLine(entry, level)
        lines.push(line)
        if (level === 'ok') ok.push(line)
        else if (level === 'error') errors.push(line)
        else warnings.push(line)
    }
    return {
        ok, warnings, errors, lines,
        hasOutput: lines.length > 0,
        hasProblems: warnings.length + errors.length > 0,
    }
}

// Text einer Zeile mit der i18n-Funktion t(key, values).
export function formatPtrLine(t, line) {
    if (!line) return ''
    if (line.level === 'error') {
        return t(line.key, { ...line.values, detail: line.values.detail || t('ptr.reason.error') })
    }
    if (line.key === 'ptr.resultSkipped') {
        const r = line.reason
        const reason = r && r.key ? t(r.key, r.values) : (r?.text || '')
        return t(line.key, { ...line.values, reason })
    }
    return t(line.key, line.values)
}

// Fertige Texte fuer die Banner der Zonenansicht:
//   success: Zusatz zur Erfolgsmeldung (gesetzte/entfernte PTRs, mit ' · ' verbunden) oder ''
//   warning: Titel `ptr.warningTitle` + je Problem eine Zeile ('\n'-getrennt, Banner rendert whitespace-pre-line) oder ''
export function ptrMessages(t, summary) {
    const s = summary && Array.isArray(summary.lines) ? summary : summarizePtr(summary)
    const success = s.ok.map((line) => formatPtrLine(t, line)).join(' · ')
    const problems = s.lines.filter((line) => line.level !== 'ok').map((line) => `• ${formatPtrLine(t, line)}`)
    const warning = problems.length ? [t('ptr.warningTitle'), ...problems].join('\n') : ''
    return { success, warning }
}

// Ergebnisse an die Banner eines Kontexts (ctx.setSuccess / ctx.setWarning, Updater-Form) anhaengen.
// Liefert die Zusammenfassung. Ohne PTR-Ergebnis passiert nichts.
export function applyPtrMessages(t, detailsOrList, { setSuccess, setWarning } = {}) {
    const summary = summarizePtr(detailsOrList)
    if (!summary.hasOutput) return summary
    const { success, warning } = ptrMessages(t, summary)
    if (success && typeof setSuccess === 'function') setSuccess((prev) => (prev ? `${prev} ${success}` : success))
    if (warning && typeof setWarning === 'function') setWarning((prev) => (prev ? `${prev}\n${warning}` : warning))
    return summary
}

/* ----- Live-Pruefung im Record-Dialog (GET /ptr/lookup) ------------------------------------------- */

// PtrLookupResponse -> { tone: 'ok'|'muted'|'warn', key, values }
//   ok + would=set -> willSet (gruen), ok + unchanged -> muted, ok + conflict -> warn,
//   no_reverse_zone -> muted, classless/forbidden -> warn, error/unbekannt -> muted
export function lookupLine(res, { ip = '', target = '' } = {}) {
    const ipText = res?.ip || ip
    const ptr = stripDot(res?.ptr) || ipText
    const tgt = stripDot(target)
    const zone = stripDot(res?.zone)
    switch (res?.status) {
    case 'ok':
        if (res.would === 'set') return { tone: 'ok', key: 'ptr.lookupWillSet', values: { ptr, target: tgt, zone } }
        if (res.would === 'unchanged') return { tone: 'muted', key: 'ptr.lookupUnchanged', values: { ptr, target: tgt } }
        if (res.would === 'conflict') {
            const existing = joinExisting(res.current)
            return existing
                ? { tone: 'warn', key: 'ptr.lookupConflict', values: { ptr, existing } }
                : { tone: 'warn', key: 'ptr.lookupConflictPlain', values: { ptr } }
        }
        return { tone: 'muted', key: 'ptr.lookupError', values: { ip: ipText } }
    case 'no_reverse_zone':
        return { tone: 'muted', key: 'ptr.lookupNoZone', values: { ip: ipText } }
    case 'classless': {
        const cz = stripDot(res.classless_zone || res.zone)
        return cz
            ? { tone: 'warn', key: 'ptr.lookupClassless', values: { zone: cz } }
            : { tone: 'warn', key: 'ptr.lookupClasslessPlain', values: {} }
    }
    case 'forbidden':
        return zone
            ? { tone: 'warn', key: 'ptr.lookupForbidden', values: { zone } }
            : { tone: 'warn', key: 'ptr.lookupForbiddenPlain', values: {} }
    default:
        return { tone: 'muted', key: 'ptr.lookupError', values: { ip: ipText } }
    }
}

// Werte fuer die Live-Pruefung: gueltige IPs (isValid), ohne Duplikate, hoechstens `max`.
export function lookupCandidates(values, isValid, max = 3) {
    const out = []
    for (const v of values || []) {
        const s = String(v || '').trim()
        if (!s || out.includes(s)) continue
        if (typeof isValid === 'function' && !isValid(s)) continue
        out.push(s)
        if (out.length >= max) break
    }
    return out
}
