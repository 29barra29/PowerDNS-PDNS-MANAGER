import { useTranslation } from 'react-i18next'
import { AlertTriangle, ExternalLink, Info, Loader2 } from 'lucide-react'
import { useDateFormat } from '../../lib/useDateFormat'
import { auditActionLabel, resourceTypeLabel } from '../../constants/auditActions'
import RrsetChangeDiff from './RrsetChangeDiff'
import {
    actorInfo, displayName, entryChangeCount, entryChanges, entryVersion, fanoutRows, historyFlags, legacyFields,
    needsFullLoad, notablePrimaryOutcome, otherDetailFields, tokenAuth,
} from './historyModel.js'

// Detailansicht eines Protokolleintrags (F7 §6.1): Felder, Fehlermeldung, Fan-out-Tabelle, v2-Diffs,
// Legacy-Hinweis, Roh-JSON. Genutzt vom Drawer der AuditLogPage und aufgeklappt im Zonenverlauf.
// Props:
//   entry               HistoryEntry (Verlauf) oder AuditLogEntry (Audit-Log)
//   zoneKey             Zone mit Punkt fuer relative Namen (Default: entry.zone_name)
//   t                   optional
//   showFields          Kopf-Felder (Zeit, Benutzer, Aktion, ...) anzeigen - im Drawer ja, im Verlauf nein
//   onLoadAll           optional: vollstaendige Aenderungen nachladen (bei gekuerzten Listen)
//   loadingAll          Busy-Zustand fuer onLoadAll
//   onOpenZoneHistory   optional: Link "Im Zonenverlauf oeffnen" (nur bei zone_name + server_name)
const FANOUT_STYLE = {
    ok: 'text-success',
    skipped: 'text-text-muted',
    warning: 'text-warning',
    error: 'text-danger',
}

function Field({ label, children }) {
    return (
        <div className="min-w-0">
            <dt className="text-[11px] uppercase tracking-wide text-text-muted">{label}</dt>
            <dd className="text-sm text-text-primary break-all">{children}</dd>
        </div>
    )
}

export function ActorLabel({ entry, t }) {
    const actor = actorInfo(entry)
    if (actor.kind === 'system') return <span className="text-text-muted">{t('audit.systemUser')}</span>
    if (actor.kind === 'deleted') {
        return (
            <span className="text-text-muted" title={actor.name || undefined}>
                {t('history.unknownUser', { id: actor.id })}
            </span>
        )
    }
    return <span>{actor.name}</span>
}

export default function AuditEntryDetails({
    entry, zoneKey, t: tProp, showFields = false, onLoadAll, loadingAll = false, onOpenZoneHistory,
}) {
    const { t: tHook } = useTranslation()
    const t = tProp || tHook
    const { fmtDateTime } = useDateFormat()
    if (!entry) return null
    const details = entry.details
    const zone = zoneKey || entry.zone_name || ''
    const version = entryVersion(entry)
    const changes = entryChanges(entry)
    const total = entryChangeCount(entry)
    const flags = historyFlags(details)
    const outcome = notablePrimaryOutcome(details)
    const token = tokenAuth(details)
    const fan = fanoutRows(details)
    const legacy = version === 1 ? legacyFields(details) : []
    const others = otherDetailFields(details)
    const skipped = Array.isArray(details?.skipped) ? details.skipped : []
    const isRecordEntry = entry.resource_type === 'record'
    const canOpenZone = Boolean(onOpenZoneHistory && entry.zone_name && entry.server_name)

    return (
        <div className="space-y-4 text-sm">
            {showFields && (
                <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <Field label={t('audit.timestamp')}>{fmtDateTime(entry.timestamp)}</Field>
                    <Field label={t('audit.user')}><ActorLabel entry={entry} t={t} /></Field>
                    <Field label={t('audit.action')}>{auditActionLabel(t, entry.action)} <span className="text-text-muted font-mono text-xs">({entry.action})</span></Field>
                    <Field label={t('audit.resourceType')}>{resourceTypeLabel(t, entry.resource_type)}</Field>
                    <Field label={t('audit.resource')}><span className="font-mono">{entry.resource_name || '–'}</span></Field>
                    <Field label={t('audit.zone')}><span className="font-mono">{entry.zone_name || '–'}</span></Field>
                    <Field label={t('audit.server')}>{entry.server_name || '–'}</Field>
                    <Field label={t('audit.status')}>
                        {entry.status === 'success'
                            ? <span className="text-success">{t('audit.ok')}</span>
                            : <span className="text-danger">{t('audit.error')}</span>}
                    </Field>
                    {entry.actor_username && entry.actor_username !== entry.username && (
                        <Field label={t('audit.actorUsername')}>{entry.actor_username}</Field>
                    )}
                    {entry.client_ip && <Field label={t('audit.clientIp')}><span className="font-mono">{entry.client_ip}</span></Field>}
                    {token && <Field label={t('audit.authVia')}>{token.name ? t('history.viaTokenNamed', { name: token.name }) : t('history.viaToken')}</Field>}
                    {entry.revert_of_id != null && <Field label={t('audit.revertOfField')}>#{entry.revert_of_id}</Field>}
                    {entry.reverted_by_id != null && <Field label={t('audit.revertedByField')}>#{entry.reverted_by_id}</Field>}
                </dl>
            )}

            {canOpenZone && (
                <button
                    type="button"
                    onClick={() => onOpenZoneHistory(entry)}
                    className="inline-flex items-center gap-1.5 text-sm text-accent-light hover:underline"
                >
                    <ExternalLink className="w-4 h-4" aria-hidden="true" /> {t('audit.openZoneHistory')}
                </button>
            )}

            {entry.before_recreate && (
                <div className="p-3 rounded-lg bg-bg-secondary/60 border border-border text-text-secondary text-xs flex items-start gap-2">
                    <Info className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    <span>{t('history.beforeRecreateHint')}</span>
                </div>
            )}

            {outcome && (
                <div className={`p-3 rounded-lg border text-xs flex items-start gap-2 ${outcome === 'verified_after_timeout' ? 'bg-warning/10 border-warning/30 text-warning' : 'bg-danger/10 border-danger/30 text-danger'}`}>
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    <span>{t(`history.outcome.${outcome}`)}</span>
                </div>
            )}

            {entry.error_message && (
                <div>
                    <div className="text-xs font-medium text-text-muted mb-1">{t('audit.detailError')}</div>
                    <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs break-words whitespace-pre-line">
                        {entry.error_message}
                    </div>
                </div>
            )}

            {(flags.incomplete || flags.truncated || flags.computedAfter) && (
                <ul className="space-y-1 text-xs text-warning">
                    {flags.incomplete && <li>{t('history.incompleteNote')}</li>}
                    {flags.truncated && <li>{t('history.truncatedNote', { total: total || flags.changeKeys.length })}</li>}
                    {flags.computedAfter && <li className="text-text-muted">{t('history.computedAfterNote')}</li>}
                </ul>
            )}

            {details?.forced && (
                <div className="text-xs text-warning">
                    {t('history.forcedNote')}
                </div>
            )}

            {version === 2 && changes.length > 0 && (
                <div className="space-y-2">
                    {changes.map((c) => (
                        <RrsetChangeDiff key={`${c.name}|${c.type}`} change={c} zoneKey={zone} t={t} />
                    ))}
                </div>
            )}

            {version === 2 && needsFullLoad(entry) && onLoadAll && (
                <button
                    type="button"
                    onClick={onLoadAll}
                    disabled={loadingAll}
                    className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                >
                    {loadingAll && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />}
                    {entry.changes_truncated ? t('history.showAllChanges', { count: total }) : t('audit.detailLoadFull')}
                </button>
            )}

            {flags.truncated && flags.changeKeys.length > 0 && (
                <ul className="text-xs font-mono text-text-secondary flex flex-wrap gap-1.5">
                    {flags.changeKeys.map((k) => (
                        <li key={`${k.name}|${k.type}`} className="px-1.5 py-0.5 rounded bg-bg-hover">
                            {displayName(k.name, zone)} {k.type}
                        </li>
                    ))}
                </ul>
            )}

            {skipped.length > 0 && (
                <div>
                    <div className="text-xs font-medium text-text-muted mb-1">{t('history.rollbackSkippedTitle')}</div>
                    <ul className="text-xs space-y-0.5">
                        {skipped.map((s) => (
                            <li key={`${s.name}|${s.type}`}>
                                <span className="font-mono">{displayName(s.name, zone)} {s.type}</span>
                                <span className="text-text-muted"> – {t(`history.skipReason.${s.reason}`, { defaultValue: s.reason })}</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {version === 1 && isRecordEntry && (
                <div className="p-3 rounded-lg bg-bg-secondary/60 border border-border text-text-secondary text-xs flex items-start gap-2">
                    <Info className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    <span>{t('history.legacyEntry')}</span>
                </div>
            )}

            {legacy.length > 0 && (
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                    {legacy.map((f) => (
                        <div key={f.key} className="contents">
                            <dt className="text-text-muted font-mono">{f.key}</dt>
                            <dd className="font-mono text-text-primary break-all">{f.value}</dd>
                        </div>
                    ))}
                </dl>
            )}

            {fan.length > 0 && (
                <div>
                    <div className="text-xs font-medium text-text-muted mb-1">{t('history.fanout')}</div>
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                            <thead>
                                <tr className="border-b border-border">
                                    <th className="text-left py-1 pr-3 font-medium text-text-muted">{t('audit.server')}</th>
                                    <th className="text-left py-1 font-medium text-text-muted">{t('history.fanoutResult')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {fan.map((row) => (
                                    <tr key={row.server} className="border-b border-border/30">
                                        <td className="py-1 pr-3 text-text-primary">{row.server}</td>
                                        <td className={`py-1 font-mono break-all ${FANOUT_STYLE[row.level]}`}>{row.status}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {others.length > 0 && (
                <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                    {others.map((f) => (
                        <div key={f.key} className="contents">
                            <dt className="text-text-muted font-mono">{f.key}</dt>
                            <dd className="font-mono text-text-primary break-all">{f.value}</dd>
                        </div>
                    ))}
                </dl>
            )}

            {details !== null && details !== undefined && (
                <details className="text-xs">
                    <summary className="cursor-pointer text-text-muted hover:text-text-primary">{t('audit.detailRaw')}</summary>
                    <pre className="mt-2 p-3 rounded-lg bg-bg-primary border border-border overflow-x-auto max-h-96 text-[11px] leading-relaxed">
                        {JSON.stringify(details, null, 2)}
                    </pre>
                </details>
            )}
        </div>
    )
}
