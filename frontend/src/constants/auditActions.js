// Audit-Aktionen (F7 §6.1, Plan B.14): Basis + Aggregation aller Slot-Dateien `./auditActions/*.actions.js`.
// Diese Datei wird nach Welle 0 nicht mehr editiert (Plan Regel 5) - neue Aktionen kommen als eigene Datei
// `constants/auditActions/<ws>.actions.js` (Vertrag: src/lib/auditCatalog.js).
import { buildAuditCatalog } from '../lib/auditCatalog.js'

export {
    AUDIT_ACTION_GROUPS, HISTORY_GROUPS, actionsInGroup, auditActionLabel, resourceTypeLabel,
} from '../lib/auditCatalog.js'

const modules = import.meta.glob('./auditActions/*.actions.js', { eager: true })
const catalog = buildAuditCatalog(modules)

// [{ action, group, history }] - Basis zuerst, danach die Slot-Dateien alphabetisch
export const AUDIT_ACTIONS = catalog.actions

// { [action]: { action, group, history } }
export const AUDIT_ACTION_MAP = catalog.actionMap

// bekannte resource_type-Werte (Filter "Typ" im Audit-Log)
export const AUDIT_RESOURCE_TYPES = catalog.resourceTypes

// Teilmenge fuer den Zonenverlauf (F7 §6.1)
export const HISTORY_ACTIONS = catalog.historyActions

export function auditActionGroup(action) {
    return AUDIT_ACTION_MAP[action]?.group || null
}
