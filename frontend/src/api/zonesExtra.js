// Zonen-Zusatzaktionen der Zonenansicht (WS-F2F3, F2 §6.1): NOTIFY senden und Export als Datei.
// Der JSON-Vertrag von GET …/export bleibt unveraendert (Skripte nutzen ihn); die Datei baut das Frontend.

function zonePath(server, zone) {
    return `/zones/${encodeURIComponent(server)}/${encodeURIComponent(zone)}`
}

// Dateiname wie im Backend (`_export_filename`), falls die Antwort keinen liefert
export function exportFilename(zone) {
    const base = String(zone || '').replace(/\.$/, '').toLowerCase() || 'zone'
    return `${base.replace(/[^a-z0-9._-]/g, '_')}.txt`
}

export default {
    // DNS NOTIFY vom angegebenen Server aus (Schreibrecht auf die Zone noetig)
    notifyZone(server, zone) {
        return this.request('POST', `${zonePath(server, zone)}/notify`, {})
    },
    // Laedt die Zone als BIND-Zonendatei herunter -> { filename, bytes }.
    // Leere Antwort: Error mit code 'EMPTY_EXPORT' (kein Download).
    async downloadZoneExport(server, zone) {
        const res = await this.request('GET', `${zonePath(server, zone)}/export`)
        const content = typeof res?.content === 'string' ? res.content : ''
        if (!content.trim()) {
            const err = new Error('empty export')
            err.code = 'EMPTY_EXPORT'
            throw err
        }
        const filename = res.filename || exportFilename(zone)
        const blob = new Blob([content.endsWith('\n') ? content : `${content}\n`], { type: 'text/plain;charset=utf-8' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = filename
        document.body.appendChild(a)
        a.click()
        a.remove()
        setTimeout(() => URL.revokeObjectURL(url), 1000)
        return { filename, bytes: blob.size }
    },
}
