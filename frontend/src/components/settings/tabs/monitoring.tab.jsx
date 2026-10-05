import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
    Activity, AlertCircle, AlertTriangle, BarChart3, Check, CheckCircle2, Copy, Info, Key, Loader2, Radar, RefreshCw,
    Trash2, XCircle,
} from 'lucide-react'
import api from '../../../api'
import OneTimeSecretModal from '../../OneTimeSecretModal'
import { useDateFormat } from '../../../lib/useDateFormat'
import {
    DEFAULT_RESOLVERS, GRAFANA_QUERIES, backgroundTasks, buildCurlCommand, buildScrapeYaml, monitoringStatusRows,
    parseResolverText, resolverText, scrapeTarget,
} from '../../../zoneDetail/propagationModel.js'

// Tab "Monitoring" (nur Admins; F12 §2.2, F13 §2.3, Plan [S14]; Workstream WS-F12F13-FE):
// - Systemstatus aus GET /settings/monitoring/status (Hintergrund-Aufgaben, Migrationen, Server, Verschluesselung)
// - Propagations-Check: externe DNS-Abfragen, Resolver-Liste
// - Prometheus-Metriken: Schalter, Scrape-Token (Einmal-Anzeige ueber OneTimeSecretModal), Beispiele
// Jede Karte hat eigenen Zustand und eigene Banner; geladen wird bei jedem Aktivieren des Tabs.
// Das Backend verlangt eine Admin-Browser-Session (Panel-Tokens sind gesperrt).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'monitoring', order: 85, labelKey: 'settings.monitoring.tab', icon: Activity, adminOnly: true }

export default function MonitoringTab({ active }) {
    return (
        <div className="space-y-6">
            <StatusCard active={active} />
            <PropagationCard active={active} />
            <MetricsCard active={active} />
        </div>
    )
}

// ---------------------------------------------------------------------------------------------------------
// Gemeinsame Bausteine

function CardHeader({ icon: Icon, title, subtitle, children }) {
    return (
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 mb-5">
            <div className="flex items-center gap-3 min-w-0">
                <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center shrink-0">
                    <Icon className="w-5 h-5 text-accent-light" aria-hidden="true" />
                </div>
                <div className="min-w-0">
                    <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
                    {subtitle && <p className="text-sm text-text-muted">{subtitle}</p>}
                </div>
            </div>
            {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
        </div>
    )
}

function Banner({ tone, children, onClose, closeLabel }) {
    const cls = tone === 'success'
        ? 'bg-success/10 border-success/30 text-success'
        : tone === 'warning'
            ? 'bg-warning/10 border-warning/30 text-warning'
            : 'bg-danger/10 border-danger/30 text-danger'
    const Icon = tone === 'success' ? CheckCircle2 : tone === 'warning' ? AlertTriangle : AlertCircle
    return (
        <div className={`p-3 rounded-xl border flex items-start gap-3 text-sm ${cls}`} role={tone === 'error' ? 'alert' : 'status'}>
            <Icon className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            <div className="flex-1 break-words">{children}</div>
            {onClose && <button type="button" onClick={onClose} className="text-xs hover:underline" aria-label={closeLabel}>×</button>}
        </div>
    )
}

// Erfolgsmeldung, die nach 4 s verschwindet
function useFlash() {
    const [msg, setMsg] = useState('')
    const timer = useRef(null)
    useEffect(() => () => clearTimeout(timer.current), [])
    function flash(text) {
        clearTimeout(timer.current)
        setMsg(text || '')
        if (text) timer.current = setTimeout(() => setMsg(''), 4000)
    }
    return [msg, flash]
}

// Laden bei jedem Aktivieren des Tabs (asynchron, kein synchrones setState im Effekt)
function useLoadOnActive(active, load) {
    const loadRef = useRef(load)
    useEffect(() => { loadRef.current = load })
    useEffect(() => {
        if (!active) return undefined
        const id = setTimeout(() => { loadRef.current() }, 0)
        return () => clearTimeout(id)
    }, [active])
}

function CodeBlock({ code, label }) {
    const { t } = useTranslation()
    const [state, setState] = useState('idle') // idle | copied | failed
    const timer = useRef(null)
    useEffect(() => () => clearTimeout(timer.current), [])
    async function copy() {
        clearTimeout(timer.current)
        try {
            if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
            await navigator.clipboard.writeText(code)
            setState('copied')
            timer.current = setTimeout(() => setState('idle'), 3000)
        } catch {
            setState('failed')
        }
    }
    return (
        <div className="space-y-1">
            <div className="relative">
                <pre
                    className="text-xs font-mono bg-bg-primary/80 border border-border rounded-lg p-3 pr-12 overflow-x-auto whitespace-pre text-text-primary select-all"
                    aria-label={label}
                >{code}</pre>
                <button
                    type="button"
                    onClick={copy}
                    className="absolute top-2 right-2 p-1.5 rounded-md bg-bg-secondary/80 border border-border text-text-muted hover:text-text-primary"
                    title={t('common.copy')}
                    aria-label={t('common.copy')}
                >
                    {state === 'copied' ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
                </button>
            </div>
            <p aria-live="polite" className="text-xs min-h-[1rem]">
                {state === 'copied' && <span className="text-success">{t('common.copied')}</span>}
                {state === 'failed' && <span className="text-danger">{t('settings.monitoring.metricsTokenCopyFailed')}</span>}
            </p>
        </div>
    )
}

const TONE_STYLES = {
    ok: { cls: 'text-success', Icon: CheckCircle2 },
    warn: { cls: 'text-warning', Icon: AlertTriangle },
    error: { cls: 'text-danger', Icon: XCircle },
    info: { cls: 'text-text-secondary', Icon: Info },
}

// ---------------------------------------------------------------------------------------------------------
// Karte "Systemstatus" (GET /settings/monitoring/status, [S14])

function StatusCard({ active }) {
    const { t } = useTranslation()
    const { fmtDateTime } = useDateFormat()
    const [status, setStatus] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')
    const seq = useRef(0)

    async function load() {
        const mySeq = ++seq.current
        setLoading(true)
        setError('')
        try {
            const data = await api.getMonitoringStatus()
            if (mySeq === seq.current) setStatus(data)
        } catch (err) {
            if (mySeq === seq.current) setError(err.message)
        } finally {
            if (mySeq === seq.current) setLoading(false)
        }
    }
    useLoadOnActive(active, load)

    const rows = monitoringStatusRows(status)
    const tasks = backgroundTasks(status)

    return (
        <div className="glass-card p-6">
            <CardHeader icon={Activity} title={t('settings.monitoring.statusTitle')} subtitle={t('settings.monitoring.statusSubtitle')}>
                {status && (
                    <span className={`text-xs px-2 py-0.5 rounded-full border ${status.ok
                        ? 'bg-success/10 text-success border-success/30'
                        : 'bg-warning/10 text-warning border-warning/30'}`}
                    >
                        {status.ok ? t('settings.monitoring.statusOverallOk') : t('settings.monitoring.statusOverallProblem')}
                    </span>
                )}
                <button
                    type="button"
                    onClick={load}
                    disabled={loading}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-bg-secondary border border-border text-text-secondary hover:text-text-primary disabled:opacity-50"
                >
                    <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
                    {t('settings.monitoring.statusRefresh')}
                </button>
            </CardHeader>

            {error && (
                <div className="mb-4">
                    <Banner tone="error" onClose={() => setError('')} closeLabel={t('common.close')}>
                        <p>{t('settings.monitoring.loadFailed')} {error}</p>
                        <button type="button" onClick={load} className="mt-1 text-xs underline">{t('common.retry')}</button>
                    </Banner>
                </div>
            )}

            {!status && loading && (
                <div className="flex items-center gap-2 text-sm text-text-muted"><Loader2 className="w-4 h-4 animate-spin" />{t('common.loading')}</div>
            )}

            {status && (
                <div className="space-y-4">
                    <ul className="space-y-2">
                        {rows.map((row) => {
                            const { cls, Icon } = TONE_STYLES[row.tone] || TONE_STYLES.info
                            return (
                                <li key={row.id} className="flex items-start gap-2 text-sm">
                                    <Icon className={`w-4 h-4 shrink-0 mt-0.5 ${cls}`} aria-hidden="true" />
                                    <div className="min-w-0">
                                        <p className="text-text-primary break-words">{t(row.key, row.params)}</p>
                                        {Array.isArray(row.detail) && row.detail.map((d, i) => (
                                            <p key={i} className="text-xs text-text-muted font-mono break-all">
                                                {d.key ? t(d.key, d.params) : d.text}
                                            </p>
                                        ))}
                                    </div>
                                </li>
                            )
                        })}
                    </ul>

                    {tasks.length > 0 && (
                        <div>
                            <p className="text-xs font-medium text-text-secondary mb-1.5">{t('settings.monitoring.statusTasksTitle')}</p>
                            <div className="overflow-x-auto rounded-lg border border-border/60">
                                <table className="w-full text-xs min-w-[480px]">
                                    <tbody>
                                        {tasks.map((task) => (
                                            <tr key={task.name} className="border-b border-border/30 last:border-0">
                                                <td className="p-2 font-mono text-text-primary">{task.name}</td>
                                                <td className="p-2">
                                                    <span className={task.running ? 'text-success' : 'text-danger'}>
                                                        {task.running ? t('settings.monitoring.statusTaskRunning') : t('settings.monitoring.statusTaskStopped')}
                                                    </span>
                                                </td>
                                                <td className="p-2 text-text-muted">
                                                    {task.last_run_at ? t('settings.monitoring.statusTaskLastRun', { time: fmtDateTime(task.last_run_at) }) : '–'}
                                                </td>
                                                <td className="p-2 text-text-muted">
                                                    {task.last_error_at
                                                        ? <span className="text-danger">{t('settings.monitoring.statusTaskLastError', { time: fmtDateTime(task.last_error_at) })}</span>
                                                        : ''}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    )}

                    <p className="text-xs text-text-muted">
                        {status.version && <span>{t('settings.monitoring.statusVersion', { version: status.version })} · </span>}
                        {t('settings.monitoring.statusCheckedAt', { time: fmtDateTime(status.checked_at) })}
                    </p>
                </div>
            )}
        </div>
    )
}

// ---------------------------------------------------------------------------------------------------------
// Karte "Propagations-Check" (GET/PUT /settings/propagation)

function PropagationCard({ active }) {
    const { t } = useTranslation()
    const [form, setForm] = useState(null) // { enabled, check_authoritative, ipv6, resolversText }
    const [defaults, setDefaults] = useState(DEFAULT_RESOLVERS)
    const [loading, setLoading] = useState(false)
    const [saving, setSaving] = useState(false)
    const [error, setError] = useState('')
    const [success, flashSuccess] = useFlash()

    function apply(data) {
        setForm({
            enabled: !!data.enabled,
            check_authoritative: !!data.check_authoritative,
            ipv6: !!data.ipv6,
            resolversText: resolverText(data.resolvers),
        })
        if (Array.isArray(data.default_resolvers) && data.default_resolvers.length > 0) setDefaults(data.default_resolvers)
    }

    async function load() {
        setLoading(true)
        setError('')
        try {
            apply(await api.getPropagationSettings())
        } catch (err) {
            setError(`${t('settings.monitoring.loadFailed')} ${err.message}`)
        } finally {
            setLoading(false)
        }
    }
    useLoadOnActive(active, load)

    async function save(e) {
        e.preventDefault()
        if (!form) return
        setSaving(true)
        setError('')
        flashSuccess('')
        try {
            const data = await api.updatePropagationSettings({
                enabled: form.enabled,
                check_authoritative: form.check_authoritative,
                ipv6: form.ipv6,
                resolvers: parseResolverText(form.resolversText),
            })
            apply(data)
            flashSuccess(t('settings.monitoring.propSaved'))
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))

    return (
        <div className="glass-card p-6">
            <CardHeader icon={Radar} title={t('settings.monitoring.propTitle')} subtitle={t('settings.monitoring.propSubtitle')} />

            <div className="space-y-3 mb-4 empty:hidden">
                {error && (
                    <Banner tone="error" onClose={() => setError('')} closeLabel={t('common.close')}>
                        <p>{error}</p>
                        {!form && <button type="button" onClick={load} className="mt-1 text-xs underline">{t('common.retry')}</button>}
                    </Banner>
                )}
                {success && <Banner tone="success">{success}</Banner>}
            </div>

            {!form && loading && (
                <div className="flex items-center gap-2 text-sm text-text-muted"><Loader2 className="w-4 h-4 animate-spin" />{t('common.loading')}</div>
            )}

            {form && (
                <form onSubmit={save} className="space-y-5">
                    <div>
                        <label className="flex items-center gap-3 cursor-pointer">
                            <input type="checkbox" checked={form.enabled} onChange={set('enabled')} className="rounded border-border" />
                            <span className="text-sm font-medium text-text-primary">{t('settings.monitoring.propEnabled')}</span>
                        </label>
                        <p className="text-xs text-text-muted ml-7 mt-1">{t('settings.monitoring.propEnabledHint')}</p>
                    </div>

                    <div className={`space-y-4 pl-7 ${form.enabled ? '' : 'opacity-60'}`}>
                        <label className="flex items-center gap-3 cursor-pointer">
                            <input type="checkbox" checked={form.check_authoritative} onChange={set('check_authoritative')} className="rounded border-border" />
                            <span className="text-sm text-text-primary">{t('settings.monitoring.propAuthoritative')}</span>
                        </label>
                        <div>
                            <label className="flex items-center gap-3 cursor-pointer">
                                <input type="checkbox" checked={form.ipv6} onChange={set('ipv6')} className="rounded border-border" />
                                <span className="text-sm text-text-primary">{t('settings.monitoring.propIpv6')}</span>
                            </label>
                            <p className="text-xs text-text-muted ml-7 mt-1">{t('settings.monitoring.propIpv6Hint')}</p>
                        </div>
                        <div>
                            <label htmlFor="monitoring-resolvers" className="block text-sm font-medium text-text-secondary mb-1.5">
                                {t('settings.monitoring.propResolvers')}
                            </label>
                            <textarea
                                id="monitoring-resolvers"
                                value={form.resolversText}
                                onChange={set('resolversText')}
                                rows={5}
                                spellCheck={false}
                                className="w-full md:w-1/2 px-3 py-2 text-sm font-mono"
                            />
                            <div className="flex flex-col sm:flex-row sm:items-center gap-2 mt-1">
                                <p className="text-xs text-text-muted flex-1">{t('settings.monitoring.propResolversHint')}</p>
                                <button
                                    type="button"
                                    onClick={() => setForm((f) => ({ ...f, resolversText: resolverText(defaults) }))}
                                    className="self-start text-xs px-2.5 py-1 rounded-lg border border-border text-text-secondary hover:text-text-primary hover:bg-bg-hover"
                                >
                                    {t('settings.monitoring.propResetDefaults')}
                                </button>
                            </div>
                        </div>
                    </div>

                    <button
                        type="submit"
                        disabled={saving}
                        className="inline-flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50"
                    >
                        {saving && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                        {t('common.save')}
                    </button>
                </form>
            )}
        </div>
    )
}

// ---------------------------------------------------------------------------------------------------------
// Karte "Prometheus-Metriken" (GET/PUT /settings/metrics, POST/DELETE /settings/metrics/token)

function MetricsCard({ active }) {
    const { t } = useTranslation()
    const { fmtDateTime } = useDateFormat()
    const [metrics, setMetrics] = useState(null)
    const [form, setForm] = useState({ enabled: false, pdns_probe: true })
    const [loading, setLoading] = useState(false)
    const [saving, setSaving] = useState(false)
    const [tokenBusy, setTokenBusy] = useState(false)
    const [error, setError] = useState('')
    const [success, flashSuccess] = useFlash()
    const [newToken, setNewToken] = useState(null) // Klartext nur bis zum Schliessen des Modals

    function apply(data) {
        setMetrics(data)
        setForm({ enabled: !!data.enabled, pdns_probe: !!data.pdns_probe })
    }

    async function load() {
        setLoading(true)
        setError('')
        try {
            apply(await api.getMetricsSettings())
        } catch (err) {
            setError(`${t('settings.monitoring.loadFailed')} ${err.message}`)
        } finally {
            setLoading(false)
        }
    }
    useLoadOnActive(active, load)

    async function save(e) {
        e.preventDefault()
        if (!metrics) return
        setSaving(true)
        setError('')
        flashSuccess('')
        try {
            // Bei Env-Override ist nur die PowerDNS-Pruefung aenderbar (enabled -> 409)
            const body = metrics.env_override ? { pdns_probe: form.pdns_probe } : { enabled: form.enabled, pdns_probe: form.pdns_probe }
            apply(await api.updateMetricsSettings(body))
            flashSuccess(t('settings.monitoring.metricsSaved'))
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function createToken() {
        const hasToken = metrics?.token_set || metrics?.token_unreadable
        if (hasToken && !window.confirm(t('settings.monitoring.metricsTokenRotateConfirm'))) return
        setTokenBusy(true)
        setError('')
        flashSuccess('')
        try {
            const res = await api.createMetricsToken()
            setNewToken(res?.token || '')
            try {
                apply(await api.getMetricsSettings())
            } catch (err) {
                setError(`${t('settings.monitoring.loadFailed')} ${err.message}`)
            }
        } catch (err) {
            setError(err.message)
        } finally {
            setTokenBusy(false)
        }
    }

    async function deleteToken() {
        if (!window.confirm(t('settings.monitoring.metricsTokenDeleteConfirm'))) return
        setTokenBusy(true)
        setError('')
        flashSuccess('')
        try {
            await api.deleteMetricsToken()
            flashSuccess(t('settings.monitoring.metricsTokenDeleted'))
            apply(await api.getMetricsSettings())
        } catch (err) {
            setError(err.message)
        } finally {
            setTokenBusy(false)
        }
    }

    const envOverride = !!metrics?.env_override
    const hasToken = !!metrics?.token_set
    const scrape = scrapeTarget(metrics?.scrape_url)

    return (
        <div className="glass-card p-6">
            <CardHeader icon={BarChart3} title={t('settings.monitoring.metricsTitle')} subtitle={t('settings.monitoring.metricsSubtitle')}>
                {metrics && (
                    <>
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${metrics.effective_enabled
                            ? 'bg-success/10 text-success border-success/30'
                            : 'bg-bg-hover text-text-muted border-border'}`}
                        >
                            {metrics.effective_enabled ? t('settings.monitoring.metricsStatusActive') : t('settings.monitoring.metricsStatusInactive')}
                        </span>
                        <code className="text-xs font-mono text-text-muted">{metrics.endpoint_path || '/metrics'}</code>
                    </>
                )}
            </CardHeader>

            <div className="space-y-3 mb-4 empty:hidden">
                {error && (
                    <Banner tone="error" onClose={() => setError('')} closeLabel={t('common.close')}>
                        <p>{error}</p>
                        {!metrics && <button type="button" onClick={load} className="mt-1 text-xs underline">{t('common.retry')}</button>}
                    </Banner>
                )}
                {success && <Banner tone="success">{success}</Banner>}
                {envOverride && <Banner tone="warning">{t('settings.monitoring.metricsEnvOverride')}</Banner>}
                {metrics?.token_unreadable && <Banner tone="warning">{t('settings.monitoring.metricsTokenUnreadable')}</Banner>}
            </div>

            {!metrics && loading && (
                <div className="flex items-center gap-2 text-sm text-text-muted"><Loader2 className="w-4 h-4 animate-spin" />{t('common.loading')}</div>
            )}

            {metrics && (
                <div className="space-y-6">
                    {/* Token */}
                    <div className="space-y-2">
                        <p className="text-sm text-text-primary">
                            {hasToken || metrics.token_unreadable
                                ? t('settings.monitoring.metricsTokenInfo', {
                                    hint: metrics.token_hint || '…',
                                    date: metrics.token_created_at ? fmtDateTime(metrics.token_created_at) : '–',
                                })
                                : t('settings.monitoring.metricsTokenNone')}
                        </p>
                        <div className="flex flex-wrap gap-2">
                            <button
                                type="button"
                                onClick={createToken}
                                disabled={envOverride || tokenBusy}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm bg-accent/20 text-accent-light hover:bg-accent/30 transition-colors disabled:opacity-50"
                            >
                                {tokenBusy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Key className="w-4 h-4" aria-hidden="true" />}
                                {hasToken || metrics.token_unreadable ? t('settings.monitoring.metricsTokenRotate') : t('settings.monitoring.metricsTokenCreate')}
                            </button>
                            {(hasToken || metrics.token_unreadable) && (
                                <button
                                    type="button"
                                    onClick={deleteToken}
                                    disabled={envOverride || tokenBusy}
                                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-danger border border-danger/30 hover:bg-danger/10 transition-colors disabled:opacity-50"
                                >
                                    <Trash2 className="w-4 h-4" aria-hidden="true" />
                                    {t('settings.monitoring.metricsTokenDelete')}
                                </button>
                            )}
                        </div>
                    </div>

                    {/* Schalter */}
                    <form onSubmit={save} className="space-y-4 pt-4 border-t border-border">
                        <div>
                            <label className={`flex items-center gap-3 ${envOverride || !hasToken ? 'cursor-not-allowed' : 'cursor-pointer'}`}>
                                <input
                                    type="checkbox"
                                    checked={envOverride ? !!metrics.effective_enabled : form.enabled}
                                    onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
                                    disabled={envOverride || (!hasToken && !form.enabled)}
                                    className="rounded border-border"
                                />
                                <span className="text-sm text-text-primary">{t('settings.monitoring.metricsEnabled')}</span>
                            </label>
                            {!envOverride && !hasToken && (
                                <p className="text-xs text-text-muted ml-7 mt-1">{t('settings.monitoring.metricsNeedToken')}</p>
                            )}
                        </div>
                        <label className="flex items-center gap-3 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={form.pdns_probe}
                                onChange={(e) => setForm((f) => ({ ...f, pdns_probe: e.target.checked }))}
                                className="rounded border-border"
                            />
                            <span className="text-sm text-text-primary">{t('settings.monitoring.metricsPdnsProbe')}</span>
                        </label>
                        <button
                            type="submit"
                            disabled={saving}
                            className="inline-flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50"
                        >
                            {saving && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                            {t('common.save')}
                        </button>
                    </form>

                    {/* Beispiele */}
                    <div className="space-y-4 pt-4 border-t border-border">
                        <div>
                            <h3 className="text-sm font-semibold text-text-primary">{t('settings.monitoring.metricsScrapeTitle')}</h3>
                            <p className="text-xs text-text-muted mb-2">{t('settings.monitoring.metricsScrapeHint')}</p>
                            {scrape.placeholder && (
                                <p className="text-xs text-warning mb-2">{t('settings.monitoring.metricsNoBaseUrl')}</p>
                            )}
                            <CodeBlock code={buildScrapeYaml(metrics.scrape_url)} label={t('settings.monitoring.metricsScrapeTitle')} />
                        </div>
                        <div>
                            <h3 className="text-sm font-semibold text-text-primary mb-2">{t('settings.monitoring.metricsCurlTitle')}</h3>
                            <CodeBlock code={buildCurlCommand(metrics.scrape_url)} label={t('settings.monitoring.metricsCurlTitle')} />
                        </div>
                        <div>
                            <h3 className="text-sm font-semibold text-text-primary">{t('settings.monitoring.metricsGrafanaTitle')}</h3>
                            <p className="text-xs text-text-muted mb-2">{t('settings.monitoring.metricsGrafanaHint')}</p>
                            <CodeBlock code={GRAFANA_QUERIES} label={t('settings.monitoring.metricsGrafanaTitle')} />
                        </div>
                        <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-sm text-amber-200">
                            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                            <p>{t('settings.monitoring.metricsSecurityHint')}</p>
                        </div>
                    </div>
                </div>
            )}

            {newToken !== null && (
                <OneTimeSecretModal
                    title={t('settings.monitoring.metricsTokenModalTitle')}
                    body={t('settings.monitoring.metricsTokenModalWarning')}
                    secret={newToken}
                    doneLabel={t('settings.monitoring.metricsTokenDone')}
                    onDone={() => setNewToken(null)}
                />
            )}
        </div>
    )
}
