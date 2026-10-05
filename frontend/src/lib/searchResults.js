// Auswertung der Suche ueber mehrere Server (F8-E03, E05), ohne React/i18n – per `node --test` pruefbar.

export const SEARCH_MAX_RESULTS = 100 // Default von GET /search/{server} (max_results)

/** Zone eines Treffers ohne Punkt am Ende ('' wenn unbekannt). */
export function searchResultZone(r) {
    const z = r?.zone_id || r?.zone || (r?.object_type === 'zone' ? r?.name : '') || ''
    return String(z).replace(/\.$/, '')
}

/** Link auf die Zonenansicht des ersten Servers eines Treffers ('' ohne Zone/Server). */
export function searchResultLink(r) {
    const zone = searchResultZone(r)
    const server = Array.isArray(r?._servers) && r._servers.length ? r._servers[0] : r?._server
    if (!zone || !server) return ''
    return `/zones/${encodeURIComponent(server)}/${encodeURIComponent(zone)}`
}

/**
 * Ergebnisse von Promise.allSettled je Server zusammenfuehren.
 * outcomes: [{ server, status: 'fulfilled'|'rejected', value?, reason? }] in Server-Reihenfolge.
 * Rueckgabe: { results, serverErrors: [{ server, message }], truncatedServers: [server], aborted }
 * - gleiche Treffer (name|type|content) werden zusammengefasst, `_servers` listet alle Server mit dem Treffer
 * - abgebrochene Abfragen (AbortError) zaehlen nicht als Fehler, setzen aber `aborted`
 */
export function mergeSearchOutcomes(outcomes) {
    const merged = new Map()
    const serverErrors = []
    const truncatedServers = []
    let aborted = false
    for (const o of outcomes || []) {
        if (o.status === 'rejected') {
            if (o.reason?.name === 'AbortError') { aborted = true; continue }
            serverErrors.push({ server: o.server, message: o.reason?.message || String(o.reason) })
            continue
        }
        const data = o.value || {}
        if (data.truncated) truncatedServers.push(o.server)
        for (const r of data.results || []) {
            const key = `${r.name}|${r.type}|${r.content}`
            const cur = merged.get(key)
            if (!cur) merged.set(key, { ...r, _servers: [o.server] })
            else if (!cur._servers.includes(o.server)) cur._servers.push(o.server)
        }
    }
    return { results: Array.from(merged.values()), serverErrors, truncatedServers, aborted }
}
