// Slot-Registry (Plan B.14, Regel 5): wertet das Ergebnis von `import.meta.glob(..., { eager: true })` aus.
// Rein (kein React, kein i18n, kein import.meta.glob) und damit per `node --test` ladbar. Die Globs selbst
// stehen in den Aggregatoren (z. B. zoneDetail/ZoneDetailSlots.js, components/users/UserBadges.jsx), weil
// Vite sie nur dort statisch aufloesen kann.
//
// Dateikonvention eines Slots: `<verzeichnis>/NN-<name>.<art>.jsx` (NN = Sortierschluessel, optional).
// Jede Slot-Datei exportiert ein Metadaten-Objekt unter einem festen Namen (z. B. `tab`, `action`, `ext`) und in
// der Regel eine Default-Komponente.

const FILE_RE = /(?:^|\/)(?:(\d+)-)?([^/]+?)\.([A-Za-z0-9]+)\.(?:jsx|js|mjs)$/

// './tabs/10-records.tab.jsx' -> { file: '10-records.tab.jsx', prefix: 10, name: 'records', kind: 'tab' }
export function parseSlotFile(path) {
    const p = String(path || '')
    const file = p.split('/').pop()
    const m = FILE_RE.exec(p)
    if (!m) return { file, prefix: null, name: file.replace(/\.(jsx|js|mjs)$/, ''), kind: null }
    return { file, prefix: m[1] !== undefined ? Number(m[1]) : null, name: m[2], kind: m[3] }
}

function isComponent(value) {
    if (typeof value === 'function') return true
    // React.memo / forwardRef / lazy liefern Objekte mit $$typeof
    return !!(value && typeof value === 'object' && value.$$typeof)
}

function defaultReporter(message) {
    if (typeof console !== 'undefined') console.error(`[slots] ${message}`)
}

// Sammelt Slot-Eintraege aus einem Glob-Ergebnis.
//   modules   : { [pfad]: modul } (eager)
//   options   : { exportName: 'tab'|'action'|..., requireComponent = true, onProblem(message, path) }
// Liefert sortierte Eintraege: { ...meta, id, order, file, path, Component }
//   - id    = meta.id, sonst Dateiname ohne NN- und Endung
//   - order = meta.order (Zahl), sonst NN aus dem Dateinamen, sonst 1000
// Fehlerhafte Slots (fehlende Metadaten, fehlende Komponente, doppelte id) werden uebersprungen und gemeldet,
// damit ein kaputter Slot nicht die ganze Seite mitnimmt.
export function collectSlots(modules, options = {}) {
    const { exportName, requireComponent = true, onProblem = defaultReporter } = options
    if (!exportName) throw new TypeError('collectSlots: exportName fehlt')
    const entries = []
    const seen = new Set()
    const paths = Object.keys(modules || {}).sort()
    for (const path of paths) {
        const mod = modules[path] || {}
        const info = parseSlotFile(path)
        const meta = mod[exportName]
        if (!meta || typeof meta !== 'object' || Array.isArray(meta)) {
            onProblem(`${info.file}: Export "${exportName}" fehlt oder ist kein Objekt - Slot ignoriert`, path)
            continue
        }
        const Component = isComponent(mod.default) ? mod.default : null
        if (requireComponent && !Component) {
            onProblem(`${info.file}: Default-Export ist keine Komponente - Slot ignoriert`, path)
            continue
        }
        const id = typeof meta.id === 'string' && meta.id ? meta.id : info.name
        if (seen.has(id)) {
            onProblem(`${info.file}: id "${id}" ist bereits belegt - Slot ignoriert`, path)
            continue
        }
        seen.add(id)
        const order = Number.isFinite(meta.order) ? meta.order : (info.prefix ?? 1000)
        entries.push({ ...meta, id, order, file: info.file, path, Component })
    }
    entries.sort((a, b) => (a.order - b.order) || (a.file < b.file ? -1 : a.file > b.file ? 1 : 0))
    return entries
}

// Fuehrt ein optionales Praedikat `when(...)` defensiv aus: fehlt es -> true; wirft es -> false (+ Meldung).
export function slotApplies(entry, args = [], onProblem = defaultReporter) {
    if (!entry || typeof entry.when !== 'function') return true
    try {
        return !!entry.when(...args)
    } catch (err) {
        onProblem(`${entry.file || entry.id}: when() hat geworfen (${err?.message || err}) - Slot ausgeblendet`, entry.path)
        return false
    }
}

// Vereinigt Listen-Slots (z. B. constants/auditActions/*.actions.js mit `export default [...]`).
//   options: { listExport = 'default', key = (item) => item, first = [] (Dateinamen ohne Endung, die vorne stehen),
//              onProblem }
// Reihenfolge: zuerst die Dateien aus `first` (in dieser Reihenfolge), dann alle uebrigen alphabetisch.
// Doppelte Schluessel: der erste gewinnt; `onProblem` meldet nur echte Konflikte (abweichender Inhalt).
// Liefert [{ item, file }].
export function mergeListSlots(modules, options = {}) {
    const { listExport = 'default', key = (item) => item, first = [], onProblem = defaultReporter } = options
    const paths = Object.keys(modules || {})
    const rank = (path) => {
        const name = parseSlotFile(path).file.replace(/\.[^.]+\.(jsx|js|mjs)$/, '')
        const idx = first.indexOf(name)
        return idx === -1 ? first.length : idx
    }
    paths.sort((a, b) => (rank(a) - rank(b)) || (a < b ? -1 : a > b ? 1 : 0))
    const out = []
    const byKey = new Map()
    for (const path of paths) {
        const file = parseSlotFile(path).file
        const list = (modules[path] || {})[listExport]
        if (list === undefined) continue
        if (!Array.isArray(list)) {
            onProblem(`${file}: Export "${listExport}" ist keine Liste - ignoriert`, path)
            continue
        }
        for (const item of list) {
            let k
            try {
                k = key(item)
            } catch {
                k = undefined
            }
            if (k === undefined || k === null || k === '') {
                onProblem(`${file}: Eintrag ohne Schluessel - ignoriert`, path)
                continue
            }
            if (byKey.has(k)) {
                const prev = byKey.get(k)
                if (JSON.stringify(prev.item) !== JSON.stringify(item)) {
                    onProblem(`${file}: "${k}" ist bereits in ${prev.file} anders definiert - erster Eintrag gilt`, path)
                }
                continue
            }
            const entry = { item, file }
            byKey.set(k, entry)
            out.push(entry)
        }
    }
    return out
}
