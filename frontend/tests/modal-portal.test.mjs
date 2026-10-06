// Overlays modaler Dialoge haengen per components/common/ModalPortal.jsx an document.body (WS-W3-NACHARBEIT,
// Fund UI-SMOKE-1): Innerhalb einer .glass-card (backdrop-filter) waere `position: fixed` an die Karte gebunden – das
// Overlay bedeckte nur die Karte, spaetere Karten verdeckten Knoepfe des Dialogs.
// Statische Pruefung: Jede Komponente mit aria-modal-Dialog und Vollbild-Overlay (`fixed inset-0`) rendert das
// Overlay in <ModalPortal>. Die Browser-Pruefung (Lage ueber der Seite, alle Knoepfe klickbar) machen die UI-Smoke-
// Specs (scripts/e2e/ui/tests/17-dialogs.spec.js, expectDialogOnTop).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')
const PORTAL_FILE = 'components/common/ModalPortal.jsx'

// Ausnahmen (Pfad relativ zu src/, mit Grund). Neue Dialoge kommen nicht auf diese Liste.
const EXCEPTIONS = Object.freeze({
    // Seitenleiste auf schmalen Bildschirmen: Teil des Layouts selbst (kein Vorfahre mit backdrop-filter/transform),
    // das Overlay ist unabhaengig vom Seiteninhalt
    'components/Layout.jsx': 'Seitenleiste des Layouts, nicht in einer Karte',
})

// UI-SMOKE-1: diese Dialoge wurden innerhalb von Einstellungs-Karten gerendert und muessen das Portal nutzen.
const REQUIRED = [
    'components/OneTimeSecretModal.jsx',
    'components/webhooks/WebhookFormModal.jsx',
    'components/webhooks/WebhookDeliveriesDrawer.jsx',
    'components/panelTokens/PanelTokenFormModal.jsx',
    'components/dyndns/DyndnsTokenModal.jsx',
]

function walk(dir, out = []) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) walk(full, out)
        else if (/\.(jsx?|tsx?)$/.test(entry.name)) out.push(full)
    }
    return out
}

function rel(file) {
    return path.relative(SRC, file).split(path.sep).join('/')
}

const ARIA_MODAL = /aria-modal\s*=\s*(?:"true"|'true'|\{\s*true\s*\})/
const OVERLAY = /\bfixed\s+inset-0\b/

function modalOverlayFiles() {
    return walk(SRC)
        .map(rel)
        .filter((f) => f !== PORTAL_FILE)
        .filter((f) => {
            const src = fs.readFileSync(path.join(SRC, f), 'utf8')
            return ARIA_MODAL.test(src) && OVERLAY.test(src)
        })
        .sort()
}

// Problembeschreibung oder null, wenn die Datei ihr Overlay im Portal rendert.
export function portalProblem(src) {
    if (!/import\s+ModalPortal\s+from\s*['"](?:\.{1,2}\/)+(?:components\/)?common\/ModalPortal(?:\.jsx)?['"]/.test(src)) {
        return 'importiert ModalPortal nicht aus components/common/ModalPortal'
    }
    const opens = (src.match(/<ModalPortal\s*>/g) || []).length
    const closes = (src.match(/<\/ModalPortal\s*>/g) || []).length
    if (opens === 0) return 'rendert kein <ModalPortal>'
    if (opens !== closes) return '<ModalPortal> nicht geschlossen'
    // Jedes Vollbild-Overlay muss innerhalb eines Portals stehen
    let depth = 0
    const tokens = src.matchAll(/<ModalPortal\s*>|<\/ModalPortal\s*>|\bfixed\s+inset-0\b/g)
    for (const m of tokens) {
        if (m[0].startsWith('</')) depth -= 1
        else if (m[0].startsWith('<')) depth += 1
        else if (depth <= 0) return 'Overlay (fixed inset-0) ausserhalb von <ModalPortal>'
    }
    return null
}

test('ModalPortal haengt an document.body (createPortal)', () => {
    const src = fs.readFileSync(path.join(SRC, PORTAL_FILE), 'utf8')
    assert.match(src, /import\s*\{\s*createPortal\s*\}\s*from\s*['"]react-dom['"]/)
    assert.match(src, /createPortal\(\s*children\s*,\s*document\.body\s*\)/)
})

test('UI-SMOKE-1: jedes aria-modal-Overlay rendert in <ModalPortal> (oder steht begruendet in EXCEPTIONS)', () => {
    const files = modalOverlayFiles()
    assert.ok(files.length >= REQUIRED.length, 'zu wenige Dialoge gefunden – Suchmuster pruefen')
    const problems = []
    for (const file of files) {
        if (Object.hasOwn(EXCEPTIONS, file)) continue
        const problem = portalProblem(fs.readFileSync(path.join(SRC, file), 'utf8'))
        if (problem) problems.push(`${file}: ${problem}`)
    }
    assert.deepEqual(problems, [], 'Dialoge ohne Portal:\n' + problems.join('\n'))
})

test('UI-SMOKE-1: die Dialoge aus den Einstellungs-Karten nutzen das Portal', () => {
    for (const file of REQUIRED) {
        assert.equal(Object.hasOwn(EXCEPTIONS, file), false, `${file} darf keine Ausnahme sein`)
        assert.equal(portalProblem(fs.readFileSync(path.join(SRC, file), 'utf8')), null, file)
    }
})

test('portalProblem erkennt fehlenden Import, fehlendes Portal und Overlays ausserhalb', () => {
    const imp = "import ModalPortal from '../common/ModalPortal'\n"
    const ok = imp + '<ModalPortal>\n<div className="fixed inset-0 z-50">x</div>\n</ModalPortal>'
    assert.equal(portalProblem(ok), null)
    assert.equal(portalProblem("import ModalPortal from './common/ModalPortal'\n<ModalPortal><div className=\"fixed inset-0\" /></ModalPortal>"), null)
    assert.equal(portalProblem("import ModalPortal from '../components/common/ModalPortal'\n<ModalPortal><div className=\"fixed inset-0\" /></ModalPortal>"), null)
    assert.match(portalProblem('<div className="fixed inset-0">'), /importiert/)
    assert.match(portalProblem(imp + '<div className="fixed inset-0">'), /kein/)
    assert.match(portalProblem(imp + '<ModalPortal><div className="fixed inset-0">'), /nicht geschlossen/)
    assert.match(portalProblem(ok + '\n<div className="fixed inset-0 z-50">y</div>'), /ausserhalb/)
})
