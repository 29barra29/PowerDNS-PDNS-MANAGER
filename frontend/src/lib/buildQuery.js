// Baut einen Query-String aus einem Objekt (F7 §6.2).
// undefined, null und '' werden uebersprungen; Arrays werden als wiederholte Parameter angehaengt.
// Rueckgabe: '' oder '?a=b&c=d'.
export function buildQuery(params = {}) {
    if (!params || typeof params !== 'object') return ''
    const q = new URLSearchParams()
    for (const [key, raw] of Object.entries(params)) {
        const values = Array.isArray(raw) ? raw : [raw]
        for (const v of values) {
            if (v === undefined || v === null || v === '') continue
            q.append(key, String(v))
        }
    }
    const s = q.toString()
    return s ? `?${s}` : ''
}

export default buildQuery
