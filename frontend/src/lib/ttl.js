// TTL-Hilfen (F8 §6.3.5), ohne React/i18n-Import. formatTtl bekommt die t-Funktion vom Aufrufer.
// Benoetigte Keys (Plural): ttl.seconds|minutes|hours|days|weeks (_one/_other, sr/bs/hr zusaetzlich _few).

export const TTL_MIN = 60
export const TTL_MAX = 604800
export const TTL_PRESETS = [60, 300, 900, 1800, 3600, 7200, 14400, 43200, 86400, 172800, 604800]

const UNITS = [['weeks', 604800], ['days', 86400], ['hours', 3600], ['minutes', 60]]

// Nur nicht-negative Ganzzahlen (als Zahl oder String) sind gueltig, sonst null.
export function parseTtl(v) {
    const s = String(v ?? '').trim()
    return /^\d+$/.test(s) ? parseInt(s, 10) : null
}

export function isValidTtl(v, min = TTL_MIN, max = TTL_MAX) {
    const n = parseTtl(v)
    return n !== null && n >= min && n <= max
}

// Groesste glatte Einheit, z. B. 7200 -> { unit: 'hours', count: 2 }, 90 -> { unit: 'seconds', count: 90 }
export function splitTtl(sec) {
    const n = Number(sec)
    for (const [unit, f] of UNITS) if (n >= f && n % f === 0) return { unit, count: n / f }
    return { unit: 'seconds', count: n }
}

export function formatTtl(sec, t) {
    const { unit, count } = splitTtl(sec)
    return t(`ttl.${unit}`, { count })
}
