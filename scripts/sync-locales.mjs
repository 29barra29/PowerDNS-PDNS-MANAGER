#!/usr/bin/env node
// Synchronisiert alle Locale-Dateien gegen en.json (= source of truth).
// - Fehlende Keys werden mit dem englischen Wert eingefuegt (Reihenfolge wie in en.json).
// - Keys, die nicht mehr in en.json existieren, werden entfernt.
// - Bestehende Uebersetzungen bleiben unangetastet.
// - Am Ende wird pro Sprache geloggt, was sich geaendert hat.
//
// Aufruf:  node scripts/sync-locales.mjs
// Im Anschluss `git diff frontend/src/locales/` ansehen, dann committen.

import fs from 'node:fs'
import path from 'node:path'
import url from 'node:url'

const __dirname = path.dirname(url.fileURLToPath(import.meta.url))
const localesDir = path.resolve(__dirname, '..', 'frontend', 'src', 'locales')
const sourceFile = path.join(localesDir, 'en.json')

const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf-8'))
const writeJson = (file, obj) => fs.writeFileSync(file, JSON.stringify(obj, null, 2) + '\n', 'utf-8')

// Geht en.json Schluessel fuer Schluessel durch und baut ein neues Objekt:
// - Existiert der Key in der Zielsprache: dortigen Wert uebernehmen
// - Sonst: englischen Wert als Fallback einsetzen (UI zeigt damit wenigstens englisch)
// Liefert zusaetzlich added/removed-Keys zurueck, damit man sieht, was passiert ist.
function syncObject(source, target, prefix = '') {
    const merged = {}
    const added = []
    const removed = []

    for (const key of Object.keys(source)) {
        const fullKey = prefix ? `${prefix}.${key}` : key
        const sVal = source[key]
        const tVal = target?.[key]

        if (sVal !== null && typeof sVal === 'object' && !Array.isArray(sVal)) {
            const child = syncObject(sVal, (tVal && typeof tVal === 'object') ? tVal : {}, fullKey)
            merged[key] = child.merged
            added.push(...child.added)
            removed.push(...child.removed)
        } else if (target && Object.prototype.hasOwnProperty.call(target, key)) {
            merged[key] = tVal
        } else {
            merged[key] = sVal
            added.push(fullKey)
        }
    }

    if (target) {
        for (const key of Object.keys(target)) {
            if (!(key in source)) {
                const fullKey = prefix ? `${prefix}.${key}` : key
                removed.push(fullKey)
            }
        }
    }

    return { merged, added, removed }
}

const en = readJson(sourceFile)
const targets = fs.readdirSync(localesDir)
    .filter((f) => f.endsWith('.json') && f !== 'en.json')
    .sort()

let changedFiles = 0
for (const file of targets) {
    const filePath = path.join(localesDir, file)
    const target = readJson(filePath)
    const { merged, added, removed } = syncObject(en, target)

    const before = JSON.stringify(target)
    const after = JSON.stringify(merged)

    if (before === after) {
        console.log(`✓ ${file.padEnd(10)}  schon synchron`)
        continue
    }

    writeJson(filePath, merged)
    changedFiles++
    console.log(`↻ ${file.padEnd(10)}  +${added.length} hinzugefuegt, -${removed.length} entfernt`)
    if (added.length) {
        console.log(`    NEU (englischer Fallback eingesetzt, bitte uebersetzen):`)
        for (const k of added.slice(0, 12)) console.log(`      • ${k}`)
        if (added.length > 12) console.log(`      … und ${added.length - 12} weitere`)
    }
    if (removed.length) {
        console.log(`    ENTFERNT (waren nicht mehr in en.json):`)
        for (const k of removed.slice(0, 12)) console.log(`      • ${k}`)
        if (removed.length > 12) console.log(`      … und ${removed.length - 12} weitere`)
    }
}

console.log(`\nFertig. ${changedFiles} Datei(en) geaendert. Jetzt: git diff frontend/src/locales/`)
