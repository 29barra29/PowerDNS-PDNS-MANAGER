import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Pencil, Copy, Globe } from 'lucide-react'
import api from '../api'
import BulkActionBar from '../components/bulk/BulkActionBar'
import BulkTtlDialog from '../components/bulk/BulkTtlDialog'
import { ALL_RECORD_TYPE_KEYS } from '../constants/dnsRecordTypes'
import { getDefault, getManagePtr } from '../lib/ptrPreference.js'
import { slotApplies } from '../lib/slots.js'
import { formatTtl } from '../lib/ttl.js'
import {
    EMPTY_SELECTION, PTR_FORWARD_TYPES, buildDeleteOps, buildDisabledOps, buildTtlOps, filterExistingSelection,
    firstSelectedTtl, headerState, isSelectable, nextModalId, recordKey, selectionStats, textEditorPrefill,
    toggleSelection,
} from './bulkModel.js'
import { useZoneDetail, useZoneSlotState } from './zoneDetailContext'
import { RECORD_TYPES } from './zoneDetailModel'

// Record-Liste der Zonenansicht (2.4.1: eine Karte je Typ, Sortierung nach ALL_RECORD_TYPE_KEYS) mit Leer-Zustand.
// Slots (Plan B.14):
//   - Wert-Renderer  value-renderers/*.renderer.jsx: export const renderer = { id, order?, when(record, ctx) },
//                    Default-Komponente ({ record, ctx }); der erste passende ersetzt die Wert-Zelle.
//   - Zeilen-Aktionen row-actions/NN-*.action.jsx (README dort); werden hinter Stift/Klon/Papierkorb gerendert.
// Die Slot-Listen kommen ueber ctx.slots (keine Import-Schleife ueber ZoneDetailSlots.js).
// F8: Badge fuer deaktivierte Records (F07), Klonen nur fuer Typen mit eigenem Editor (F04), Loeschschutz nur fuer
// SOA und Apex-NS – Delegations-NS sind loeschbar (F06).
// F1 (Bulk-Editor): Auswahlspalte (nur canEdit; SOA und DNSSEC-Auto-Typen nicht waehlbar), Aktionsleiste und
// TTL-Dialog. Die Auswahl liegt im geteilten Slot-Zustand 'bulk.selected', die Vorschau oeffnet das Modal der
// Kopf-Aktion "Text-Editor" ueber 'bulk.modal' (header-actions/40-text-editor.action.jsx).
// F11 (PTR): Papierkorb bei A/AAAA sendet die gemerkte PTR-Auswahl der Zone (lib/ptrPreference.js) als manage_ptr;
// ohne gemerkte Auswahl entscheidet der Admin-Default im Backend.

// Breite der Aktionsspalte: 2.4.1 w-28 fuer drei Icons, je weiterer Aktion etwas mehr.
function actionColumnWidth(extraCount) {
    if (extraCount <= 0) return 'w-28'
    if (extraCount === 1) return 'w-36'
    return 'w-44'
}

// Kopf-Checkbox mit Zwischenzustand (indeterminate ist nur per DOM setzbar)
function HeaderCheckbox({ state, onChange, label }) {
    return (
        <input
            type="checkbox"
            aria-label={label}
            checked={state === 'all'}
            ref={(el) => { if (el) el.indeterminate = state === 'some' }}
            onChange={(e) => onChange(e.target.checked)}
            className="w-4 h-4 align-middle"
        />
    )
}

export default function RecordsTable() {
    const { t } = useTranslation()
    const ctx = useZoneDetail()
    const { records, canEdit, openAdd, openEdit, openClone, handleDelete, slots, zoneKey, server, zoneId, subscribeZoneLoaded } = ctx
    const rowActions = slots?.rowActions || []
    const valueRenderers = slots?.valueRenderers || []

    // ---- Mehrfachauswahl (F1) -----------------------------------------------------------------------------------
    const [selectedRaw, setSelected] = useZoneSlotState('bulk.selected', EMPTY_SELECTION)
    const [, setBulkModal] = useZoneSlotState('bulk.modal', null)
    const selected = useMemo(() => filterExistingSelection(selectedRaw, records), [selectedRaw, records])
    const [bulkBusy, setBulkBusy] = useState(false)
    const [barError, setBarError] = useState('')
    const [ttlDialog, setTtlDialog] = useState(null) // { id, initialTtl } | null
    const [ttlError, setTtlError] = useState('')

    // Nach jedem Laden die Auswahl auf noch vorhandene Records reduzieren (F1 2.6)
    useEffect(() => subscribeZoneLoaded((ev) => {
        if (ev?.ok) setSelected((prev) => filterExistingSelection(prev, ev.records || []))
    }), [subscribeZoneLoaded, setSelected])

    const stats = useMemo(() => selectionStats(records, selected), [records, selected])
    const showBulk = canEdit && selected.size > 0

    async function runSelectionPreview(ops, { fromTtl = false } = {}) {
        if (bulkBusy) return
        setBulkBusy(true)
        setBarError('')
        setTtlError('')
        try {
            const request = { ops }
            const preview = await api.previewBulkRecords(server, zoneId, request)
            setTtlDialog(null)
            setBulkModal({ id: nextModalId(), flow: 'selection', step: 'preview', request, preview })
        } catch (err) {
            if (err?.name === 'AbortError') return
            const msg = err?.message || String(err)
            if (fromTtl) setTtlError(msg)
            else setBarError(msg)
        } finally {
            setBulkBusy(false)
        }
    }

    function openTextEditorForSelection() {
        const p = textEditorPrefill(records, zoneKey, selected)
        setBarError('')
        setBulkModal({
            id: nextModalId(), flow: 'text', step: 'text', text: p.text, mode: 'sync_scope', scope: p.scope,
            defaultTtl: 3600, loadedHint: { rrsets: p.rrsets, values: p.values },
        })
    }

    function toggleRow(r) {
        setSelected((prev) => toggleSelection(filterExistingSelection(prev, records), [recordKey(r)]))
    }

    function toggleType(rows, on) {
        const keys = rows.filter(isSelectable).map(recordKey)
        setSelected((prev) => toggleSelection(filterExistingSelection(prev, records), keys, on))
    }

    // ---- PTR beim Loeschen (F11 2.5 Schritt 5) ------------------------------------------------------------------
    function ptrDeleteExtra(r) {
        if (!PTR_FORWARD_TYPES.has(String(r.type || '').toUpperCase())) return null
        const stored = getManagePtr(zoneKey)
        return stored === null ? null : { manage_ptr: stored }
    }

    function deleteTitle(r) {
        if (!PTR_FORWARD_TYPES.has(String(r.type || '').toUpperCase())) return t('zoneDetail.deleteRecord')
        const stored = getManagePtr(zoneKey)
        const effective = stored === null ? getDefault() : stored
        return effective ? `${t('zoneDetail.deleteRecord')} – ${t('bulk.ptrDeleteHint')}` : t('zoneDetail.deleteRecord')
    }

    if (records.length === 0) {
        return (
            <div className="glass-card p-8 text-center text-text-muted">
                <Globe className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <h2 className="text-lg font-semibold text-text-primary">{t('zoneDetail.noRecordsTitle')}</h2>
                <p className="text-sm mt-2 max-w-xl mx-auto">{t('zoneDetail.noRecordsBody')}</p>
                {canEdit && (
                    <button
                        type="button"
                        onClick={openAdd}
                        className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium"
                    >
                        <Plus className="w-4 h-4" /> {t('zoneDetail.addRecord')}
                    </button>
                )}
            </div>
        )
    }

    const grouped = {}
    records.forEach(r => {
        if (!grouped[r.type]) grouped[r.type] = []
        grouped[r.type].push(r)
    })
    const typeOrder = ALL_RECORD_TYPE_KEYS
    const sortedTypes = Object.keys(grouped).sort((a, b) => {
        const ai = typeOrder.indexOf(a), bi = typeOrder.indexOf(b)
        return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi)
    })
    const actionsWidth = actionColumnWidth(rowActions.length)

    // SOA und Apex-NS sind nicht loeschbar; Delegationen (sub NS) schon
    function canDeleteRecord(r) {
        if (r.type === 'SOA') return false
        if (r.type === 'NS' && String(r.name || '').toLowerCase() === zoneKey) return false
        return true
    }

    function renderValue(r) {
        const renderer = valueRenderers.find((entry) => slotApplies(entry, [r, ctx]))
        if (!renderer) return r.content
        const Renderer = renderer.Component
        return <Renderer record={r} ctx={ctx} />
    }

    return (
        <>
            {sortedTypes.map(type => (
                <div key={type} className="glass-card overflow-hidden">
                    <div className="px-4 py-3 bg-bg-hover/30 border-b border-border flex items-center gap-2">
                        <span className="text-xs font-bold px-2 py-0.5 bg-accent/20 text-accent-light rounded">{type}</span>
                        <span className="text-xs text-text-muted">{t('zoneDetail.recordCount', { count: grouped[type].length })}</span>
                    </div>
                    <div className="overflow-x-auto">
                    <table className={`w-full text-sm ${canEdit ? 'min-w-[680px]' : 'min-w-[640px]'}`}>
                        <thead>
                            <tr className="border-b border-border/50">
                                {canEdit && (
                                    <th className="w-8 p-3 text-left">
                                        {grouped[type].some(isSelectable) && (
                                            <HeaderCheckbox
                                                state={headerState(grouped[type], selected)}
                                                onChange={(on) => toggleType(grouped[type], on)}
                                                label={t('bulk.selectAll', { type })}
                                            />
                                        )}
                                    </th>
                                )}
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.name')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.value')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs w-20">{t('zoneDetail.ttl')}</th>
                                <th className={`text-right p-3 text-text-muted font-medium text-xs ${actionsWidth}`}>{t('zones.actions')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {grouped[type].map((r) => (
                                <tr key={`${r.name}:${r.type}:${r.content}`} className={`border-b border-border/30 hover:bg-bg-hover/30 transition-colors ${r.disabled ? 'opacity-60' : ''} ${selected.has(recordKey(r)) ? 'bg-accent/5' : ''}`}>
                                    {canEdit && (
                                        <td className="p-3 w-8 align-top">
                                            <input
                                                type="checkbox"
                                                className="w-4 h-4 align-middle disabled:opacity-30"
                                                aria-label={t('bulk.selectRow')}
                                                checked={selected.has(recordKey(r))}
                                                disabled={!isSelectable(r) || bulkBusy}
                                                title={r.type === 'SOA' ? t('bulk.soaNotSelectable') : undefined}
                                                onChange={() => toggleRow(r)}
                                            />
                                        </td>
                                    )}
                                    <td className="p-3 font-mono text-xs text-text-primary">
                                        <span className="break-all">{r.name.replace(/\.$/, '')}</span>
                                        {r.disabled && (
                                            <span className="ml-2 inline-block align-middle font-sans text-[10px] px-1.5 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/30">
                                                {t('zoneDetail.disabledBadge')}
                                            </span>
                                        )}
                                    </td>
                                    <td className="p-3 font-mono text-xs text-text-secondary break-all">{renderValue(r)}</td>
                                    <td className="p-3 text-text-muted text-xs" title={Number.isFinite(Number(r.ttl)) ? formatTtl(Number(r.ttl), t) : undefined}>{r.ttl}</td>
                                    <td className="p-3 text-right whitespace-nowrap">
                                        <button
                                            onClick={() => openEdit(r)}
                                            disabled={!canEdit}
                                            className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors mr-1 disabled:opacity-30 disabled:pointer-events-none"
                                            title={t('zoneDetail.edit')}
                                        >
                                            <Pencil className="w-3.5 h-3.5" />
                                        </button>
                                        {type !== 'SOA' && RECORD_TYPES[type] && (
                                            <button
                                                onClick={() => openClone(r)}
                                                disabled={!canEdit}
                                                className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors mr-1 disabled:opacity-30 disabled:pointer-events-none"
                                                title={t('zoneDetail.clone')}
                                            >
                                                <Copy className="w-3.5 h-3.5" />
                                            </button>
                                        )}
                                        {canDeleteRecord(r) && (
                                            <button
                                                onClick={() => handleDelete(r, ptrDeleteExtra(r))}
                                                disabled={!canEdit}
                                                className="p-1 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors disabled:opacity-30 disabled:pointer-events-none"
                                                title={deleteTitle(r)}
                                            >
                                                <Trash2 className="w-3.5 h-3.5" />
                                            </button>
                                        )}
                                        {rowActions.filter((a) => slotApplies(a, [r, ctx])).map((a) => {
                                            const RowAction = a.Component
                                            return (
                                                <span key={a.id} className="inline-block ml-1">
                                                    <RowAction record={r} ctx={ctx} />
                                                </span>
                                            )
                                        })}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    </div>
                </div>
            ))}

            {showBulk && (
                <>
                    {/* Platzhalter, damit die fixierte Leiste die letzte Tabellenzeile nicht verdeckt */}
                    <div className="h-20" aria-hidden="true" />
                    <BulkActionBar
                        count={stats.count}
                        busy={bulkBusy}
                        error={barError}
                        onClearError={() => setBarError('')}
                        onDelete={() => runSelectionPreview(buildDeleteOps(records, selected))}
                        onSetTtl={() => { setTtlError(''); setTtlDialog({ id: nextModalId(), initialTtl: firstSelectedTtl(records, selected) }) }}
                        onDisable={() => runSelectionPreview(buildDisabledOps(records, selected, true))}
                        onEnable={() => runSelectionPreview(buildDisabledOps(records, selected, false))}
                        onEditText={openTextEditorForSelection}
                        onClear={() => { setBarError(''); setSelected(EMPTY_SELECTION) }}
                    />
                </>
            )}
            {showBulk && ttlDialog && (
                <BulkTtlDialog
                    key={ttlDialog.id}
                    initialTtl={ttlDialog.initialTtl}
                    stats={stats}
                    busy={bulkBusy}
                    error={ttlError}
                    onCancel={() => { if (!bulkBusy) setTtlDialog(null) }}
                    onConfirm={(ttl) => runSelectionPreview(buildTtlOps(records, selected, ttl), { fromTtl: true })}
                />
            )}
        </>
    )
}
