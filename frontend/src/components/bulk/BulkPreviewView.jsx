import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Info, XCircle } from 'lucide-react'
import {
    PREVIEW_ROW_LIMIT, blockingIssues, collapseKept, hasRemovals, issueI18n, opKey, previewChanges, relativeOwner,
    semanticsBanner, semanticsKey, ttlLabel, valueRows, warningIssues,
} from '../../zoneDetail/bulkModel.js'

// Vorschau einer Bulk-Aenderung (F1 2.5, 6.3.4): Semantik-Banner, Zusammenfassung, Probleme, Aenderungstabelle.
// Rein darstellend; nur "weitere anzeigen" ist lokaler Zustand.
const OP_BADGE = {
    create: 'bg-success/10 text-success border-success/30',
    update: 'bg-accent/10 text-accent-light border-accent/30',
    delete: 'bg-danger/10 text-danger border-danger/30',
}

function IssueList({ issues, tone, title, t }) {
    if (!issues.length) return null
    const cls = tone === 'error'
        ? 'bg-danger/10 border-danger/30 text-danger'
        : 'bg-warning/10 border-warning/30 text-warning'
    const Icon = tone === 'error' ? XCircle : AlertTriangle
    return (
        <div className={`p-3 rounded-xl border ${cls}`} role={tone === 'error' ? 'alert' : 'status'}>
            <p className="text-sm font-medium flex items-center gap-2">
                <Icon className="w-4 h-4 shrink-0" aria-hidden="true" /> {title}
            </p>
            <ul className="mt-2 space-y-1 text-xs list-disc pl-5 break-words">
                {issues.map((issue, i) => {
                    const { key, values } = issueI18n(issue)
                    return <li key={`${issue.code}-${issue.line ?? ''}-${issue.name ?? ''}-${i}`}>{t(key, { ...values, defaultValue: issue.message })}</li>
                })}
            </ul>
        </div>
    )
}

function ValueCell({ change, t }) {
    const { rows, hidden } = collapseKept(valueRows(change))
    return (
        <ul className="space-y-0.5 font-mono text-xs break-all">
            {rows.map((row, i) => {
                let cls = 'text-text-muted'
                let sign = ' '
                if (row.status === 'removed') { cls = 'text-danger line-through'; sign = '−' }
                if (row.status === 'added') { cls = 'text-success'; sign = '+' }
                return (
                    <li key={`${row.status}-${row.content}-${i}`} className={cls}>
                        <span aria-hidden="true" className="inline-block w-3">{sign}</span>
                        {row.content}
                        {row.disabled && row.status !== 'removed' && !row.flip && (
                            <span className="ml-1 font-sans text-[10px] text-warning">{t('bulk.valueDisabled')}</span>
                        )}
                        {row.flip && (
                            <span className="ml-1 font-sans text-[10px] text-warning">
                                {t(row.flip === 'disabled' ? 'bulk.valueNowDisabled' : 'bulk.valueNowEnabled')}
                            </span>
                        )}
                    </li>
                )
            })}
            {hidden > 0 && <li className="font-sans text-text-muted">{t('bulk.moreKept', { count: hidden })}</li>}
        </ul>
    )
}

export default function BulkPreviewView({ preview, zoneKey, confirmRemoval, onConfirmRemovalChange, applyIssues = [] }) {
    const { t } = useTranslation()
    const [showAll, setShowAll] = useState(false)
    if (!preview) return null

    const changes = previewChanges(preview)
    const visible = showAll ? changes : changes.slice(0, PREVIEW_ROW_LIMIT)
    const summary = preview.summary || {}
    const banner = semanticsBanner(preview)
    const removals = hasRemovals(preview) && changes.length > 0 && !preview.blocking
    const errors = [...blockingIssues(preview), ...(applyIssues || []).filter((i) => i?.severity === 'error')]
    const warnings = [...warningIssues(preview), ...(applyIssues || []).filter((i) => i?.severity !== 'error')]
    const peers = Array.isArray(preview.peers) ? preview.peers : []
    const zoneLabel = String(preview.zone || zoneKey || '').replace(/\.$/, '')

    const chips = [
        ['bulk.summaryCreated', summary.rrsets_created],
        ['bulk.summaryUpdated', summary.rrsets_updated],
        ['bulk.summaryDeleted', summary.rrsets_deleted],
        ['bulk.summaryValuesAdded', summary.values_added],
        ['bulk.summaryValuesRemoved', summary.values_removed],
        ['bulk.summaryUnchanged', summary.rrsets_unchanged],
    ]

    return (
        <div className="space-y-4">
            <div>
                <h3 className="text-base font-semibold text-text-primary">{t('bulk.previewTitle')}</h3>
                <p className="text-xs text-text-muted">{t('bulk.previewSubtitle', { zone: zoneLabel, server: preview.server })}</p>
            </div>

            {banner === 'replace' && (
                <div className="p-3 rounded-xl border bg-warning/10 border-warning/40 text-warning text-sm flex gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    {t('bulk.semanticsReplaceBanner')}
                </div>
            )}
            {banner === 'sync' && (
                <div className="p-3 rounded-xl border bg-danger/10 border-danger/30 text-danger text-sm flex gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    {t('bulk.semanticsSyncBanner')}
                </div>
            )}
            {banner === 'merge' && (
                <div className="p-3 rounded-xl border bg-accent/10 border-accent/30 text-accent-light text-sm flex gap-2">
                    <Info className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    {t('bulk.semanticsMergeBanner')}
                </div>
            )}

            <ul className="flex flex-wrap gap-1.5 text-xs" aria-label={t('bulk.previewTitle')}>
                {chips.map(([key, value]) => (
                    <li key={key} className="px-2 py-0.5 rounded-full border border-border bg-bg-secondary/60 text-text-secondary">
                        {t(key, { count: Number(value) || 0 })}
                    </li>
                ))}
            </ul>

            <IssueList issues={errors} tone="error" title={t('bulk.blockingTitle')} t={t} />
            <IssueList issues={warnings} tone="warning" title={t('bulk.issuesTitle')} t={t} />

            {removals && (
                <div className="p-3 rounded-xl border bg-danger/10 border-danger/30 text-danger text-sm space-y-2">
                    <p className="font-semibold">{t('bulk.removalWarning', {
                        values: Number(summary.values_removed) || 0, rrsets: Number(summary.rrsets_deleted) || 0,
                    })}</p>
                    <label className="flex items-center gap-2 cursor-pointer text-text-primary">
                        <input
                            type="checkbox"
                            checked={!!confirmRemoval}
                            onChange={(e) => onConfirmRemovalChange(e.target.checked)}
                            className="w-4 h-4"
                        />
                        {t('bulk.confirmRemoval')}
                    </label>
                </div>
            )}

            {changes.length === 0 && !preview.blocking && (
                <div className="p-3 rounded-xl border border-border bg-bg-secondary/60 text-text-secondary text-sm" role="status">
                    {t('bulk.noChanges')}
                </div>
            )}

            {changes.length > 0 && (
                <div className="overflow-x-auto rounded-xl border border-border">
                    <table className="w-full text-sm min-w-[760px]">
                        <thead>
                            <tr className="border-b border-border/60 bg-bg-hover/30 text-xs text-text-muted">
                                <th className="text-left p-2 font-medium">{t('bulk.colName')}</th>
                                <th className="text-left p-2 font-medium w-20">{t('bulk.colType')}</th>
                                <th className="text-left p-2 font-medium w-48">{t('bulk.colChange')}</th>
                                <th className="text-left p-2 font-medium w-28">{t('bulk.colTtl')}</th>
                                <th className="text-left p-2 font-medium">{t('bulk.colValues')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map((c) => {
                                const removes = (c.removed || []).length > 0 || c.op === 'delete'
                                const replaceEdge = (c.semantics || []).includes('replace') && removes
                                return (
                                    <tr
                                        key={`${c.name}|${c.type}`}
                                        className={`border-b border-border/30 align-top ${replaceEdge ? 'border-l-4 border-l-danger' : ''}`}
                                    >
                                        <td className="p-2 font-mono text-xs text-text-primary break-all">{relativeOwner(c.name, zoneKey)}</td>
                                        <td className="p-2 text-xs"><span className="px-1.5 py-0.5 rounded bg-accent/20 text-accent-light font-bold">{c.type}</span></td>
                                        <td className="p-2">
                                            <div className="flex flex-wrap gap-1">
                                                <span className={`text-[11px] px-1.5 py-0.5 rounded-full border ${OP_BADGE[c.op] || OP_BADGE.update}`}>{t(opKey(c.op))}</span>
                                                {(c.semantics || []).map((s) => semanticsKey(s) && (
                                                    <span key={s} className="text-[11px] px-1.5 py-0.5 rounded-full border border-border text-text-muted">{t(semanticsKey(s))}</span>
                                                ))}
                                            </div>
                                        </td>
                                        <td className="p-2 text-xs text-text-secondary whitespace-nowrap">{ttlLabel(c)}</td>
                                        <td className="p-2"><ValueCell change={c} t={t} /></td>
                                    </tr>
                                )
                            })}
                        </tbody>
                    </table>
                    {!showAll && changes.length > PREVIEW_ROW_LIMIT && (
                        <div className="p-2 text-center">
                            <button type="button" onClick={() => setShowAll(true)} className="text-xs text-accent-light hover:underline">
                                {t('bulk.showMore', { count: changes.length - PREVIEW_ROW_LIMIT })}
                            </button>
                        </div>
                    )}
                </div>
            )}

            {peers.length > 0 && changes.length > 0 && (
                <p className="text-xs text-text-muted flex items-start gap-2">
                    <Info className="w-4 h-4 shrink-0" aria-hidden="true" />
                    {t('bulk.fanoutNote', { primary: preview.server, peers: peers.join(', ') })}
                </p>
            )}
        </div>
    )
}
