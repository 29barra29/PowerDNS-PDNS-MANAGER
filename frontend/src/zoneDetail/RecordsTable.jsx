import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Pencil, Copy, Globe } from 'lucide-react'
import { ALL_RECORD_TYPE_KEYS } from '../constants/dnsRecordTypes'
import { slotApplies } from '../lib/slots.js'
import { useZoneDetail } from './zoneDetailContext'

// Record-Liste der Zonenansicht (2.4.1: eine Karte je Typ, Sortierung nach ALL_RECORD_TYPE_KEYS) mit Leer-Zustand.
// Slots (Plan B.14):
//   - Wert-Renderer  value-renderers/*.renderer.jsx: export const renderer = { id, order?, when(record, ctx) },
//                    Default-Komponente ({ record, ctx }); der erste passende ersetzt die Wert-Zelle.
//   - Zeilen-Aktionen row-actions/NN-*.action.jsx (README dort); werden hinter Stift/Klon/Papierkorb gerendert.
// Die Slot-Listen kommen ueber ctx.slots (keine Import-Schleife ueber ZoneDetailSlots.js).

// Breite der Aktionsspalte: 2.4.1 w-28 fuer drei Icons, je weiterer Aktion etwas mehr.
function actionColumnWidth(extraCount) {
    if (extraCount <= 0) return 'w-28'
    if (extraCount === 1) return 'w-36'
    return 'w-44'
}

export default function RecordsTable() {
    const { t } = useTranslation()
    const ctx = useZoneDetail()
    const { records, canEdit, openAdd, openEdit, openClone, handleDelete, slots } = ctx
    const rowActions = slots?.rowActions || []
    const valueRenderers = slots?.valueRenderers || []

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
                    <table className="w-full text-sm min-w-[640px]">
                        <thead>
                            <tr className="border-b border-border/50">
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.name')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.value')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs w-20">{t('zoneDetail.ttl')}</th>
                                <th className={`text-right p-3 text-text-muted font-medium text-xs ${actionsWidth}`}>{t('zones.actions')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {grouped[type].map((r) => (
                                <tr key={`${r.name}:${r.type}:${r.content}`} className="border-b border-border/30 hover:bg-bg-hover/30 transition-colors">
                                    <td className="p-3 font-mono text-xs text-text-primary">{r.name.replace(/\.$/, '')}</td>
                                    <td className="p-3 font-mono text-xs text-text-secondary break-all">{renderValue(r)}</td>
                                    <td className="p-3 text-text-muted text-xs">{r.ttl}</td>
                                    <td className="p-3 text-right whitespace-nowrap">
                                        <button
                                            onClick={() => openEdit(r)}
                                            disabled={!canEdit}
                                            className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors mr-1 disabled:opacity-30 disabled:pointer-events-none"
                                            title={t('zoneDetail.edit')}
                                        >
                                            <Pencil className="w-3.5 h-3.5" />
                                        </button>
                                        {type !== 'SOA' && (
                                            <button
                                                onClick={() => openClone(r)}
                                                disabled={!canEdit}
                                                className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors mr-1 disabled:opacity-30 disabled:pointer-events-none"
                                                title={t('zoneDetail.clone')}
                                            >
                                                <Copy className="w-3.5 h-3.5" />
                                            </button>
                                        )}
                                        {type !== 'SOA' && type !== 'NS' && (
                                            <button
                                                onClick={() => handleDelete(r)}
                                                disabled={!canEdit}
                                                className="p-1 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors disabled:opacity-30 disabled:pointer-events-none"
                                                title={t('zoneDetail.deleteRecord')}
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
        </>
    )
}
