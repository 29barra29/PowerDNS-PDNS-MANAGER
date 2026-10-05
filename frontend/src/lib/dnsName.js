// Normalisierung von Record-Namen relativ zu einer Zone (F8 §6.3.7), ohne React/i18n.
// Rueckgabe: { fqdn } (immer mit abschliessendem Punkt, klein geschrieben) oder
// { error: 'whitespace' | 'emptyLabel' | 'labelTooLong' | 'tooLong' | 'outsideZone' }.
export function normalizeRecordName(input, zoneName) {
    const zone = String(zoneName ?? '').trim().toLowerCase().replace(/\.$/, '')
    let n = String(input ?? '').trim()
    if (n === '' || n === '@') return { fqdn: `${zone}.` }
    if (/\s/.test(n)) return { error: 'whitespace' }
    const absolute = n.endsWith('.')
    n = n.replace(/\.$/, '').toLowerCase()
    let fqdn
    if (n === zone || n.endsWith(`.${zone}`)) fqdn = n
    else if (absolute) return { error: 'outsideZone' }
    else fqdn = `${n}.${zone}`
    const labels = fqdn.split('.')
    if (labels.some((l) => l === '')) return { error: 'emptyLabel' }
    if (labels.some((l) => l.length > 63)) return { error: 'labelTooLong' }
    if (fqdn.length > 253) return { error: 'tooLong' }
    return { fqdn: `${fqdn}.` }
}

// Relativer Anzeigename: Apex -> '@', sonst der Teil vor der Zone (ohne Punkt am Ende).
export function relativeRecordName(fqdn, zoneName) {
    const zone = String(zoneName ?? '').trim().toLowerCase().replace(/\.$/, '')
    const name = String(fqdn ?? '').trim().toLowerCase().replace(/\.$/, '')
    if (name === zone) return '@'
    if (zone && name.endsWith(`.${zone}`)) return name.slice(0, -(zone.length + 1))
    return name
}
