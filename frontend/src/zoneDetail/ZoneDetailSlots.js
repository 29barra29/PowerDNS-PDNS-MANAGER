// Slot-Verzeichnisse der Zonenansicht (Plan B.14, Regel 5). Diese Datei wird nach Welle 0 nicht mehr editiert:
// neue Tabs, Aktionen, Formular-Erweiterungen und Wert-Renderer kommen als eigene Dateien in die Verzeichnisse.
// Die Shell (pages/ZoneDetailPage.jsx) reicht die Listen ueber den Context weiter (ctx.slots), damit Slot-Dateien
// diese Datei nicht importieren muessen (sonst Import-Schleife ueber die eager-Globs).
//
// tabs/NN-<id>.tab.jsx
//   export const tab = { id, order, labelKey, icon, when?(data) }     Default-Komponente ({ ctx })
//   - URL: ?tab=<id> (Records = ohne Parameter). Nur der aktive Tab ist gemountet (Wechsel = neu oeffnen).
//   - when(data) bekommt den Context ohne die Tab-Felder (tabs/activeTab/setTab); die Tab-Leiste erscheint ab 2 Tabs.
//   - Belegt: 10 records (W0), 20 history (F7-FE), 30 propagation (F12F13-FE).
// header-actions/NN-<name>.action.jsx
//   export const action = { id, order, when?(ctx) }                   Default-Komponente ({ ctx })
//   - in allen Tabs sichtbar. Belegt: 10 dnssec-ds (W0 -> F4-B), 20 export / 30 notify (F2F3),
//     40 text-editor (F1), 90 add-record (W0).
// row-actions/NN-<name>.action.jsx                                    (siehe row-actions/README.md)
//   export const action = { id, order, when(record, ctx) }            Default-Komponente ({ record, ctx })
// form-extensions/<name>.ext.jsx                                      (siehe form-extensions/README.md)
//   export const ext = { id, when(type), position, initialState, collect, validate?, onResult? }
// value-renderers/<name>.renderer.jsx
//   export const renderer = { id, order?, when(record, ctx) }         Default-Komponente ({ record, ctx })
//   - der erste passende Renderer ersetzt die Wert-Zelle der Record-Tabelle (z. B. LUA, F15).
import { collectSlots } from '../lib/slots.js'
import { normalizeExtensions } from './formExtensions.js'

export const DEFAULT_TAB_ID = 'records'

export const ZONE_TABS = collectSlots(
    import.meta.glob('./tabs/*.tab.jsx', { eager: true }),
    { exportName: 'tab' },
)

export const HEADER_ACTIONS = collectSlots(
    import.meta.glob('./header-actions/*.action.jsx', { eager: true }),
    { exportName: 'action' },
)

export const ROW_ACTIONS = collectSlots(
    import.meta.glob('./row-actions/*.action.jsx', { eager: true }),
    { exportName: 'action' },
)

// Erweiterungen duerfen ohne eigene Oberflaeche auskommen (nur collect/onResult).
export const FORM_EXTENSIONS = normalizeExtensions(collectSlots(
    import.meta.glob('./form-extensions/*.ext.jsx', { eager: true }),
    { exportName: 'ext', requireComponent: false },
))

export const VALUE_RENDERERS = collectSlots(
    import.meta.glob('./value-renderers/*.renderer.jsx', { eager: true }),
    { exportName: 'renderer' },
)

export const ZONE_DETAIL_SLOTS = Object.freeze({
    tabs: ZONE_TABS,
    headerActions: HEADER_ACTIONS,
    rowActions: ROW_ACTIONS,
    formExtensions: FORM_EXTENSIONS,
    valueRenderers: VALUE_RENDERERS,
})
