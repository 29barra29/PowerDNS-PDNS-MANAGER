/**
 * Vergleicht zwei Versionsstrings (z. B. 2.3.1 vs 2.4.0). "v" am Anfang wird ignoriert.
 * @returns 1 wenn a > b, -1 wenn a < b, 0 wenn gleich
 */
export function compareSemver(a, b) {
    const pa = String(a ?? '')
        .replace(/^v/i, '')
        .split('.')
        .map((n) => parseInt(n, 10) || 0)
    const pb = String(b ?? '')
        .replace(/^v/i, '')
        .split('.')
        .map((n) => parseInt(n, 10) || 0)
    const len = Math.max(pa.length, pb.length)
    for (let i = 0; i < len; i++) {
        const da = pa[i] || 0
        const db = pb[i] || 0
        if (da > db) return 1
        if (da < db) return -1
    }
    return 0
}
