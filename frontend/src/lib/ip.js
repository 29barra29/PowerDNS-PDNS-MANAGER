// IP-Pruefungen ohne Abhaengigkeiten (F8 §6.3.7), per node --test testbar.

export const IPV4_RE = /^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/

export function isValidIPv4(s) {
    if (typeof s !== 'string') return false
    return IPV4_RE.test(s.trim())
}

// IPv6 inkl. "::"-Kompression und eingebettetem IPv4-Suffix; Zonen-IDs ("%eth0") sind nicht erlaubt.
export function isValidIPv6(input) {
    if (typeof input !== 'string') return false
    let s = input.trim()
    if (!s || s.includes('%')) return false
    const lastColon = s.lastIndexOf(':')
    if (lastColon < 0) return false
    const tail = s.slice(lastColon + 1)
    if (tail.includes('.')) {
        if (!IPV4_RE.test(tail)) return false
        // IPv4-Suffix belegt zwei 16-Bit-Gruppen
        s = s.slice(0, lastColon + 1) + '0:0'
    }
    const parts = s.split('::')
    if (parts.length > 2) return false
    const groups = (x) => (x === '' ? [] : x.split(':'))
    const head = groups(parts[0])
    const rest = parts.length === 2 ? groups(parts[1]) : []
    if ([...head, ...rest].some((x) => !/^[0-9a-f]{1,4}$/i.test(x))) return false
    return parts.length === 2 ? head.length + rest.length <= 7 : head.length === 8
}

export function isValidIP(s) {
    return isValidIPv4(s) || isValidIPv6(s)
}
