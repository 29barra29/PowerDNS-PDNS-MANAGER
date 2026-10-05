// Katalog der Audit-Aktionen (F7 §6.1/§6.6, Plan B.14 "Audit-Aktionen").
// Rein (kein React, kein i18n, kein import.meta.glob) und damit per `node --test` ladbar.
// Die Aggregation der Slot-Dateien macht `constants/auditActions.js`; diese Datei enthaelt nur die Logik.
//
// Slot-Datei `constants/auditActions/<ws>.actions.js`:
//   export default [{ action: 'WEBHOOK_CREATE', group: 'settings', history?: boolean }, ...]
//   export const resourceTypes = ['webhook']        // optional: neue resource_type-Werte
// - group: eine aus AUDIT_ACTION_GROUPS (Filter "Bereich" im Audit-Log)
// - history: erscheint die Aktion im Zonenverlauf? Default: ja fuer die zonenbezogenen Gruppen
//   (HISTORY_GROUPS), sonst nein. Explizit setzen, wenn eine Aktion davon abweicht.
// - Labels: i18n-Key `audit.actions.<ACTION>` bzw. `audit.resourceTypes.<type>` im Fragment des Workstreams;
//   fehlt ein Label, wird der Rohstring angezeigt.

import { mergeListSlots } from './slots.js'

export const AUDIT_ACTION_GROUPS = Object.freeze(['records', 'zone', 'dnssec', 'acme', 'auth', 'users', 'settings', 'system'])

// Gruppen, deren Aktionen zonenbezogen sind und standardmaessig im Zonenverlauf erscheinen.
export const HISTORY_GROUPS = Object.freeze(['records', 'zone', 'dnssec', 'acme'])

const ACTION_RE = /^[A-Z][A-Z0-9_]*$/
const RESOURCE_TYPE_RE = /^[a-z][a-z0-9_]*$/

function reporter(onProblem) {
    return onProblem || ((message) => { if (typeof console !== 'undefined') console.error(`[auditActions] ${message}`) })
}

// modules: Glob-Ergebnis { './auditActions/base.actions.js': { default: [...], resourceTypes: [...] }, ... }
// Liefert { actions, actionMap, resourceTypes, historyActions }.
export function buildAuditCatalog(modules, { onProblem } = {}) {
    const report = reporter(onProblem)
    const merged = mergeListSlots(modules, {
        key: (item) => item?.action,
        first: ['base'],
        onProblem: report,
    })
    const actions = []
    for (const { item, file } of merged) {
        if (typeof item.action !== 'string' || !ACTION_RE.test(item.action)) {
            report(`${file}: ungueltiger Aktionsname "${item.action}" - ignoriert`)
            continue
        }
        if (!AUDIT_ACTION_GROUPS.includes(item.group)) {
            report(`${file}: ${item.action} hat die unbekannte Gruppe "${item.group}" - als "system" gefuehrt`)
        }
        const group = AUDIT_ACTION_GROUPS.includes(item.group) ? item.group : 'system'
        const history = typeof item.history === 'boolean' ? item.history : HISTORY_GROUPS.includes(group)
        actions.push(Object.freeze({ action: item.action, group, history }))
    }

    const types = mergeListSlots(modules, {
        listExport: 'resourceTypes',
        key: (item) => item,
        first: ['base'],
        onProblem: report,
    })
    const resourceTypes = []
    for (const { item, file } of types) {
        if (typeof item !== 'string' || !RESOURCE_TYPE_RE.test(item)) {
            report(`${file}: ungueltiger resource_type "${item}" - ignoriert`)
            continue
        }
        resourceTypes.push(item)
    }

    const actionMap = Object.freeze(Object.fromEntries(actions.map((a) => [a.action, a])))
    return {
        actions: Object.freeze(actions),
        actionMap,
        resourceTypes: Object.freeze(resourceTypes),
        historyActions: Object.freeze(actions.filter((a) => a.history).map((a) => a.action)),
    }
}

// Aktionen einer Gruppe (z. B. fuer einen Filter "Bereich").
export function actionsInGroup(actions, group) {
    return actions.filter((a) => a.group === group).map((a) => a.action)
}

// Label einer Aktion; unbekannte Aktionen fallen auf den Rohstring zurueck (F7 §6.6).
export function auditActionLabel(t, action) {
    if (!action) return ''
    return t(`audit.actions.${action}`, { defaultValue: action })
}

// Label eines resource_type; unbekannte Typen fallen auf den Rohstring zurueck.
export function resourceTypeLabel(t, resourceType) {
    if (!resourceType) return ''
    return t(`audit.resourceTypes.${resourceType}`, { defaultValue: resourceType })
}
