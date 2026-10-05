// React.lazy mit einmaligem Seiten-Reload bei Chunk-Ladefehlern (F8 §6.3.4, f157).
// Nach einem Deploy existieren alte Chunk-Dateien nicht mehr: erster Fehler -> Flag setzen und neu laden;
// scheitert es nach dem Reload erneut, wird der Fehler an die ErrorBoundary weitergereicht.
import { lazy } from 'react'

const FLAG = 'pdns_chunk_reload'

function readFlag() {
    try { return sessionStorage.getItem(FLAG) === '1' } catch { return false } // sessionStorage blockiert -> wie "nicht neu geladen"
}

// Liefert false, wenn sessionStorage nicht nutzbar ist.
function writeFlag(on) {
    try {
        if (on) sessionStorage.setItem(FLAG, '1')
        else sessionStorage.removeItem(FLAG)
        return true
    } catch {
        return false
    }
}

export function lazyWithReload(factory) {
    return lazy(async () => {
        try {
            const mod = await factory()
            writeFlag(false)
            return mod
        } catch (err) {
            // Ohne nutzbares sessionStorage kein Reload (sonst Endlosschleife) -> Fehler direkt an die ErrorBoundary
            if (!readFlag() && writeFlag(true)) {
                window.location.reload()
                // Bis zum Reload nichts rendern
                return new Promise(() => {})
            }
            throw err
        }
    })
}

export default lazyWithReload
