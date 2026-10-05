import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
    AlertCircle, AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, History, Info, KeyRound, Loader2, RefreshCw,
    RotateCcw, X, XCircle,
} from 'lucide-react'
import api from '../../api'
import Pagination from '../common/Pagination'
import { useDateFormat } from '../../lib/useDateFormat'
import { ALL_RECORD_TYPE_KEYS } from '../../constants/dnsRecordTypes'
import { HISTORY_ACTIONS, auditActionLabel, resourceTypeLabel } from '../../constants/auditActions'
import AuditEntryDetails, { ActorLabel } from '../audit/AuditEntryDetails'
import RollbackModal from './RollbackModal'
import {
    EMPTY_HISTORY_FILTERS, HISTORY_AREAS, HISTORY_DEFAULT_LIMIT, HISTORY_PAGE_SIZES, SEARCH_DEBOUNCE_MS,
    buildHistoryParams, displayName, effectiveSearch, entrySummary, hasActiveFilters, isInvalidRange,
    notablePrimaryOutcome, tokenAuth,
} from '../audit/historyModel.js'

// Zonenverlauf (F7 §2.1-2.4, §6.1). Eigener Fehlerzustand im Panel (nicht auf Seitenebene), Sequenzzaehler gegen
// veraltete Antworten, Liste bleibt beim Nachladen sichtbar (opacity-60).
// Props:
//   server, zoneId, zoneKey            Identitaet (zoneKey = Zone klein mit Punkt)
//   recordFilter {name,type}|null      aus ?hname/&htype (Zeilen-Aktion "Verlauf")
//   onClearRecordFilter()              entfernt hname/htype
//   focusEntryId                       aus ?entry= (Audit-Log "Im Zonenverlauf oeffnen")
//   onClearFocus()                     entfernt entry
//   onRolledBack(res, entry)           nach erfolgreichem Rollback (Seitenbanner, stilles Nachladen der Records)
//   canEdit                            kosmetisch (Rollback-Button); die Pruefung macht das Backend
//   t                                  optional
const STATUS_ICON_OK = <CheckCircle2 className="w-4 h-4 text-success shrink-0" aria-hidden="true" />
const STATUS_ICON_ERR = <XCircle className="w-4 h-4 text-danger shrink-0" aria-hidden="true" />

const inputCls = 'w-full h-9 px-2 text-sm'
const labelCls = 'block text-[11px] uppercase tracking-wide text-text-muted mb-1'

function Badge({ className = '', children, title }) {
    return (
        <span title={title} className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[11px] ${className}`}>
            {children}
        </span>
    )
}

function EntryBadges({ entry, t }) {
    const token = tokenAuth(entry.details)
    const outcome = notablePrimaryOutcome(entry.details)
    return (
        <>
            {entry.revert_of_id != null && (
                <Badge className="border-accent/30 bg-accent/10 text-accent-light">{t('history.revertOf', { id: entry.revert_of_id })}</Badge>
            )}
            {entry.reverted_by_id != null && (
                <Badge className="border-border bg-bg-hover text-text-secondary">{t('history.revertedBy', { id: entry.reverted_by_id })}</Badge>
            )}
            {entry.details?.forced && (
                <Badge className="border-warning/40 bg-warning/10 text-warning">{t('history.forced')}</Badge>
            )}
            {token && (
                <Badge className="border-border text-text-secondary" title={token.name || undefined}>
                    <KeyRound className="w-3 h-3" aria-hidden="true" /> {t('history.viaToken')}
                </Badge>
            )}
            {entry.before_recreate && (
                <Badge className="border-border bg-bg-secondary text-text-muted" title={t('history.beforeRecreateHint')}>
                    {t('history.beforeRecreate')}
                </Badge>
            )}
            {outcome && (
                <Badge
                    className={outcome === 'verified_after_timeout' ? 'border-warning/40 bg-warning/10 text-warning' : 'border-danger/40 bg-danger/10 text-danger'}
                    title={t(`history.outcome.${outcome}`)}
                >
                    <AlertTriangle className="w-3 h-3" aria-hidden="true" /> {t(`history.outcomeShort.${outcome}`)}
                </Badge>
            )}
        </>
    )
}

function EntrySummaryText({ entry, zoneKey, t }) {
    const s = entrySummary(entry)
    if (s.kind === 'single') {
        const kindKey = { created: 'history.changeCreated', deleted: 'history.changeDeleted', modified: 'history.changeModified' }[s.change]
        return (
            <span className="text-text-secondary">
                <span className="font-mono text-text-primary">{displayName(s.name, zoneKey)}</span> {s.type} · {t(kindKey)}
            </span>
        )
    }
    if (s.kind === 'count') return <span className="text-text-secondary">{t('history.changeCount', { count: s.count })}</span>
    if (entry.resource_name) return <span className="font-mono text-text-secondary break-all">{entry.resource_name}</span>
    return null
}

function HistoryEntryCard({
    entry, zoneKey, t, fmtDateTime, expanded, onToggle, onRollback, canEdit, onLoadAll, loadingAll, pinned,
}) {
    const showButton = entry.can_rollback && canEdit !== false
    const reason = entry.rollback_blocked_reason || (entry.can_rollback ? 'no_write_permission' : null)
    return (
        <li className={`rounded-xl border ${pinned ? 'border-accent/50 bg-accent/5' : 'border-border/60 bg-bg-secondary/30'}`}>
            <div className="flex items-start gap-3 p-3">
                <button
                    type="button"
                    onClick={onToggle}
                    aria-expanded={expanded}
                    aria-label={expanded ? t('history.hideDetails') : t('history.showDetails')}
                    className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover shrink-0"
                >
                    {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                </button>
                <div className="flex-1 min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                        {entry.status === 'success' ? STATUS_ICON_OK : STATUS_ICON_ERR}
                        <span className="font-medium text-text-primary">{auditActionLabel(t, entry.action)}</span>
                        <Badge className="border-accent/30 bg-accent/10 text-accent-light">{resourceTypeLabel(t, entry.resource_type)}</Badge>
                        <EntrySummaryText entry={entry} zoneKey={zoneKey} t={t} />
                    </div>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
                        <span>#{entry.id}</span>
                        <span>{fmtDateTime(entry.timestamp)}</span>
                        <ActorLabel entry={entry} t={t} />
                        {entry.server_name && <span>{t('history.viaServer', { server: entry.server_name })}</span>}
                        <EntryBadges entry={entry} t={t} />
                    </div>
                </div>
                <div className="shrink-0">
                    {showButton ? (
                        <button
                            type="button"
                            onClick={() => onRollback(entry)}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs border border-accent/40 text-accent-light hover:bg-accent/10"
                        >
                            <RotateCcw className="w-3.5 h-3.5" aria-hidden="true" /> {t('history.rollback')}
                        </button>
                    ) : reason ? (
                        <span
                            className="inline-flex p-1 text-text-muted"
                            title={t(`history.blocked.${reason}`, { defaultValue: reason })}
                            aria-label={t(`history.blocked.${reason}`, { defaultValue: reason })}
                            role="img"
                        >
                            <Info className="w-4 h-4" />
                        </span>
                    ) : null}
                </div>
            </div>
            {expanded && (
                <div className="border-t border-border/60 p-3">
                    <AuditEntryDetails
                        entry={entry}
                        zoneKey={zoneKey}
                        t={t}
                        onLoadAll={entry.changes_truncated ? onLoadAll : undefined}
                        loadingAll={loadingAll}
                    />
                </div>
            )}
        </li>
    )
}

export default function ZoneHistoryPanel({
    server, zoneId, zoneKey, recordFilter, onClearRecordFilter, focusEntryId, onClearFocus, onRolledBack, canEdit,
    t: tProp,
}) {
    const { t: tHook } = useTranslation()
    const t = tProp || tHook
    const { fmtDateTime } = useDateFormat()

    const [entries, setEntries] = useState([])
    const [total, setTotal] = useState(0)
    const [actors, setActors] = useState([])
    const [offset, setOffset] = useState(0)
    const [limit, setLimit] = useState(HISTORY_DEFAULT_LIMIT)
    const [filters, setFilters] = useState(EMPTY_HISTORY_FILTERS)
    const [qInput, setQInput] = useState('')
    const [loaded, setLoaded] = useState(false)
    const [refreshing, setRefreshing] = useState(false)
    const [error, setError] = useState('')
    const [expanded, setExpanded] = useState(() => new Set())
    const [fullEntries, setFullEntries] = useState({})
    const [loadingAllId, setLoadingAllId] = useState(null)
    const [focused, setFocused] = useState(null)
    const [focusError, setFocusError] = useState('')
    const [focusLoading, setFocusLoading] = useState(false)
    const [rollbackEntry, setRollbackEntry] = useState(null)
    const [reloadKey, setReloadKey] = useState(0)
    const reqSeq = useRef(0)
    const focusSeq = useRef(0)

    const recName = recordFilter?.name || ''
    const recType = recordFilter?.type || ''
    const invalidRange = isInvalidRange(filters.date_from, filters.date_to)
    const filtersActive = hasActiveFilters(filters) || Boolean(recName)

    // Liste laden (Sequenzzaehler verwirft veraltete Antworten)
    useEffect(() => {
        if (invalidRange) return undefined
        const my = ++reqSeq.current
        const ctrl = new AbortController()
        const params = buildHistoryParams(filters, {
            offset, limit, recordFilter: recName ? { name: recName, type: recType } : null,
        })
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Lade-Anzeige beim Nachladen (F7 §2.1 Nr. 5)
        setRefreshing(true)
        api.getZoneHistory(server, zoneId, { ...params, signal: ctrl.signal })
            .then((res) => {
                if (my !== reqSeq.current) return
                setEntries(Array.isArray(res?.entries) ? res.entries : [])
                setTotal(Number(res?.total) || 0)
                if (Array.isArray(res?.actors)) setActors(res.actors)
                setError('')
                // Seite hinter dem Ende (z. B. nach Bereinigung): auf die letzte Seite springen
                if ((res?.entries || []).length === 0 && offset > 0 && Number(res?.total) > 0) {
                    setOffset(Math.max(0, Math.floor((Number(res.total) - 1) / limit) * limit))
                }
            })
            .catch((err) => {
                if (err?.name === 'AbortError' || my !== reqSeq.current) return
                setError(err.message || String(err))
            })
            .finally(() => {
                if (my !== reqSeq.current) return
                setRefreshing(false)
                setLoaded(true)
            })
        return () => ctrl.abort()
    }, [server, zoneId, filters, offset, limit, recName, recType, reloadKey, invalidRange])

    // Suche entprellt (400 ms, ab 2 Zeichen)
    useEffect(() => {
        const next = effectiveSearch(qInput)
        if (next === filters.q) return undefined
        const timer = setTimeout(() => {
            setFilters((f) => ({ ...f, q: next }))
            setOffset(0)
        }, SEARCH_DEBOUNCE_MS)
        return () => clearTimeout(timer)
    }, [qInput, filters.q])

    // Ausgewaehlter Eintrag (?entry=)
    const loadFocus = useCallback(async (id) => {
        const my = ++focusSeq.current
        setFocusLoading(true)
        setFocusError('')
        try {
            const entry = await api.getZoneHistoryEntry(server, zoneId, id)
            if (my !== focusSeq.current) return
            setFocused(entry)
        } catch (err) {
            if (my !== focusSeq.current) return
            setFocused(null)
            setFocusError(err.message || String(err))
        } finally {
            if (my === focusSeq.current) setFocusLoading(false)
        }
    }, [server, zoneId])

    useEffect(() => {
        const counter = focusSeq
        if (focusEntryId == null || focusEntryId === '') {
            counter.current++
            /* eslint-disable react-hooks/set-state-in-effect -- Auswahl folgt dem URL-Parameter ?entry= */
            setFocused(null)
            setFocusError('')
            setFocusLoading(false)
            /* eslint-enable react-hooks/set-state-in-effect */
            return undefined
        }
        loadFocus(focusEntryId)
        return () => { counter.current++ }
    }, [focusEntryId, loadFocus])

    const setFilter = (key, value) => {
        setFilters((f) => ({ ...f, [key]: value }))
        setOffset(0)
    }

    const resetFilters = () => {
        setFilters(EMPTY_HISTORY_FILTERS)
        setQInput('')
        setOffset(0)
        if (recName) onClearRecordFilter?.()
    }

    const toggle = (id) => {
        setExpanded((prev) => {
            const next = new Set(prev)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            return next
        })
    }

    async function loadAll(entry) {
        setLoadingAllId(entry.id)
        try {
            const full = await api.getZoneHistoryEntry(server, zoneId, entry.id)
            setFullEntries((m) => ({ ...m, [entry.id]: full }))
        } catch (err) {
            setError(err.message || String(err))
        } finally {
            setLoadingAllId(null)
        }
    }

    function handleRollbackDone(res) {
        const entry = rollbackEntry
        setRollbackEntry(null)
        onRolledBack?.(res, entry)
        setFullEntries({})
        setOffset(0)
        setReloadKey((k) => k + 1)
        if (focused) loadFocus(focused.id)
    }

    const actorOptions = useMemo(() => [...actors].sort((a, b) => (a.username || '').localeCompare(b.username || '')), [actors])
    const shown = entries.map((e) => fullEntries[e.id] || e)
    const busy = refreshing && loaded

    return (
        <section className="space-y-4" aria-labelledby="zone-history-title">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <h2 id="zone-history-title" className="text-lg font-semibold text-text-primary flex items-center gap-2">
                        <History className="w-5 h-5 text-accent-light" aria-hidden="true" /> {t('history.title')}
                        {busy && <Loader2 className="w-4 h-4 animate-spin text-text-muted" aria-label={t('common.loading')} />}
                    </h2>
                    <p className="text-sm text-text-muted">{t('history.subtitle')}</p>
                </div>
                <button
                    type="button"
                    onClick={() => setReloadKey((k) => k + 1)}
                    disabled={refreshing}
                    className="self-start inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                >
                    <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" /> {t('history.refresh')}
                </button>
            </div>

            {/* Filterleiste */}
            <div className="glass-card p-3 space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    <div>
                        <label className={labelCls} htmlFor="zh-area">{t('history.filterArea')}</label>
                        <select id="zh-area" className={inputCls} value={filters.resource_type} onChange={(e) => setFilter('resource_type', e.target.value)}>
                            <option value="">{t('common.all')}</option>
                            {HISTORY_AREAS.map((a) => <option key={a} value={a}>{resourceTypeLabel(t, a)}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="zh-action">{t('history.filterAction')}</label>
                        <select id="zh-action" className={inputCls} value={filters.action} onChange={(e) => setFilter('action', e.target.value)}>
                            <option value="">{t('common.all')}</option>
                            {HISTORY_ACTIONS.map((a) => <option key={a} value={a}>{auditActionLabel(t, a)}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="zh-type">{t('history.filterType')}</label>
                        <select
                            id="zh-type"
                            className={inputCls}
                            value={recName ? recType : filters.type}
                            disabled={Boolean(recName)}
                            onChange={(e) => setFilter('type', e.target.value)}
                        >
                            <option value="">{t('common.all')}</option>
                            {ALL_RECORD_TYPE_KEYS.map((k) => <option key={k} value={k}>{k}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="zh-q">{t('history.filterSearch')}</label>
                        <input
                            id="zh-q"
                            type="search"
                            className={inputCls}
                            value={qInput}
                            maxLength={100}
                            placeholder={t('history.filterSearchPlaceholder')}
                            onChange={(e) => setQInput(e.target.value)}
                        />
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="zh-user">{t('history.filterUser')}</label>
                        <select id="zh-user" className={inputCls} value={filters.user_id} onChange={(e) => setFilter('user_id', e.target.value)}>
                            <option value="">{t('common.all')}</option>
                            {actorOptions.map((a) => (
                                <option key={a.user_id} value={String(a.user_id)}>
                                    {a.username || t('history.unknownUser', { id: a.user_id })}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="zh-status">{t('history.filterStatus')}</label>
                        <select id="zh-status" className={inputCls} value={filters.status} onChange={(e) => setFilter('status', e.target.value)}>
                            <option value="">{t('common.all')}</option>
                            <option value="success">{t('audit.ok')}</option>
                            <option value="error">{t('audit.error')}</option>
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="zh-from">{t('history.filterFrom')}</label>
                        <input id="zh-from" type="datetime-local" className={inputCls} value={filters.date_from} onChange={(e) => setFilter('date_from', e.target.value)} />
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="zh-to">{t('history.filterTo')}</label>
                        <input id="zh-to" type="datetime-local" className={inputCls} value={filters.date_to} onChange={(e) => setFilter('date_to', e.target.value)} />
                    </div>
                </div>

                {(recName || filtersActive) && (
                    <div className="flex flex-wrap items-center gap-2">
                        {recName && (
                            <span className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-full bg-accent/10 border border-accent/30 text-accent-light text-xs">
                                {t('history.recordFilter', { name: displayName(recName, zoneKey), type: recType || '*' })}
                                <button
                                    type="button"
                                    onClick={onClearRecordFilter}
                                    className="p-0.5 rounded-full hover:bg-accent/20"
                                    aria-label={t('history.recordFilterRemove')}
                                    title={t('history.recordFilterRemove')}
                                >
                                    <X className="w-3 h-3" />
                                </button>
                            </span>
                        )}
                        {filtersActive && (
                            <button type="button" onClick={resetFilters} className="text-xs text-accent-light hover:underline">
                                {t('history.filterReset')}
                            </button>
                        )}
                    </div>
                )}

                {invalidRange && (
                    <p className="text-xs text-warning" role="alert">{t('history.filterInvalidRange')}</p>
                )}
            </div>

            {/* Ausgewaehlter Eintrag (?entry=) */}
            {focusError && (
                <div className="p-3 rounded-xl bg-danger/10 border border-danger/30 text-danger text-sm flex items-start gap-2" role="alert">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    <span className="flex-1">{focusError}</span>
                    <button type="button" onClick={onClearFocus} className="text-xs hover:underline">{t('history.focusedClear')}</button>
                </div>
            )}
            {focusLoading && !focused && (
                <div className="flex items-center gap-2 text-sm text-text-muted"><Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> {t('common.loading')}</div>
            )}
            {focused && (
                <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium text-text-primary">{t('history.focusedEntry', { id: focused.id })}</span>
                        <button type="button" onClick={onClearFocus} className="text-xs text-accent-light hover:underline">{t('history.focusedClear')}</button>
                    </div>
                    <ul>
                        <HistoryEntryCard
                            entry={focused}
                            zoneKey={zoneKey}
                            t={t}
                            fmtDateTime={fmtDateTime}
                            expanded
                            pinned
                            onToggle={onClearFocus}
                            onRollback={setRollbackEntry}
                            canEdit={canEdit}
                        />
                    </ul>
                </div>
            )}

            {/* Fehler im Panel */}
            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3" role="alert">
                    <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
                    <p className="text-sm flex-1 break-words">{t('history.loadError', { message: error })}</p>
                    <button type="button" onClick={() => setReloadKey((k) => k + 1)} className="text-xs hover:underline shrink-0">
                        {t('common.retry')}
                    </button>
                </div>
            )}

            {/* Liste */}
            {!loaded ? (
                <div className="flex items-center justify-center py-12">
                    <Loader2 className="w-8 h-8 text-accent animate-spin" aria-label={t('common.loading')} />
                </div>
            ) : shown.length === 0 ? (
                !error && (
                    <div className="glass-card p-10 text-center text-text-muted text-sm space-y-3">
                        <History className="w-10 h-10 mx-auto opacity-30" aria-hidden="true" />
                        <p>{filtersActive ? t('history.emptyFiltered') : t('history.empty')}</p>
                        {filtersActive && (
                            <button type="button" onClick={resetFilters} className="text-accent-light hover:underline text-sm">
                                {t('history.filterReset')}
                            </button>
                        )}
                    </div>
                )
            ) : (
                <ul className={`space-y-2 transition-opacity ${busy ? 'opacity-60' : ''}`}>
                    {shown.map((entry) => (
                        <HistoryEntryCard
                            key={entry.id}
                            entry={entry}
                            zoneKey={zoneKey}
                            t={t}
                            fmtDateTime={fmtDateTime}
                            expanded={expanded.has(entry.id)}
                            onToggle={() => toggle(entry.id)}
                            onRollback={setRollbackEntry}
                            canEdit={canEdit}
                            onLoadAll={() => loadAll(entry)}
                            loadingAll={loadingAllId === entry.id}
                        />
                    ))}
                </ul>
            )}

            {loaded && total > 0 && (
                <Pagination
                    offset={offset}
                    limit={limit}
                    total={total}
                    onPage={setOffset}
                    pageSizes={HISTORY_PAGE_SIZES}
                    onPageSize={(n) => { setLimit(n); setOffset(0) }}
                    t={t}
                />
            )}

            {rollbackEntry && (
                <RollbackModal
                    server={server}
                    zoneId={zoneId}
                    zoneKey={zoneKey}
                    entry={rollbackEntry}
                    onClose={() => setRollbackEntry(null)}
                    onDone={handleRollbackDone}
                    t={t}
                />
            )}
        </section>
    )
}
