// Auswertung von Fan-out-Ergebnissen (F8 §6.3.7, Plan B.5 [D4]), ohne React/i18n.
// Backend-Vertrag je Server: 'saved', 'deleted', 'skipped (zone not present)', 'skipped (read-only)',
// 'skipped (no matching content)', 'skipped (no changes needed)', 'skipped (not loaded: <grund>)', 'error: <text>'.
// Die Map steht entweder direkt in details oder unter details.fanout.

const NOT_LOADED_RE = /^skipped \(not loaded(?::\s*(.*))?\)\s*$/i

export function fanoutMap(details) {
    if (!details || typeof details !== 'object') return {}
    const map = details.fanout && typeof details.fanout === 'object' ? details.fanout : details
    return map && typeof map === 'object' && !Array.isArray(map) ? map : {}
}

// Fehler: Werte, die mit 'error:' beginnen -> [{ server, message }]
export function fanoutErrors(details) {
    const out = []
    for (const [server, status] of Object.entries(fanoutMap(details))) {
        if (typeof status === 'string' && status.startsWith('error:')) {
            out.push({ server, message: status.slice('error:'.length).trim() })
        }
    }
    return out
}

// Warnungen: konfigurierte, aber nicht geladene Server ('skipped (not loaded: ...)') -> [{ server, reason }]
export function fanoutWarnings(details) {
    const out = []
    for (const [server, status] of Object.entries(fanoutMap(details))) {
        if (typeof status !== 'string') continue
        const m = NOT_LOADED_RE.exec(status.trim())
        if (m) out.push({ server, reason: (m[1] || '').trim() })
    }
    return out
}

export const formatFanoutErrors = (list) => list.map((e) => `${e.server}: ${e.message}`).join(' · ')

export const formatFanoutWarnings = (list) => list.map((w) => (w.reason ? `${w.server}: ${w.reason}` : w.server)).join(' · ')

// Zusammenfassung fuer Banner: Fehler (rot) und Warnungen (gelb) getrennt.
export function fanoutSummary(details) {
    const errors = fanoutErrors(details)
    const warnings = fanoutWarnings(details)
    return { errors, warnings, hasErrors: errors.length > 0, hasWarnings: warnings.length > 0 }
}
