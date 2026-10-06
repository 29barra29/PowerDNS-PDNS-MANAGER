import { Fragment, useCallback, useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, ChevronDown, ChevronRight, Copy, Loader2, RefreshCw, RotateCcw, ScrollText, X } from 'lucide-react'
import api from '../../api'
import Pagination from '../common/Pagination'
import { useDateFormat } from '../../lib/useDateFormat'
import { useDialogFocus } from '../../lib/useDialogFocus'
import WebhookStatusBadge from './WebhookStatusBadge'
import {
    AUTO_REFRESH_MS, DELIVERY_STATUSES, PAGE_SIZE,
    deliveryEventOptions, errorLabel, eventLabel, prettyJson, responseSummary, retryAction, shouldAutoRefresh,
    statusLabel,
} from './webhookUi'

// Zustellprotokoll eines Webhooks als Drawer rechts (F6 2.7, 6.3).
// Props: { hook, availableEvents, maxAttempts, retentionDays, onClose }
// Eigene Fehler-/Infozeile im Drawer; Auto-Refresh alle 5 s, solange sichtbare Zeilen offen sind und der Tab
// sichtbar ist. Aeltere Antworten werden per Sequenzzaehler verworfen.
export default function WebhookDeliveriesDrawer({ hook, availableEvents, maxAttempts, retentionDays, onClose }) {
    const { t } = useTranslation()
    const { fmtDateTime, fmtRelative } = useDateFormat()
    const titleId = useId()
    const [items, setItems] = useState([])
    const [total, setTotal] = useState(0)
    const [offset, setOffset] = useState(0)
    const [statusFilter, setStatusFilter] = useState('')
    const [eventFilter, setEventFilter] = useState('')
    const [loading, setLoading] = useState(true)
    const [drawerError, setDrawerError] = useState('')
    const [drawerInfo, setDrawerInfo] = useState('')
    const [expanded, setExpanded] = useState(null)
    const [payloads, setPayloads] = useState({}) // id -> { loading, data, error, shown }
    const [busyRetry, setBusyRetry] = useState(null)
    const [copiedId, setCopiedId] = useState(null)
    const [copyFailed, setCopyFailed] = useState(false)
    const seqRef = useRef(0)
    const abortRef = useRef(null)
    const closeRef = useRef(null)

    const load = useCallback(async ({ silent = false } = {}) => {
        const seq = ++seqRef.current
        abortRef.current?.abort()
        const ctrl = new AbortController()
        abortRef.current = ctrl
        if (!silent) setLoading(true)
        try {
            const res = await api.getWebhookDeliveries(hook.id, {
                limit: PAGE_SIZE, offset, status: statusFilter, event: eventFilter, signal: ctrl.signal,
            })
            if (seq !== seqRef.current) return
            setItems(Array.isArray(res?.deliveries) ? res.deliveries : [])
            setTotal(Number(res?.total || 0))
            setDrawerError('')
        } catch (err) {
            if (err?.name === 'AbortError' || seq !== seqRef.current) return
            setDrawerError(err.message)
        } finally {
            if (seq === seqRef.current) setLoading(false)
        }
    }, [hook.id, offset, statusFilter, eventFilter])

    useEffect(() => {
        queueMicrotask(() => load())
    }, [load])

    useEffect(() => () => abortRef.current?.abort(), [])

    // Fokus auf "Schliessen", Tab-Falle, ESC schliesst, Fokus-Rueckgabe an den ausloesenden Button (L11)
    const dialogRef = useDialogFocus({ onClose, initialFocusRef: closeRef })

    const autoRefresh = shouldAutoRefresh(items)
    useEffect(() => {
        if (!autoRefresh) return undefined
        const timer = setInterval(() => {
            if (typeof document === 'undefined' || document.visibilityState === 'visible') load({ silent: true })
        }, AUTO_REFRESH_MS)
        return () => clearInterval(timer)
    }, [autoRefresh, load])

    function changeStatus(v) {
        setStatusFilter(v)
        setOffset(0)
        setExpanded(null)
    }

    function changeEvent(v) {
        setEventFilter(v)
        setOffset(0)
        setExpanded(null)
    }

    async function togglePayload(d) {
        const cur = payloads[d.id]
        if (cur?.data && cur.shown) {
            setPayloads((p) => ({ ...p, [d.id]: { ...cur, shown: false } }))
            return
        }
        if (cur?.data) {
            setPayloads((p) => ({ ...p, [d.id]: { ...cur, shown: true } }))
            return
        }
        setPayloads((p) => ({ ...p, [d.id]: { loading: true } }))
        try {
            const data = await api.getWebhookDelivery(hook.id, d.id)
            setPayloads((p) => ({ ...p, [d.id]: { data, shown: true } }))
        } catch (err) {
            setPayloads((p) => ({ ...p, [d.id]: { error: err.message } }))
        }
    }

    async function handleRetry(d) {
        const action = retryAction(d)
        if (!action) return
        if (action.confirmKey && !window.confirm(t(action.confirmKey))) return
        setBusyRetry(d.id)
        setDrawerError('')
        setDrawerInfo('')
        try {
            await api.retryWebhookDelivery(hook.id, d.id)
            setDrawerInfo(t('webhooks.retryQueued'))
            setPayloads((p) => {
                const next = { ...p }
                delete next[d.id]
                return next
            })
            await load({ silent: true })
        } catch (err) {
            setDrawerError(err.message)
        } finally {
            setBusyRetry(null)
        }
    }

    async function copyText(id, text) {
        setCopyFailed(false)
        try {
            if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
            await navigator.clipboard.writeText(String(text ?? ''))
            setCopiedId(id)
            setTimeout(() => setCopiedId((c) => (c === id ? null : c)), 2000)
        } catch {
            setCopyFailed(true)
        }
    }

    const filtered = Boolean(statusFilter || eventFilter)
    const eventOptions = deliveryEventOptions(availableEvents)
    const selectCls = 'h-9 px-2 text-sm rounded-lg border border-border bg-bg-primary text-text-primary'

    return (
        <div
            className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex justify-end"
            onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
        >
            <aside
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                className="h-full w-full max-w-4xl bg-bg-secondary border-l border-border shadow-2xl flex flex-col"
            >
                <header className="flex items-start justify-between gap-3 px-4 sm:px-6 pt-5 pb-3 border-b border-border/50">
                    <div className="min-w-0">
                        <h2 id={titleId} className="text-lg font-bold flex items-center gap-2 text-text-primary">
                            <ScrollText className="w-5 h-5 shrink-0" aria-hidden="true" />
                            <span className="truncate">{t('webhooks.drawerTitle', { name: hook.name })}</span>
                        </h2>
                        <p className="text-xs text-text-muted mt-1">
                            {t('webhooks.drawerHint', { days: retentionDays ?? 30, max: maxAttempts ?? 6 })}
                        </p>
                    </div>
                    <button
                        ref={closeRef}
                        type="button"
                        onClick={onClose}
                        className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-hover"
                        aria-label={t('common.close')}
                        title={t('common.close')}
                    >
                        <X className="w-5 h-5" aria-hidden="true" />
                    </button>
                </header>

                <div className="px-4 sm:px-6 py-3 flex flex-wrap items-end gap-3 border-b border-border/50">
                    <label className="flex flex-col gap-1 text-xs text-text-secondary">
                        {t('webhooks.filterStatus')}
                        <select value={statusFilter} onChange={(e) => changeStatus(e.target.value)} className={selectCls}>
                            <option value="">{t('common.all')}</option>
                            {DELIVERY_STATUSES.map((s) => <option key={s} value={s}>{statusLabel(t, s)}</option>)}
                        </select>
                    </label>
                    <label className="flex flex-col gap-1 text-xs text-text-secondary">
                        {t('webhooks.filterEvent')}
                        <select value={eventFilter} onChange={(e) => changeEvent(e.target.value)} className={selectCls}>
                            <option value="">{t('common.all')}</option>
                            {eventOptions.map((ev) => <option key={ev} value={ev}>{eventLabel(t, ev)}</option>)}
                        </select>
                    </label>
                    <div className="flex items-center gap-2 ml-auto">
                        {autoRefresh && <span className="text-[11px] text-text-muted">{t('webhooks.autoRefresh')}</span>}
                        <button
                            type="button"
                            onClick={() => load()}
                            disabled={loading}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-40"
                        >
                            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
                            {t('webhooks.refresh')}
                        </button>
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-4 space-y-3">
                    {drawerError && (
                        <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm break-words">
                            {drawerError}
                        </div>
                    )}
                    {drawerInfo && (
                        <div role="status" className="p-3 rounded-lg bg-success/10 border border-success/30 text-success text-sm">
                            {drawerInfo}
                        </div>
                    )}
                    {copyFailed && (
                        <div role="alert" className="p-2 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs">
                            {t('secretModal.copyFailed')}
                        </div>
                    )}

                    {loading && items.length === 0 ? (
                        <div className="flex justify-center py-10">
                            <Loader2 className="w-6 h-6 animate-spin text-text-muted" aria-label={t('common.loading')} />
                        </div>
                    ) : items.length === 0 ? (
                        <p className="text-sm text-text-muted py-8 text-center">
                            {filtered ? t('webhooks.noDeliveriesFiltered') : t('webhooks.noDeliveries')}
                        </p>
                    ) : (
                        <div className="overflow-x-auto rounded-lg border border-border/50">
                            <table className="w-full min-w-[720px] text-sm">
                                <thead className="bg-bg-hover/40 text-xs text-text-muted">
                                    <tr>
                                        <th className="w-8" aria-hidden="true" />
                                        <th className="text-left font-medium px-2 py-2">{t('webhooks.colTime')}</th>
                                        <th className="text-left font-medium px-2 py-2">{t('webhooks.colEvent')}</th>
                                        <th className="text-left font-medium px-2 py-2">{t('webhooks.colStatus')}</th>
                                        <th className="text-left font-medium px-2 py-2">{t('webhooks.colAttempts')}</th>
                                        <th className="text-left font-medium px-2 py-2">{t('webhooks.colResponse')}</th>
                                        <th className="text-right font-medium px-2 py-2">{t('webhooks.colActions')}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {items.map((d) => {
                                        const isOpen = expanded === d.id
                                        const action = retryAction(d)
                                        const pl = payloads[d.id]
                                        return (
                                            <Fragment key={d.id}>
                                                <tr className="border-t border-border/40 align-top">
                                                    <td className="px-1 py-2">
                                                        <button
                                                            type="button"
                                                            onClick={() => setExpanded(isOpen ? null : d.id)}
                                                            className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover"
                                                            aria-expanded={isOpen}
                                                            aria-label={t('webhooks.details')}
                                                            title={t('webhooks.details')}
                                                        >
                                                            {isOpen
                                                                ? <ChevronDown className="w-4 h-4" aria-hidden="true" />
                                                                : <ChevronRight className="w-4 h-4" aria-hidden="true" />}
                                                        </button>
                                                    </td>
                                                    <td className="px-2 py-2 whitespace-nowrap text-xs">{fmtDateTime(d.created_at)}</td>
                                                    <td className="px-2 py-2">
                                                        <div>{eventLabel(t, d.event)}</div>
                                                        {d.zone && <div className="font-mono text-[11px] text-text-muted break-all">{d.zone}</div>}
                                                    </td>
                                                    <td className="px-2 py-2">
                                                        <WebhookStatusBadge status={d.status} />
                                                        {d.next_attempt_at && (d.status === 'failed' || d.status === 'queued') && (
                                                            <div className="text-[11px] text-text-muted mt-1 whitespace-nowrap">
                                                                {t('webhooks.nextAttempt', { time: fmtRelative(d.next_attempt_at) })}
                                                            </div>
                                                        )}
                                                        {d.status === 'succeeded' && d.delivered_at && (
                                                            <div className="text-[11px] text-text-muted mt-1 whitespace-nowrap">
                                                                {t('webhooks.deliveredAt', { time: fmtDateTime(d.delivered_at) })}
                                                            </div>
                                                        )}
                                                    </td>
                                                    <td className="px-2 py-2 whitespace-nowrap text-xs">
                                                        {t('webhooks.attemptsOf', { attempts: d.attempts, max: d.max_attempts })}
                                                    </td>
                                                    <td className="px-2 py-2 text-xs break-words max-w-[14rem]">{responseSummary(t, d)}</td>
                                                    <td className="px-2 py-2 text-right">
                                                        {action && (
                                                            <button
                                                                type="button"
                                                                onClick={() => handleRetry(d)}
                                                                disabled={busyRetry !== null}
                                                                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-40 whitespace-nowrap"
                                                            >
                                                                {busyRetry === d.id
                                                                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                                                                    : <RotateCcw className="w-3.5 h-3.5" aria-hidden="true" />}
                                                                {t(action.kind === 'retryNow' ? 'webhooks.retryNow' : 'webhooks.retry')}
                                                            </button>
                                                        )}
                                                    </td>
                                                </tr>
                                                {isOpen && (
                                                    <tr className="bg-bg-hover/20">
                                                        <td />
                                                        <td colSpan={6} className="px-2 pb-4 pt-1">
                                                            <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[auto,1fr] text-xs">
                                                                <dt className="text-text-muted">{t('webhooks.deliveryId')}</dt>
                                                                <dd className="flex items-center gap-2 min-w-0">
                                                                    <code className="font-mono break-all">{d.delivery_id}</code>
                                                                    <button
                                                                        type="button"
                                                                        onClick={() => copyText(d.id, d.delivery_id)}
                                                                        className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover shrink-0"
                                                                        aria-label={t('common.copy')}
                                                                        title={copiedId === d.id ? t('common.copied') : t('common.copy')}
                                                                    >
                                                                        {copiedId === d.id
                                                                            ? <Check className="w-3.5 h-3.5 text-success" aria-hidden="true" />
                                                                            : <Copy className="w-3.5 h-3.5" aria-hidden="true" />}
                                                                    </button>
                                                                </dd>
                                                                <dt className="text-text-muted">{t('webhooks.eventId')}</dt>
                                                                <dd><code className="font-mono break-all">{d.event_id}</code></dd>
                                                                <dt className="text-text-muted">{t('webhooks.createdAt')}</dt>
                                                                <dd>{fmtDateTime(d.created_at)}</dd>
                                                                <dt className="text-text-muted">{t('webhooks.lastAttemptAt')}</dt>
                                                                <dd>{fmtDateTime(d.last_attempt_at)}</dd>
                                                                {d.next_attempt_at && (
                                                                    <>
                                                                        <dt className="text-text-muted">{t('webhooks.nextAttemptAt')}</dt>
                                                                        <dd>{fmtDateTime(d.next_attempt_at)}</dd>
                                                                    </>
                                                                )}
                                                                {d.delivered_at && (
                                                                    <>
                                                                        <dt className="text-text-muted">{t('webhooks.deliveredAtLabel')}</dt>
                                                                        <dd>{fmtDateTime(d.delivered_at)}</dd>
                                                                    </>
                                                                )}
                                                                {(d.last_error || d.last_error_code) && (
                                                                    <>
                                                                        <dt className="text-text-muted">{t('webhooks.lastError')}</dt>
                                                                        <dd className="break-words">
                                                                            {errorLabel(t, d)}
                                                                            {d.last_error && d.last_error_code && (
                                                                                <span className="block text-text-muted">{d.last_error}</span>
                                                                            )}
                                                                        </dd>
                                                                    </>
                                                                )}
                                                            </dl>
                                                            <div className="mt-3">
                                                                <p className="text-xs text-text-muted mb-1">{t('webhooks.responseExcerpt')}</p>
                                                                {d.last_response_excerpt ? (
                                                                    <pre className="whitespace-pre-wrap break-all max-h-48 overflow-auto p-2 rounded-lg bg-bg-primary border border-border text-[11px]">
                                                                        {d.last_response_excerpt}
                                                                    </pre>
                                                                ) : (
                                                                    <p className="text-xs text-text-muted">{t('webhooks.noResponse')}</p>
                                                                )}
                                                            </div>
                                                            <div className="mt-3 space-y-2">
                                                                <button
                                                                    type="button"
                                                                    onClick={() => togglePayload(d)}
                                                                    disabled={pl?.loading}
                                                                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-40"
                                                                >
                                                                    {pl?.loading && <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />}
                                                                    {pl?.data && pl.shown ? t('webhooks.hidePayload') : t('webhooks.showPayload')}
                                                                </button>
                                                                {pl?.error && <p className="text-xs text-danger break-words">{pl.error}</p>}
                                                                {pl?.data && pl.shown && (
                                                                    <>
                                                                        <pre className="whitespace-pre-wrap break-all max-h-96 overflow-auto p-2 rounded-lg bg-bg-primary border border-border text-[11px]">
                                                                            {prettyJson(pl.data.body)}
                                                                        </pre>
                                                                        <p className="text-xs text-text-muted">{t('webhooks.requestHeaders')}</p>
                                                                        <pre className="whitespace-pre-wrap break-all max-h-48 overflow-auto p-2 rounded-lg bg-bg-primary border border-border text-[11px]">
                                                                            {Object.entries(pl.data.request_headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n')}
                                                                        </pre>
                                                                    </>
                                                                )}
                                                            </div>
                                                        </td>
                                                    </tr>
                                                )}
                                            </Fragment>
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}

                    {total > PAGE_SIZE && (
                        <Pagination
                            offset={offset}
                            limit={PAGE_SIZE}
                            total={total}
                            onPage={(o) => { setOffset(o); setExpanded(null) }}
                        />
                    )}
                </div>
            </aside>
        </div>
    )
}
