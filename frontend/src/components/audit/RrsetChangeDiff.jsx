import { useTranslation } from 'react-i18next'
import { Minus, Plus } from 'lucide-react'
import {
    changeKind, commentsChanged, diffRecords, displayName, ttlChange,
} from './historyModel.js'

// Diff eines RRsets (F7 §6.1). Shared: Zonenverlauf, Audit-Drawer, Rollback-Vorschau (F1-Vorschau darf es nutzen).
// Props: change { name, type, before, after }, zoneKey (Zone mit Punkt, fuer relative Namen), t (optional),
// labels { before, after } (optional, Default history.before/after), badge (optional, zusaetzliches Element).
const KIND_STYLE = {
    created: 'bg-success/10 text-success border-success/30',
    deleted: 'bg-danger/10 text-danger border-danger/30',
    modified: 'bg-accent/10 text-accent-light border-accent/30',
}
const KIND_KEY = {
    created: 'history.changeCreated',
    deleted: 'history.changeDeleted',
    modified: 'history.changeModified',
}

export default function RrsetChangeDiff({ change, zoneKey, t: tProp, labels, badge }) {
    const { t: tHook } = useTranslation()
    const t = tProp || tHook
    if (!change) return null
    const kind = changeKind(change)
    const rows = diffRecords(change.before, change.after)
    const ttl = ttlChange(change)
    const comments = commentsChanged(change)
    const beforeLabel = labels?.before || t('history.before')
    const afterLabel = labels?.after || t('history.after')

    return (
        <div className="rounded-lg border border-border/60 bg-bg-secondary/40 p-3 text-xs space-y-2 min-w-0">
            <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-text-primary break-all">{displayName(change.name, zoneKey)}</span>
                <span className="px-1.5 py-0.5 rounded bg-bg-hover text-text-secondary font-mono">{change.type}</span>
                <span className={`px-1.5 py-0.5 rounded border ${KIND_STYLE[kind]}`}>{t(KIND_KEY[kind])}</span>
                {comments && (
                    <span className="px-1.5 py-0.5 rounded border border-border text-text-muted">{t('history.commentsChanged')}</span>
                )}
                {badge}
            </div>

            <div className="text-text-muted">
                {ttl.changed
                    ? <span className="text-warning">{t('history.ttlChanged', { from: ttl.from, to: ttl.to })}</span>
                    : (ttl.to ?? ttl.from) !== null && t('history.ttl', { ttl: ttl.to ?? ttl.from })}
            </div>

            {(kind === 'created' || kind === 'deleted') && (
                <div className="text-text-muted">
                    {kind === 'created'
                        ? `${beforeLabel}: ${t('history.notPresent')}`
                        : `${afterLabel}: ${t('history.notPresent')}`}
                </div>
            )}

            <ul className="font-mono space-y-0.5" aria-label={`${beforeLabel} → ${afterLabel}`}>
                {rows.map((r) => {
                    const flipped = r.status === 'unchanged' && r.disabledBefore !== r.disabledAfter
                    let cls = 'text-text-muted'
                    let Icon = null
                    if (r.status === 'removed') { cls = 'text-danger bg-danger/5'; Icon = Minus }
                    else if (r.status === 'added') { cls = 'text-success bg-success/5'; Icon = Plus }
                    else if (flipped) cls = 'text-warning'
                    const disabled = r.status === 'removed' ? r.disabledBefore : r.disabledAfter
                    return (
                        <li key={`${r.status}:${r.content}`} className={`flex items-start gap-1.5 rounded px-1 ${cls}`}>
                            <span className="w-3.5 shrink-0 pt-0.5" aria-hidden="true">
                                {Icon ? <Icon className="w-3 h-3" /> : null}
                            </span>
                            <span className="sr-only">
                                {r.status === 'removed' ? '−' : r.status === 'added' ? '+' : ''}
                            </span>
                            <span className="break-all whitespace-pre-wrap flex-1">{r.content}</span>
                            {flipped ? (
                                <span className="shrink-0 px-1 rounded border border-warning/40 text-warning font-sans">
                                    {r.disabledAfter ? `→ ${t('history.disabled')}` : `${t('history.disabled')} →`}
                                </span>
                            ) : disabled ? (
                                <span className="shrink-0 px-1 rounded border border-border text-text-muted font-sans">{t('history.disabled')}</span>
                            ) : null}
                        </li>
                    )
                })}
            </ul>
        </div>
    )
}
