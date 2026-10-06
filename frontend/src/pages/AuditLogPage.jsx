import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
    AlertCircle, CheckCircle2, Clock, Download, Loader2, RefreshCw, ScrollText, X, XCircle,
} from 'lucide-react'
import api from '../api'
import Pagination from '../components/common/Pagination'
import AuditEntryDetails, { ActorLabel } from '../components/audit/AuditEntryDetails'
import AuditRetentionModal from '../components/audit/AuditRetentionModal'
import {
    AUDIT_ACTION_GROUPS, AUDIT_ACTIONS, AUDIT_RESOURCE_TYPES, actionsInGroup, auditActionLabel, resourceTypeLabel,
} from '../constants/auditActions'
import { useDateFormat } from '../lib/useDateFormat'
import { useDialogFocus } from '../lib/useDialogFocus'
import {
    AUDIT_PAGE_SIZES, SEARCH_DEBOUNCE_MS, auditSearchObject, buildAuditParams, effectiveSearch, hasActiveFilters,
    isInvalidRange, parseAuditSearch, zoneHistoryHref,
} from '../components/audit/historyModel.js'

// Admin-Audit-Log (F7 §2.5, §6.3; Plan B.16: WS-F7-FE besitzt die Seite komplett).
// Quelle der Filter sind die URL-Search-Params (inkl. offset/limit); jede Filteraenderung setzt offset=0.
// Fehler werden angezeigt statt verschluckt (f79/f150/f159), Datumsformat ueber useDateFormat [F14].
// Routen-Gate: RequireAdmin in App.jsx; das Backend prueft selbst (403 erscheint als Banner).

const inputCls = 'w-full h-9 px-2 text-sm'
const labelCls = 'block text-[11px] uppercase tracking-wide text-text-muted mb-1'

function sameName(a, b) {
    const n = (v) => String(v || '').toLowerCase().replace(/\.$/, '')
    return n(a) === n(b)
}

export default function AuditLogPage() {
    const { t } = useTranslation()
    const { fmtDateTime } = useDateFormat()
    const navigate = useNavigate()
    const drawerTitleId = useId()
    const [searchParams, setSearchParams] = useSearchParams()
    const searchKey = searchParams.toString()
    const { filters, offset, limit } = useMemo(() => parseAuditSearch(new URLSearchParams(searchKey)), [searchKey])
    const invalidRange = isInvalidRange(filters.date_from, filters.date_to)

    const [data, setData] = useState({ entries: [], total: 0 })
    const [loaded, setLoaded] = useState(false)
    const [refreshing, setRefreshing] = useState(false)
    const [error, setError] = useState('')
    const [reloadKey, setReloadKey] = useState(0)
    const [users, setUsers] = useState([])
    const [usersFailed, setUsersFailed] = useState(false)
    const [servers, setServers] = useState([])
    const [drawerEntry, setDrawerEntry] = useState(null)
    const [drawerLoading, setDrawerLoading] = useState(false)
    const [drawerError, setDrawerError] = useState('')
    const [showRetention, setShowRetention] = useState(false)
    const [exporting, setExporting] = useState(false)
    const [exportError, setExportError] = useState('')

    // Freitextfelder lokal halten und entprellt in die URL schreiben; URL-Aenderungen (Zurueck) uebernehmen
    const [qInput, setQInput] = useState(filters.q)
    const [zoneInput, setZoneInput] = useState(filters.zone)
    const [prevQ, setPrevQ] = useState(filters.q)
    const [prevZone, setPrevZone] = useState(filters.zone)
    if (prevQ !== filters.q) {
        setPrevQ(filters.q)
        if (effectiveSearch(qInput) !== filters.q) setQInput(filters.q)
    }
    if (prevZone !== filters.zone) {
        setPrevZone(filters.zone)
        if (zoneInput.trim() !== filters.zone) setZoneInput(filters.zone)
    }

    function applyFilters(patch, { replace = true } = {}) {
        setSearchParams(auditSearchObject({ filters: { ...filters, ...patch }, offset: 0, limit }), { replace })
    }

    function setPage(nextOffset) {
        setSearchParams(auditSearchObject({ filters, offset: nextOffset, limit }))
    }

    function setPageSize(nextLimit) {
        setSearchParams(auditSearchObject({ filters, offset: 0, limit: nextLimit }))
    }

    function resetFilters() {
        setQInput('')
        setZoneInput('')
        setSearchParams(auditSearchObject({ filters: {}, offset: 0, limit }))
    }

    // Liste laden
    useEffect(() => {
        if (invalidRange) return undefined
        const ctrl = new AbortController()
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Lade-Anzeige beim Nachladen (Liste bleibt sichtbar)
        setRefreshing(true)
        const current = parseAuditSearch(new URLSearchParams(searchKey))
        api.getAuditLog({ ...buildAuditParams(current.filters, { offset: current.offset, limit: current.limit }), signal: ctrl.signal })
            .then((res) => {
                setData({ entries: Array.isArray(res?.entries) ? res.entries : [], total: Number(res?.total) || 0 })
                setError('')
                setLoaded(true)
                setRefreshing(false)
            })
            .catch((err) => {
                if (err?.name === 'AbortError') return
                setError(err.message || String(err))
                setLoaded(true)
                setRefreshing(false)
            })
        return () => ctrl.abort()
    }, [searchKey, reloadKey, invalidRange])

    // Auswahllisten: Benutzer (Fehler deaktiviert nur dieses Select) und Server
    useEffect(() => {
        let alive = true
        api.listUsers()
            .then((res) => { if (alive) setUsers(Array.isArray(res?.users) ? res.users : []) })
            .catch(() => { if (alive) setUsersFailed(true) })
        api.getServers()
            .then((res) => { if (alive) setServers(Array.isArray(res?.servers) ? res.servers : []) })
            .catch(() => { if (alive) setServers([]) })
        return () => { alive = false }
    }, [])

    // Suche und Zone entprellt (400 ms)
    useEffect(() => {
        const nextQ = effectiveSearch(qInput)
        const nextZone = zoneInput.trim()
        if (nextQ === filters.q && nextZone === filters.zone) return undefined
        const timer = setTimeout(() => {
            setSearchParams(auditSearchObject({ filters: { ...filters, q: nextQ, zone: nextZone }, offset: 0, limit }), { replace: true })
        }, SEARCH_DEBOUNCE_MS)
        return () => clearTimeout(timer)
    }, [qInput, zoneInput, filters, limit, setSearchParams])

    // Drawer: Fokus, Tab-Falle, ESC schliesst, Fokus zurueck auf die Tabellenzeile (lib/useDialogFocus)
    const closeDrawer = useCallback(() => setDrawerEntry(null), [])
    const drawerRef = useDialogFocus({ onClose: closeDrawer, active: !!drawerEntry })

    function openDrawer(entry) {
        setDrawerEntry(entry)
        setDrawerError('')
        setDrawerLoading(false)
    }

    async function loadFullEntry() {
        if (!drawerEntry) return
        const id = drawerEntry.id
        setDrawerLoading(true)
        setDrawerError('')
        try {
            const full = await api.getAuditLogEntry(id)
            setDrawerEntry((cur) => (cur && cur.id === id ? full : cur))
        } catch (err) {
            setDrawerError(err.message || String(err))
        } finally {
            setDrawerLoading(false)
        }
    }

    async function handleExport() {
        if (invalidRange) {
            setExportError(t('audit.filterInvalidRange'))
            return
        }
        setExporting(true)
        setExportError('')
        try {
            await api.exportAuditLogCsv(buildAuditParams(filters))
        } catch (e) {
            setExportError(e.message || String(e))
        } finally {
            setExporting(false)
        }
    }

    const filtersActive = hasActiveFilters(filters)
    const entries = data.entries
    // ein abgebrochener Lauf (Zeitraum ungueltig) laesst refreshing stehen - dann nicht als "laedt" anzeigen
    const loadingNow = refreshing && !invalidRange
    const busy = loadingNow && loaded
    const actionGroups = useMemo(
        () => AUDIT_ACTION_GROUPS.map((g) => ({ group: g, actions: actionsInGroup(AUDIT_ACTIONS, g) })).filter((g) => g.actions.length > 0),
        [],
    )
    const knownAction = !filters.action || AUDIT_ACTIONS.some((a) => a.action === filters.action)
    const knownType = !filters.resource_type || AUDIT_RESOURCE_TYPES.includes(filters.resource_type)
    const sortedUsers = useMemo(() => [...users].sort((a, b) => String(a.username).localeCompare(String(b.username))), [users])

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-text-primary flex items-center gap-2">
                        {t('audit.title')}
                        {busy && <Loader2 className="w-5 h-5 animate-spin text-text-muted" aria-label={t('common.loading')} />}
                    </h1>
                    <p className="text-text-muted text-sm mt-1">{t('audit.subtitle')}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <button
                        type="button"
                        onClick={() => setReloadKey((k) => k + 1)}
                        disabled={loadingNow || invalidRange}
                        className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-border bg-bg-secondary/50 hover:bg-bg-hover text-text-primary text-sm font-medium disabled:opacity-50"
                    >
                        <RefreshCw className={`w-4 h-4 ${loadingNow ? 'animate-spin' : ''}`} aria-hidden="true" /> {t('audit.refresh')}
                    </button>
                    <button
                        type="button"
                        onClick={() => setShowRetention(true)}
                        className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-border bg-bg-secondary/50 hover:bg-bg-hover text-text-primary text-sm font-medium"
                    >
                        <Clock className="w-4 h-4" aria-hidden="true" /> {t('audit.retentionButton')}
                    </button>
                    <button
                        type="button"
                        onClick={handleExport}
                        disabled={exporting}
                        title={t('audit.exportHint')}
                        className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-border bg-bg-secondary/50 hover:bg-bg-hover text-text-primary text-sm font-medium disabled:opacity-50"
                    >
                        {exporting ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Download className="w-4 h-4" aria-hidden="true" />}
                        {t('audit.exportCsv')}
                    </button>
                </div>
            </div>

            {exportError && (
                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm flex items-start gap-2" role="alert">
                    <span className="flex-1 break-words">{exportError}</span>
                    <button type="button" onClick={() => setExportError('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            {/* Filterleiste */}
            <div className="glass-card p-4 space-y-3">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    <div>
                        <label className={labelCls} htmlFor="audit-action">{t('audit.action')}</label>
                        <select id="audit-action" className={inputCls} value={filters.action} onChange={(e) => applyFilters({ action: e.target.value })}>
                            <option value="">{t('common.all')}</option>
                            {!knownAction && <option value={filters.action}>{filters.action}</option>}
                            {actionGroups.map((g) => (
                                <optgroup key={g.group} label={t(`audit.groups.${g.group}`, { defaultValue: g.group })}>
                                    {g.actions.map((a) => <option key={a} value={a}>{auditActionLabel(t, a)}</option>)}
                                </optgroup>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="audit-rt">{t('audit.resourceType')}</label>
                        <select id="audit-rt" className={inputCls} value={filters.resource_type} onChange={(e) => applyFilters({ resource_type: e.target.value })}>
                            <option value="">{t('common.all')}</option>
                            {!knownType && <option value={filters.resource_type}>{filters.resource_type}</option>}
                            {AUDIT_RESOURCE_TYPES.map((rt) => <option key={rt} value={rt}>{resourceTypeLabel(t, rt)}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="audit-status">{t('audit.status')}</label>
                        <select id="audit-status" className={inputCls} value={filters.status} onChange={(e) => applyFilters({ status: e.target.value })}>
                            <option value="">{t('common.all')}</option>
                            <option value="success">{t('audit.ok')}</option>
                            <option value="error">{t('audit.error')}</option>
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="audit-user">{t('audit.user')}</label>
                        <select
                            id="audit-user"
                            className={inputCls}
                            value={filters.user_id}
                            disabled={usersFailed}
                            title={usersFailed ? t('audit.usersUnavailable') : undefined}
                            onChange={(e) => applyFilters({ user_id: e.target.value })}
                        >
                            <option value="">{t('common.all')}</option>
                            {filters.user_id && !users.some((u) => String(u.id) === filters.user_id) && (
                                <option value={filters.user_id}>#{filters.user_id}</option>
                            )}
                            {sortedUsers.map((u) => <option key={u.id} value={String(u.id)}>{u.username}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="audit-zone">{t('audit.zone')}</label>
                        <input
                            id="audit-zone"
                            type="text"
                            className={inputCls}
                            value={zoneInput}
                            maxLength={255}
                            placeholder={t('audit.filterZonePlaceholder')}
                            onChange={(e) => setZoneInput(e.target.value)}
                        />
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="audit-server">{t('audit.server')}</label>
                        <select id="audit-server" className={inputCls} value={filters.server_name} onChange={(e) => applyFilters({ server_name: e.target.value })}>
                            <option value="">{t('common.all')}</option>
                            {filters.server_name && !servers.some((s) => s.name === filters.server_name) && (
                                <option value={filters.server_name}>{filters.server_name}</option>
                            )}
                            {servers.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                        </select>
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="audit-from">{t('audit.filterFrom')}</label>
                        <input id="audit-from" type="datetime-local" className={inputCls} value={filters.date_from} onChange={(e) => applyFilters({ date_from: e.target.value })} />
                    </div>
                    <div>
                        <label className={labelCls} htmlFor="audit-to">{t('audit.filterTo')}</label>
                        <input id="audit-to" type="datetime-local" className={inputCls} value={filters.date_to} onChange={(e) => applyFilters({ date_to: e.target.value })} />
                    </div>
                    <div className="sm:col-span-2 lg:col-span-3">
                        <label className={labelCls} htmlFor="audit-q">{t('audit.filterSearch')}</label>
                        <input
                            id="audit-q"
                            type="search"
                            className={inputCls}
                            value={qInput}
                            maxLength={100}
                            placeholder={t('audit.filterSearchPlaceholder')}
                            onChange={(e) => setQInput(e.target.value)}
                        />
                    </div>
                    <div className="flex items-end">
                        {filtersActive && (
                            <button type="button" onClick={resetFilters} className="h-9 text-sm text-accent-light hover:underline">
                                {t('audit.filterReset')}
                            </button>
                        )}
                    </div>
                </div>
                {invalidRange && <p className="text-xs text-warning" role="alert">{t('audit.filterInvalidRange')}</p>}
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3" role="alert">
                    <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
                    <p className="text-sm flex-1 break-words">{t('audit.loadError', { message: error })}</p>
                    <button type="button" onClick={() => setReloadKey((k) => k + 1)} className="text-xs hover:underline shrink-0">
                        {t('common.retry')}
                    </button>
                </div>
            )}

            {!loaded ? (
                <div className="flex items-center justify-center h-64">
                    <Loader2 className="w-8 h-8 text-accent animate-spin" aria-label={t('common.loading')} />
                </div>
            ) : (
                <div className={`glass-card overflow-hidden transition-opacity ${busy ? 'opacity-60' : ''}`}>
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm min-w-[860px]">
                            <thead>
                                <tr className="border-b border-border">
                                    <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.timestamp')}</th>
                                    <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.user')}</th>
                                    <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.action')}</th>
                                    <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.resourceType')}</th>
                                    <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.resource')}</th>
                                    <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.server')}</th>
                                    <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.status')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {entries.map((log) => (
                                    <tr
                                        key={log.id}
                                        tabIndex={0}
                                        onClick={() => openDrawer(log)}
                                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDrawer(log) } }}
                                        className="border-b border-border/30 hover:bg-bg-hover/30 cursor-pointer focus:outline-none focus-visible:bg-bg-hover/40"
                                    >
                                        <td className="p-3 text-xs text-text-secondary whitespace-nowrap">{fmtDateTime(log.timestamp)}</td>
                                        <td className="p-3 text-xs text-text-primary"><ActorLabel entry={log} t={t} /></td>
                                        <td className="p-3 text-xs font-medium text-text-primary">{auditActionLabel(t, log.action)}</td>
                                        <td className="p-3">
                                            <span className="text-xs px-1.5 py-0.5 bg-accent/10 text-accent-light rounded">{resourceTypeLabel(t, log.resource_type)}</span>
                                        </td>
                                        <td className="p-3 text-xs text-text-secondary font-mono break-all">
                                            {log.resource_name || '–'}
                                            {log.zone_name && !sameName(log.zone_name, log.resource_name) && (
                                                <div className="text-[11px] text-text-muted">{log.zone_name}</div>
                                            )}
                                        </td>
                                        <td className="p-3 text-xs text-text-muted">{log.server_name || '-'}</td>
                                        <td className="p-3">
                                            {log.status === 'success'
                                                ? <span className="inline-flex items-center gap-1 text-xs text-success"><CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" /> {t('audit.ok')}</span>
                                                : <span className="inline-flex items-center gap-1 text-xs text-danger"><XCircle className="w-3.5 h-3.5" aria-hidden="true" /> {t('audit.error')}</span>}
                                        </td>
                                    </tr>
                                ))}
                                {entries.length === 0 && !error && (
                                    <tr><td colSpan={7} className="p-12 text-center text-text-muted">
                                        <ScrollText className="w-12 h-12 mx-auto mb-3 opacity-30" aria-hidden="true" />
                                        <p>{filtersActive ? t('audit.noEntriesFiltered') : t('audit.noEntries')}</p>
                                        {filtersActive && (
                                            <button type="button" onClick={resetFilters} className="mt-3 text-sm text-accent-light hover:underline">
                                                {t('audit.filterReset')}
                                            </button>
                                        )}
                                    </td></tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {loaded && data.total > 0 && (
                <Pagination
                    offset={offset}
                    limit={limit}
                    total={data.total}
                    onPage={setPage}
                    pageSizes={AUDIT_PAGE_SIZES}
                    onPageSize={setPageSize}
                    t={t}
                />
            )}

            {/* Detail-Drawer */}
            {drawerEntry && (
                <div className="fixed inset-0 z-50 bg-black/50" onClick={() => setDrawerEntry(null)}>
                    <aside
                        ref={drawerRef}
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby={drawerTitleId}
                        className="fixed inset-y-0 right-0 w-full max-w-2xl bg-bg-primary border-l border-border shadow-2xl overflow-y-auto"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 px-5 py-4 border-b border-border bg-bg-primary">
                            <h2 id={drawerTitleId} className="text-lg font-bold text-text-primary">{t('audit.detailTitle', { id: drawerEntry.id })}</h2>
                            <button
                                type="button"
                                onClick={() => setDrawerEntry(null)}
                                className="p-1 rounded-lg hover:bg-bg-hover text-text-muted"
                                aria-label={t('common.close')}
                            >
                                <X className="w-5 h-5" />
                            </button>
                        </div>
                        <div className="p-5 space-y-4">
                            {drawerError && (
                                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm" role="alert">
                                    {t('audit.detailLoadError', { message: drawerError })}
                                </div>
                            )}
                            <AuditEntryDetails
                                entry={drawerEntry}
                                t={t}
                                showFields
                                onLoadAll={drawerEntry.details_truncated ? loadFullEntry : undefined}
                                loadingAll={drawerLoading}
                                onOpenZoneHistory={zoneHistoryHref(drawerEntry) ? () => navigate(zoneHistoryHref(drawerEntry)) : undefined}
                            />
                        </div>
                    </aside>
                </div>
            )}

            {showRetention && <AuditRetentionModal onClose={() => setShowRetention(false)} />}
        </div>
    )
}
