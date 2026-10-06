// Fokus-Management modaler Dialoge (WS-W1-NACHARBEIT, Review-Funde L11/L12):
// 1. Statische Pruefung: Jede Komponente mit role="dialog" nutzt den gemeinsamen Hook `lib/useDialogFocus.js`
//    (initialer Fokus, Tab-Falle, ESC, Fokus-Rueckgabe) und haengt dessen Ref an. Ausnahmen stehen mit Grund in
//    PENDING; sie gehoeren anderen Workstreams und werden dort umgestellt (Antrag in der Integrationsnotiz).
// 2. Die reinen Helfer des Hooks (ohne DOM, mit kleinen Fake-Elementen).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
    belongsToOtherModal, getFocusableElements, isFocusable, isTopDialog, nextTrapTarget, openDialogCount,
    pickInitialFocus, pushDialog, shouldRestoreFocus,
} from '../src/lib/useDialogFocus.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')
const HOOK_FILE = 'lib/useDialogFocus.js'

// Dialoge, die (noch) nicht auf den Hook umgestellt sind – Pfad relativ zu src/, mit Grund.
// Seit WS-W2-NACHARBEIT (Welle 3) leer: alle Dialoge nutzen den Hook. Neue Dialoge kommen nicht auf diese Liste.
const PENDING = Object.freeze({})

// Mindestens diese Dialoge nutzen den Hook: WS-W1-NACHARBEIT (Welle 2) und WS-W2-NACHARBEIT (Welle 3,
// die frueheren PENDING-Eintraege).
const REQUIRED = [
    'components/zoneHistory/RollbackModal.jsx',
    'components/audit/AuditRetentionModal.jsx',
    'components/webhooks/WebhookFormModal.jsx',
    'components/webhooks/WebhookDeliveriesDrawer.jsx',
    'components/OneTimeSecretModal.jsx',
    'components/userSecurity/UserSecurityModal.jsx',
    'pages/AuditLogPage.jsx',
    'components/dialogs/10-stepup.dialog.jsx',
    'components/panelTokens/PanelTokenFormModal.jsx',
    'components/dyndns/DyndnsTokenModal.jsx',
    'components/dnssec/DnssecDialog.jsx',
    'components/bulk/BulkEditorModal.jsx',
    'components/bulk/BulkTtlDialog.jsx',
]

const ROLE_DIALOG = /role\s*=\s*(?:"dialog"|'dialog'|\{\s*["']dialog["']\s*\})/

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

function dialogFiles() {
    return walk(SRC)
        .filter((f) => rel(f) !== HOOK_FILE)
        .filter((f) => ROLE_DIALOG.test(fs.readFileSync(f, 'utf8')))
        .map(rel)
        .sort()
}

// Problembeschreibung oder null, wenn die Datei den Hook korrekt nutzt.
export function hookUsageProblem(src) {
    if (!/import\s*\{[^}]*\buseDialogFocus\b[^}]*\}\s*from\s*['"][./]*(?:\.\.\/)*lib\/useDialogFocus(?:\.js)?['"]/.test(src)
        && !/import\s+useDialogFocus\s+from\s*['"][./]*lib\/useDialogFocus(?:\.js)?['"]/.test(src)) {
        return 'importiert useDialogFocus nicht aus lib/useDialogFocus'
    }
    const refs = [...src.matchAll(/const\s+(\w+)\s*=\s*useDialogFocus\(/g)].map((m) => m[1])
    if (refs.length === 0) return 'ruft useDialogFocus(...) nicht auf'
    if (!refs.some((name) => new RegExp(`ref=\\{\\s*${name}\\s*\\}`).test(src))) {
        return `haengt den Ref (${refs.join(', ')}) nicht an ein Element`
    }
    // eigene ESC-Behandlung neben dem Hook schliesst doppelt (bzw. ignoriert canClose)
    if (/['"]Escape['"]/.test(src)) return 'behandelt ESC zusaetzlich selbst (macht der Hook)'
    return null
}

test('a11y: jede Komponente mit role="dialog" nutzt useDialogFocus (oder steht begruendet in PENDING)', () => {
    const problems = []
    for (const file of dialogFiles()) {
        if (Object.hasOwn(PENDING, file)) continue
        const problem = hookUsageProblem(fs.readFileSync(path.join(SRC, file), 'utf8'))
        if (problem) problems.push(`${file}: ${problem}`)
    }
    assert.deepEqual(problems, [], 'Dialoge ohne gemeinsamen Fokus-Hook:\n' + problems.join('\n'))
})

test('a11y: die Dialoge von WS-W1-NACHARBEIT und WS-W2-NACHARBEIT sind umgestellt, PENDING ist leer', () => {
    const files = dialogFiles()
    for (const file of REQUIRED) {
        assert.ok(files.includes(file), `${file}: role="dialog" fehlt`)
        assert.equal(hookUsageProblem(fs.readFileSync(path.join(SRC, file), 'utf8')), null, file)
        assert.equal(Object.hasOwn(PENDING, file), false, `${file} darf nicht in PENDING stehen`)
    }
    assert.deepEqual(Object.keys(PENDING), [], 'PENDING muss leer bleiben – neue Dialoge nutzen useDialogFocus')
})

test('a11y: die statische Pruefung erkennt fehlende Nutzung', () => {
    const ok = "import { useDialogFocus } from '../../lib/useDialogFocus'\nconst dialogRef = useDialogFocus({ onClose })\n<div ref={dialogRef} role=\"dialog\">"
    assert.equal(hookUsageProblem(ok), null)
    assert.match(hookUsageProblem('<div role="dialog">'), /importiert/)
    assert.match(hookUsageProblem("import { useDialogFocus } from '../lib/useDialogFocus.js'\n<div role=\"dialog\">"), /ruft/)
    assert.match(hookUsageProblem("import { useDialogFocus } from '../lib/useDialogFocus'\nconst r = useDialogFocus()\n<div role=\"dialog\">"), /Ref/)
    assert.match(hookUsageProblem(ok + "\nif (e.key === 'Escape') onClose()"), /ESC/)
})

// ------------------------------------------------------------------ Helfer mit Fake-Elementen
function el(name, { disabled = false, tabindex = null, hidden = false, inert = false, children = [] } = {}) {
    const node = {
        name,
        disabled,
        isConnected: true,
        focused: 0,
        children,
        focus() { this.focused++ },
        getAttribute(attr) { return attr === 'tabindex' ? tabindex : null },
        getClientRects() { return hidden ? [] : [{}] },
        closest(sel) {
            if (inert && sel.includes('[inert]')) return this
            if (sel === '[aria-modal="true"]') return this.modalParent || null
            return null
        },
        querySelectorAll() { return this.children },
        querySelector(sel) { return sel === '[data-autofocus]' ? (this.children.find((c) => c.autofocus) || null) : null },
        contains(other) { return other === this || this.children.includes(other) },
    }
    return node
}

test('isFocusable / getFocusableElements: deaktiviert, tabindex=-1, unsichtbar und inert fallen weg', () => {
    const a = el('a')
    const b = el('b', { disabled: true })
    const c = el('c', { tabindex: '-1' })
    const d = el('d', { hidden: true })
    const e = el('e', { inert: true })
    const f = el('f', { tabindex: '0' })
    assert.equal(isFocusable(null), false)
    assert.equal(isFocusable({}), false)
    const box = el('box', { children: [a, b, c, d, e, f] })
    assert.deepEqual(getFocusableElements(box).map((x) => x.name), ['a', 'f'])
    assert.deepEqual(getFocusableElements(null), [])
})

test('nextTrapTarget: Tab am Ende -> Anfang, Umschalt+Tab am Anfang -> Ende, von aussen hinein', () => {
    const [a, b, c] = [el('a'), el('b'), el('c')]
    const items = [a, b, c]
    assert.equal(nextTrapTarget(items, c), a)
    assert.equal(nextTrapTarget(items, a, { shiftKey: true }), c)
    assert.equal(nextTrapTarget(items, b), null)
    assert.equal(nextTrapTarget(items, b, { shiftKey: true }), null)
    assert.equal(nextTrapTarget(items, el('aussen')), a)
    assert.equal(nextTrapTarget(items, el('aussen'), { shiftKey: true }), c)
    assert.equal(nextTrapTarget([a], a), a)
    assert.equal(nextTrapTarget([], a), null)
})

test('pickInitialFocus: Ref vor data-autofocus vor erstem Element vor Dialog', () => {
    const first = el('first')
    const marked = el('marked')
    marked.autofocus = true
    const box = el('box', { children: [first, marked] })
    const preferred = el('preferred')
    assert.equal(pickInitialFocus(box, preferred), preferred)
    assert.equal(pickInitialFocus(box, el('weg', { disabled: true })), marked)
    assert.equal(pickInitialFocus(box, null), marked)
    marked.autofocus = false
    assert.equal(pickInitialFocus(box, null), first)
    const empty = el('empty')
    assert.equal(pickInitialFocus(empty, null), empty)
})

test('belongsToOtherModal: Tasten aus einem fremden Modal (z. B. Einmal-Secret darueber) werden ignoriert', () => {
    const mine = el('mine')
    const other = el('other')
    const target = el('btn')
    assert.equal(belongsToOtherModal(target, mine), false)
    target.modalParent = mine
    assert.equal(belongsToOtherModal(target, mine), false)
    target.modalParent = other
    assert.equal(belongsToOtherModal(target, mine), true)
    mine.children.push(other)  // fremdes Modal im eigenen Dialog verschachtelt
    assert.equal(belongsToOtherModal(target, mine), false)
    assert.equal(belongsToOtherModal(null, mine), false)
})

test('shouldRestoreFocus: zurueck nur, wenn der Ausloeser noch existiert und der Fokus nicht anderswo liegt', () => {
    const body = el('body')
    const dialog = el('dialog')
    const trigger = el('trigger')
    assert.equal(shouldRestoreFocus(trigger, body, dialog, body), true)
    assert.equal(shouldRestoreFocus(trigger, null, dialog, body), true)
    const inside = el('inside')
    dialog.children.push(inside)
    assert.equal(shouldRestoreFocus(trigger, inside, dialog, body), true)
    assert.equal(shouldRestoreFocus(trigger, el('neuerDialog'), dialog, body), false)
    trigger.isConnected = false
    assert.equal(shouldRestoreFocus(trigger, body, dialog, body), false)
    assert.equal(shouldRestoreFocus(body, body, dialog, body), false)
    assert.equal(shouldRestoreFocus(null, body, dialog, body), false)
})

test('Dialog-Stapel: nur der zuletzt geoeffnete Dialog reagiert', () => {
    const before = openDialogCount()
    const a = {}
    const b = {}
    const popA = pushDialog(a)
    assert.equal(isTopDialog(a), true)
    const popB = pushDialog(b)
    assert.equal(isTopDialog(a), false)
    assert.equal(isTopDialog(b), true)
    popB()
    assert.equal(isTopDialog(a), true)
    popA()
    popA()  // doppeltes Schliessen ist harmlos
    assert.equal(isTopDialog(a), false)
    assert.equal(openDialogCount(), before)
})
