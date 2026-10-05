import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, AlertTriangle, CheckCircle2, ChevronRight, Info, Loader2, Radar, RefreshCw, Settings } from 'lucide-react'
import api from '../../api'
import InfoHint from '../../components/InfoHint'
import { formatDateTime } from '../../lib/datetime.js'
import {
    buildCheckParams, canRunCheck, contentInfo, errorText, formatSerial, groupSources, latencyText, needsRecordType,
    noteText, recordCell, resultNotices, serialTone, sourceLabel, statusBadgeClass, statusLabelKey, summaryInfo,
    visibleNoteCodes,
} from '../propagationModel.js'

// Tab "Propagation" der Zonenansicht (F12 §2.1/§6.5, Plan B.14; Workstream WS-F12F13-FE).
// Fuer alle, die die Zone sehen (Backend prueft Lesezugriff). Der Tab wird bei jedem Oeffnen neu gemountet
// und startet dann automatisch einen Check; Zonenwechsel mountet die ganze Ansicht neu.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'propagation', order: 30, labelKey: 'zoneDetail.tabPropagation', icon: Radar }

const WHY_KEYS = [
    'propagation.whySeparateBackends',
    'propagation.whySharedBackend',
    'propagation.whySoaEdit',
    'propagation.whySecondaries',
    'propagation.whyResolverCache',
    'propagation.whyIpv6',
]

export default function PropagationTab({ ctx }) {
    const { t, i18n } = useTranslation()
    const navigate = useNavigate()
    const { server, zoneId, isAdmin } = ctx
    const lang = i18n.resolvedLanguage || i18n.language || 'en'

    const [result, setResult] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const [stale, setStale] = useState(false)
    const [recName, setRecName] = useState('')
    const [recType, setRecType] = useState('')
    const [compareContent, setCompareContent] = useState(false)
    // Sequenzzaehler + AbortController: veraltete Antworten (Doppelklick, Unmount) werden verworfen
    const seqRef = useRef(0)
    const abortRef = useRef(null)
    const resultRef = useRef(null)

    async function runCheck() {
        const seq = ++seqRef.current
        abortRef.current?.abort()
        const controller = new AbortController()
        abortRef.current = controller
        setLoading(true)
        setError('')
        try {
            const res = await api.checkZonePropagation(server, zoneId, {
                ...buildCheckParams({ recName, recType, compareContent }),
                signal: controller.signal,
            })
            if (seq !== seqRef.current) return
            resultRef.current = res
            setResult(res)
            setStale(false)
        } catch (err) {
            if (seq !== seqRef.current || err?.name === 'AbortError') return
            setError(err?.message || String(err))
            setStale(!!resultRef.current)
        } finally {
            if (seq === seqRef.current) setLoading(false)
        }
    }

    // Laufende Abfrage verwerfen (Unmount/Zonenwechsel): Antwort wird ignoriert, Request abgebrochen
    function cancelPending() {
        seqRef.current++
        abortRef.current?.abort()
    }

    // Automatischer Check einmal je Oeffnen des Tabs (kein synchrones setState im Effekt)
    useEffect(() => {
        const id = setTimeout(() => { runCheck() }, 0)
        return () => {
            clearTimeout(id)
            cancelPending()
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps -- nur bei Zonenwechsel automatisch
    }, [server, zoneId])

    const comparableTypes = Array.isArray(result?.comparable_types) ? result.comparable_types : []
    const typeMissing = needsRecordType(recName, recType)
    const submitEnabled = canRunCheck({ loading, recName, recType })

    function onSubmit(e) {
        e.preventDefault()
        if (submitEnabled) runCheck()
    }

    return (
        <div className="space-y-4">
            <div className="glass-card p-4 sm:p-6 space-y-4">
                <div className="flex items-start gap-3">
                    <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center shrink-0">
                        <Radar className="w-5 h-5 text-accent-light" aria-hidden="true" />
                    </div>
                    <div className="min-w-0">
                        <h2 className="text-lg font-semibold text-text-primary">{t('propagation.title')}</h2>
                        <p className="text-sm text-text-muted">{t('propagation.intro')}</p>
                    </div>
                </div>

                <form onSubmit={onSubmit} className="space-y-3">
                    <div>
                        <label htmlFor="prop-rec-name" className="block text-sm font-medium text-text-secondary mb-1.5">
                            {t('propagation.recordLabel')}
                        </label>
                        <div className="flex flex-col sm:flex-row gap-2">
                            <input
                                id="prop-rec-name"
                                type="text"
                                value={recName}
                                onChange={(e) => setRecName(e.target.value)}
                                placeholder={t('propagation.recordNamePlaceholder')}
                                maxLength={255}
                                autoComplete="off"
                                spellCheck={false}
                                className="w-full sm:w-64 px-3 py-2 text-sm font-mono"
                            />
                            <select
                                value={recType}
                                onChange={(e) => setRecType(e.target.value)}
                                disabled={comparableTypes.length === 0}
                                aria-label={t('propagation.colRecord')}
                                aria-invalid={typeMissing || undefined}
                                className="w-full sm:w-44 px-3 py-2 text-sm disabled:opacity-50"
                            >
                                <option value="">{t('propagation.recordTypeNone')}</option>
                                {comparableTypes.map((type) => <option key={type} value={type}>{type}</option>)}
                            </select>
                        </div>
                        {typeMissing && <p className="text-xs text-warning mt-1">{t('propagation.recordTypeRequired')}</p>}
                    </div>

                    <label className="flex items-start gap-3 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={compareContent}
                            onChange={(e) => setCompareContent(e.target.checked)}
                            className="rounded border-border mt-0.5"
                        />
                        <span>
                            <span className="block text-sm text-text-primary">{t('propagation.compareContent')}</span>
                            <span className="block text-xs text-text-muted">{t('propagation.compareContentHint')}</span>
                        </span>
                    </label>

                    <div className="flex flex-wrap items-center gap-3">
                        <button
                            type="submit"
                            disabled={!submitEnabled}
                            className="inline-flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50"
                        >
                            {loading
                                ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                                : <RefreshCw className="w-4 h-4" aria-hidden="true" />}
                            {result ? t('propagation.rerunCheck') : t('propagation.runCheck')}
                        </button>
                        {loading && (
                            <span className="text-sm text-text-muted" role="status" aria-live="polite">{t('propagation.running')}</span>
                        )}
                    </div>
                </form>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3" role="alert">
                    <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
                    <p className="text-sm flex-1 break-words">{error}</p>
                    <button type="button" onClick={() => setError('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            {result && (
                <div className={stale ? 'opacity-60 space-y-4' : 'space-y-4'} aria-busy={loading || undefined}>
                    {stale && <p className="text-xs font-medium text-text-muted uppercase tracking-wide">{t('propagation.staleResult')}</p>}
                    <ResultHeader result={result} t={t} lang={lang} />
                    <ResultNotices result={result} t={t} isAdmin={isAdmin} onOpenSettings={() => navigate('/settings?tab=monitoring')} />
                    {groupSources(result.sources).map((group) => (
                        <SourceGroup
                            key={group.kind}
                            group={group}
                            showRecord={!!result.record}
                            t={t}
                        />
                    ))}
                </div>
            )}

            <details className="group">
                <summary className="flex items-center gap-2 cursor-pointer text-sm text-text-secondary hover:text-text-primary select-none">
                    <ChevronRight className="w-4 h-4 transition-transform group-open:rotate-90" aria-hidden="true" />
                    {t('propagation.whyTitle')}
                </summary>
                <InfoHint className="mt-2">
                    <ul className="list-disc pl-4 space-y-1">
                        {WHY_KEYS.map((key) => <li key={key}>{t(key)}</li>)}
                    </ul>
                </InfoHint>
            </details>
        </div>
    )
}

function formatTime(value, lang) {
    try {
        return formatDateTime(value, lang, { timeStyle: 'medium' })
    } catch {
        return String(value || '')
    }
}

function ResultHeader({ result, t, lang }) {
    const summary = summaryInfo(result.summary)
    const checkedTime = formatTime(result.checked_at, lang)
    const nameservers = Array.isArray(result.nameservers) ? result.nameservers : []
    return (
        <div className="glass-card p-4 space-y-2">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1">
                <p className="text-sm text-text-primary">
                    {t('propagation.expectedSerial', { serial: result.expected_serial, server: result.server })}
                </p>
                <p className="text-xs text-text-muted">
                    {t('propagation.checkedAt', { time: checkedTime })}
                    {result.cached && <span> · {t('propagation.cachedHint')}</span>}
                </p>
            </div>
            {nameservers.length > 0 && (
                <p className="text-xs text-text-muted break-all">
                    {t('propagation.nameserversLabel', { list: nameservers.map((n) => String(n).replace(/\.$/, '')).join(', ') })}
                </p>
            )}
            {summary && (
                <div className={`flex items-center gap-2 text-sm font-medium ${summary.tone === 'ok' ? 'text-success' : 'text-warning'}`}>
                    {summary.tone === 'ok'
                        ? <CheckCircle2 className="w-4 h-4 shrink-0" aria-hidden="true" />
                        : <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />}
                    <span>{t(summary.key, summary.params)}</span>
                </div>
            )}
        </div>
    )
}

function ResultNotices({ result, t, isAdmin, onOpenSettings }) {
    const notices = resultNotices(result)
    if (notices.length === 0) return null
    return (
        <div className="space-y-2">
            {notices.map((n) => {
                const warn = n.tone === 'warn'
                return (
                    <div
                        key={n.id}
                        className={`p-3 rounded-xl border text-sm flex items-start gap-3 ${warn
                            ? 'bg-warning/10 border-warning/30 text-warning'
                            : 'bg-bg-secondary/60 border-border/60 text-text-secondary'}`}
                    >
                        {warn
                            ? <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                            : <Info className="w-4 h-4 shrink-0 mt-0.5 text-accent-light" aria-hidden="true" />}
                        <div className="flex-1 space-y-2">
                            <p>{t(n.key)}</p>
                            {n.id === 'externalDisabled' && isAdmin && (
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-xs text-text-muted">{t('propagation.externalDisabledAdmin')}</span>
                                    <button
                                        type="button"
                                        onClick={onOpenSettings}
                                        className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs bg-accent/20 text-accent-light hover:bg-accent/30 transition-colors"
                                    >
                                        <Settings className="w-3.5 h-3.5" aria-hidden="true" />
                                        {t('propagation.openSettings')}
                                    </button>
                                </div>
                            )}
                        </div>
                    </div>
                )
            })}
        </div>
    )
}

function SourceGroup({ group, showRecord, t }) {
    return (
        <div className="glass-card overflow-hidden">
            <div className="px-4 py-3 bg-bg-hover/30 border-b border-border">
                <h3 className="text-sm font-semibold text-text-primary">{group.labelKey ? t(group.labelKey) : group.kind}</h3>
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[760px]">
                    <thead>
                        <tr className="border-b border-border/50">
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('propagation.colSource')}</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('propagation.colTarget')}</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('propagation.colSerial')}</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('propagation.colStatus')}</th>
                            {showRecord && <th className="text-left p-3 text-text-muted font-medium text-xs">{t('propagation.colRecord')}</th>}
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('propagation.colLatency')}</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('propagation.colNotes')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {group.rows.map((row, idx) => (
                            <SourceRow key={`${row.source}\n${row.target ?? ''}\n${idx}`} row={row} showRecord={showRecord} t={t} />
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

function SourceRow({ row, showRecord, t }) {
    const tone = serialTone(row)
    const err = errorText(t, row)
    const content = contentInfo(row)
    const notes = visibleNoteCodes(row)
    const latency = latencyText(t, row)
    return (
        <tr className="border-b border-border/30 align-top">
            <td className="p-3 text-xs text-text-primary">
                <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono break-all">{sourceLabel(row)}</span>
                    {row.is_reference && (
                        <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-accent/20 text-accent-light uppercase">
                            {t('propagation.referenceBadge')}
                        </span>
                    )}
                </div>
                {row.zone_kind && <div className="text-[11px] text-text-muted mt-0.5">{row.zone_kind}</div>}
            </td>
            <td className="p-3 font-mono text-xs text-text-secondary break-all">{row.target || '–'}</td>
            <td className={`p-3 font-mono text-xs whitespace-nowrap ${tone === 'warn' ? 'text-warning' : tone === 'muted' ? 'text-text-muted' : 'text-text-primary'}`}>
                {formatSerial(row, t)}
            </td>
            <td className="p-3">
                <span className={statusBadgeClass(row.status)}>{t(statusLabelKey(row.status))}</span>
            </td>
            {showRecord && <td className="p-3"><RecordValues row={row} t={t} /></td>}
            <td className="p-3 text-xs text-text-muted whitespace-nowrap">{latency || '–'}</td>
            <td className="p-3 text-xs text-text-secondary space-y-1 min-w-[200px]">
                {err && <div className={row.status === 'skipped' ? 'text-text-muted' : 'text-danger'}>{err}</div>}
                {content && (
                    <div
                        className={content.ok ? 'text-success' : 'text-warning'}
                        title={content.sample.length > 0 ? content.sample.join('\n') : undefined}
                    >
                        {t(content.key, content.params)}
                        {!content.ok && content.sample.length > 0 && (
                            <ul className="mt-1 font-mono text-[11px] text-text-muted break-all">
                                {content.sample.map((s) => <li key={s}>{s}</li>)}
                            </ul>
                        )}
                    </div>
                )}
                {notes.map((code) => <div key={code}>{noteText(t, code, row)}</div>)}
                {!err && !content && notes.length === 0 && <span className="text-text-muted">–</span>}
            </td>
        </tr>
    )
}

function RecordValues({ row, t }) {
    const cell = recordCell(row)
    if (cell.kind === 'none') return <span className="text-xs text-text-muted">–</span>
    const badge = cell.match === true
        ? <span className={statusBadgeClass('ok')}>{t('propagation.recordMatch')}</span>
        : cell.match === false
            ? <span className={statusBadgeClass('mismatch')}>{t('propagation.recordDiffers')}</span>
            : null
    return (
        <div className="space-y-1">
            {cell.kind === 'empty'
                ? <div className="text-xs text-text-muted italic">{t('propagation.recordEmpty')}</div>
                : (
                    <ul className="font-mono text-xs text-text-secondary break-all space-y-0.5">
                        {cell.values.map((v, i) => <li key={`${i}:${v}`}>{v}</li>)}
                    </ul>
                )}
            {badge}
        </div>
    )
}
