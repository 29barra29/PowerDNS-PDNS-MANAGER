import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
    AlertTriangle, KeyRound, Loader2, Pencil, Plus, Power, ScrollText, Send, Trash2, Webhook,
} from 'lucide-react'
import api from '../../api'
import InfoHint from '../InfoHint'
import { useSettings } from '../settings/settingsContext'
import { useDateFormat } from '../../lib/useDateFormat'
import WebhookFormModal from './WebhookFormModal'
import WebhookDeliveriesDrawer from './WebhookDeliveriesDrawer'
import {
    canAddWebhook, eventChips, filterLabel, testResultInfo, webhookRowState, workerBannerKey,
} from './webhookUi'

const EMPTY_META = {
    available_events: [],
    event_categories: [],
    worker_enabled: true,
    worker_running: true,
    max_attempts: 6,
    retention_days: 30,
    max_webhooks: 20,
}

// Karte "Webhooks" im Tab "API & Sicherheit" (F6 2.1-2.8, 6.3) - fuer alle angemeldeten Benutzer.
// Eigener Lade-/Fehlerzustand; Formular und Zustellprotokoll als Overlays.
// Props: { onSecret({ title, secret }) } - das Einmal-Secret (Anlegen, "Secret erneuern") zeigt der Aufrufer im
// OneTimeSecretModal an (Slot integrations-cards/40-webhooks.card.jsx).
export default function WebhooksCard({ onSecret }) {
    const { t } = useTranslation()
    const { fmtDateTime } = useDateFormat()
    const { isAdmin } = useSettings()
    const [hooks, setHooks] = useState([])
    const [meta, setMeta] = useState(EMPTY_META)
    const [loading, setLoading] = useState(true)
    const [cardError, setCardError] = useState('')
    const [cardSuccess, setCardSuccess] = useState('')
    const [busyId, setBusyId] = useState(null)
    const [testingId, setTestingId] = useState(null)
    const [testResults, setTestResults] = useState({}) // id -> { ok, text }
    const [formState, setFormState] = useState(null) // null | { mode, hook }
    const [drawerHook, setDrawerHook] = useState(null)

    const load = useCallback(async () => {
        setLoading(true)
        try {
            const res = await api.listWebhooks()
            setHooks(Array.isArray(res?.webhooks) ? res.webhooks : [])
            setMeta({ ...EMPTY_META, ...(res || {}) })
            setCardError('')
        } catch (err) {
            setHooks([])
            setCardError(err.message)
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        queueMicrotask(() => load())
    }, [load])

    // Erfolgsmeldung nach 4 s ausblenden
    useEffect(() => {
        if (!cardSuccess) return undefined
        const timer = setTimeout(() => setCardSuccess(''), 4000)
        return () => clearTimeout(timer)
    }, [cardSuccess])

    function showSuccess(text) {
        setCardError('')
        setCardSuccess(text)
    }

    async function runAction(hook, fn) {
        setBusyId(hook.id)
        setCardError('')
        try {
            await fn()
        } catch (err) {
            setCardError(err.message)
        } finally {
            setBusyId(null)
        }
    }

    function handleSaved(res) {
        const mode = formState?.mode
        setFormState(null)
        if (!res) return // Bearbeiten ohne Aenderung
        if (mode === 'create') {
            if (res.secret) onSecret?.({ title: t('webhooks.secretTitle'), secret: res.secret })
            showSuccess(t('webhooks.created'))
        } else {
            showSuccess(t('webhooks.saved'))
        }
        load()
    }

    function toggleActive(hook) {
        if (hook.is_active && !window.confirm(t('webhooks.deactivateConfirm', { name: hook.name }))) return
        runAction(hook, async () => {
            await api.setWebhookActive(hook.id, !hook.is_active)
            showSuccess(t('webhooks.saved'))
            await load()
        })
    }

    function rotateSecret(hook) {
        if (!window.confirm(t('webhooks.rotateConfirm', { name: hook.name }))) return
        runAction(hook, async () => {
            const res = await api.rotateWebhookSecret(hook.id)
            if (res?.new_secret) onSecret?.({ title: t('webhooks.secretRotatedTitle'), secret: res.new_secret })
            await load()
        })
    }

    function deleteHook(hook) {
        if (!window.confirm(t('webhooks.deleteConfirm', { name: hook.name }))) return
        runAction(hook, async () => {
            await api.deleteWebhook(hook.id)
            setTestResults((r) => {
                const next = { ...r }
                delete next[hook.id]
                return next
            })
            showSuccess(t('webhooks.deleted'))
            await load()
        })
    }

    async function sendTest(hook) {
        setTestingId(hook.id)
        setBusyId(hook.id)
        setTestResults((r) => ({ ...r, [hook.id]: null }))
        try {
            const res = await api.testWebhook(hook.id)
            setTestResults((r) => ({ ...r, [hook.id]: testResultInfo(t, res) }))
            await load()
        } catch (err) {
            // 429 (Cooldown) und andere Fehler inline unter der Zeile
            setTestResults((r) => ({ ...r, [hook.id]: { ok: false, text: err.message } }))
        } finally {
            setTestingId(null)
            setBusyId(null)
        }
    }

    function closeDrawer() {
        setDrawerHook(null)
        load()
    }

    const banner = workerBannerKey(meta)
    const addAllowed = canAddWebhook(hooks, meta)
    const iconBtn = 'p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-hover disabled:opacity-40 disabled:pointer-events-none'

    return (
        <div className="glass-card p-6 space-y-4">
            {formState && (
                <WebhookFormModal
                    mode={formState.mode}
                    hook={formState.hook}
                    availableEvents={meta.available_events}
                    eventCategories={meta.event_categories}
                    isAdmin={isAdmin}
                    onClose={() => setFormState(null)}
                    onSaved={handleSaved}
                />
            )}
            {drawerHook && (
                <WebhookDeliveriesDrawer
                    hook={drawerHook}
                    availableEvents={meta.available_events}
                    maxAttempts={meta.max_attempts}
                    retentionDays={meta.retention_days}
                    onClose={closeDrawer}
                />
            )}

            <div className="flex flex-wrap items-start justify-between gap-3">
                <h2 className="text-lg font-bold flex items-center gap-2">
                    <Webhook className="w-5 h-5" aria-hidden="true" />
                    {t('settings.integrations.webhooks')}
                </h2>
                <button
                    type="button"
                    onClick={() => setFormState({ mode: 'create', hook: null })}
                    disabled={!addAllowed || loading || Boolean(cardError && hooks.length === 0)}
                    title={addAllowed ? undefined : t('webhooks.limitReached', { max: meta.max_webhooks })}
                    className="px-3 py-2 rounded-lg bg-accent/20 text-sm flex items-center gap-1 disabled:opacity-50"
                >
                    <Plus className="w-4 h-4" aria-hidden="true" /> {t('webhooks.add')}
                </button>
            </div>
            <p className="text-sm text-text-muted">{t('webhooks.help')}</p>
            <InfoHint title={t('webhooks.infoTitle')}>
                <p>{t('webhooks.infoBody')}</p>
            </InfoHint>
            {!addAllowed && (
                <p className="text-xs text-amber-300">{t('webhooks.limitReached', { max: meta.max_webhooks })}</p>
            )}

            {banner && (
                <div role="status" className="flex items-start gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-200 text-sm">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    <p>{t(banner)}</p>
                </div>
            )}
            {cardError && (
                <div role="alert" className="flex flex-wrap items-center justify-between gap-2 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
                    <span className="break-words min-w-0">{cardError}</span>
                    <button type="button" onClick={() => load()} className="text-xs underline hover:no-underline shrink-0">
                        {t('common.retry')}
                    </button>
                </div>
            )}
            {cardSuccess && (
                <div role="status" className="p-3 rounded-lg bg-success/10 border border-success/30 text-success text-sm">
                    {cardSuccess}
                </div>
            )}

            {loading && hooks.length === 0 ? (
                <div className="flex justify-center py-6">
                    <Loader2 className="w-6 h-6 animate-spin text-text-muted" aria-label={t('common.loading')} />
                </div>
            ) : hooks.length === 0 ? (
                !cardError && (
                    <div className="flex flex-col items-center gap-3 py-6 text-center">
                        <Webhook className="w-12 h-12 opacity-30" aria-hidden="true" />
                        <p className="text-sm text-text-muted">{t('webhooks.empty')}</p>
                        <button
                            type="button"
                            onClick={() => setFormState({ mode: 'create', hook: null })}
                            className="px-3 py-2 rounded-lg bg-accent/20 text-sm flex items-center gap-1"
                        >
                            <Plus className="w-4 h-4" aria-hidden="true" /> {t('webhooks.add')}
                        </button>
                    </div>
                )
            ) : (
                <ul className="space-y-3">
                    {hooks.map((hook) => {
                        const st = webhookRowState(hook)
                        const chips = eventChips(hook.events)
                        const busy = busyId === hook.id
                        const testing = testingId === hook.id
                        const result = testResults[hook.id]
                        return (
                            <li key={hook.id} className="border border-border/50 rounded-lg p-3 space-y-2">
                                <div className="flex flex-wrap items-start justify-between gap-2">
                                    <div className="min-w-0 flex-1 space-y-1">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="font-bold break-all">{hook.name}</span>
                                            <span className={`px-2 py-0.5 rounded-full border text-[11px] ${hook.is_active ? 'bg-success/15 text-success border-success/30' : 'bg-bg-hover text-text-muted border-border'}`}>
                                                {hook.is_active ? t('webhooks.active') : t('webhooks.inactive')}
                                            </span>
                                            <span className="px-2 py-0.5 rounded-full border border-border text-[11px] text-text-secondary">
                                                {hook.scope === 'zones' ? t('webhooks.scopeZonesShort') : t('webhooks.scopeOwnShort')}
                                            </span>
                                        </div>
                                        {st.urlUnreadable ? (
                                            <span className="inline-flex px-2 py-0.5 rounded-full border text-[11px] bg-danger/15 text-danger border-danger/30">
                                                {t('webhooks.urlUnreadableBadge')}
                                            </span>
                                        ) : (
                                            <div className="font-mono text-xs text-text-muted truncate" title={hook.url_display || ''}>
                                                {hook.url_display}
                                            </div>
                                        )}
                                        <div className="flex flex-wrap gap-1">
                                            {chips.shown.map((ev) => (
                                                <span key={ev} className="px-1.5 py-0.5 rounded bg-bg-hover text-[11px] text-text-secondary" title={ev}>
                                                    {filterLabel(t, ev, meta.event_categories)}
                                                </span>
                                            ))}
                                            {chips.rest > 0 && (
                                                <span className="px-1.5 py-0.5 rounded bg-bg-hover text-[11px] text-text-muted">+{chips.rest}</span>
                                            )}
                                        </div>
                                    </div>
                                    <div className="flex flex-wrap items-center gap-0.5">
                                        <button
                                            type="button"
                                            className={iconBtn}
                                            onClick={() => sendTest(hook)}
                                            disabled={busy || st.urlUnreadable}
                                            title={st.urlUnreadable ? t('webhooks.urlUnreadableBadge') : t('webhooks.test')}
                                            aria-label={t('webhooks.test')}
                                        >
                                            {testing
                                                ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                                                : <Send className="w-4 h-4" aria-hidden="true" />}
                                        </button>
                                        <button type="button" className={iconBtn} onClick={() => setDrawerHook(hook)} disabled={busy}
                                            title={t('webhooks.deliveries')} aria-label={t('webhooks.deliveries')}>
                                            <ScrollText className="w-4 h-4" aria-hidden="true" />
                                        </button>
                                        <button type="button" className={iconBtn} onClick={() => setFormState({ mode: 'edit', hook })} disabled={busy}
                                            title={t('webhooks.edit')} aria-label={t('webhooks.edit')}>
                                            <Pencil className="w-4 h-4" aria-hidden="true" />
                                        </button>
                                        <button type="button" className={iconBtn} onClick={() => rotateSecret(hook)} disabled={busy}
                                            title={t('webhooks.rotateSecret')} aria-label={t('webhooks.rotateSecret')}>
                                            <KeyRound className="w-4 h-4" aria-hidden="true" />
                                        </button>
                                        <button type="button" className={iconBtn} onClick={() => toggleActive(hook)} disabled={busy}
                                            title={hook.is_active ? t('webhooks.deactivate') : t('webhooks.activate')}
                                            aria-label={hook.is_active ? t('webhooks.deactivate') : t('webhooks.activate')}>
                                            <Power className={`w-4 h-4 ${hook.is_active ? 'text-success' : ''}`} aria-hidden="true" />
                                        </button>
                                        <button type="button" className={`${iconBtn} hover:text-danger`} onClick={() => deleteHook(hook)} disabled={busy}
                                            title={t('webhooks.delete')} aria-label={t('webhooks.delete')}>
                                            <Trash2 className="w-4 h-4" aria-hidden="true" />
                                        </button>
                                    </div>
                                </div>

                                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
                                    <span>
                                        {hook.last_success_at
                                            ? t('webhooks.lastSuccess', { time: fmtDateTime(hook.last_success_at) })
                                            : t('webhooks.neverDelivered')}
                                    </span>
                                    {st.showLastFailure && (
                                        <span className="text-danger">{t('webhooks.lastFailure', { time: fmtDateTime(hook.last_failure_at) })}</span>
                                    )}
                                    {st.pending > 0 && (
                                        <span className="px-2 py-0.5 rounded-full border border-sky-500/30 bg-sky-500/15 text-sky-300">
                                            {t('webhooks.pendingBadge', { count: st.pending })}
                                        </span>
                                    )}
                                    {st.dead > 0 && (
                                        <span className="px-2 py-0.5 rounded-full border border-danger/30 bg-danger/15 text-danger">
                                            {t('webhooks.deadBadge', { count: st.dead })}
                                        </span>
                                    )}
                                    {st.failing > 0 && (
                                        <span className="px-2 py-0.5 rounded-full border border-amber-500/30 bg-amber-500/15 text-amber-300">
                                            {t('webhooks.failingBadge', { count: st.failing })}
                                        </span>
                                    )}
                                </div>

                                {st.urlUnreadable && (
                                    <div className="flex flex-wrap items-center gap-2 p-2 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs">
                                        <span className="flex-1 min-w-0">{t('webhooks.urlUnreadable')}</span>
                                        <button type="button" onClick={() => setFormState({ mode: 'edit', hook })} disabled={busy}
                                            className="underline hover:no-underline shrink-0">
                                            {t('webhooks.urlReenter')}
                                        </button>
                                    </div>
                                )}
                                {st.secretUnreadable && (
                                    <div className="flex flex-wrap items-center gap-2 p-2 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs">
                                        <span className="flex-1 min-w-0">{t('webhooks.secretUnreadable')}</span>
                                        <button type="button" onClick={() => rotateSecret(hook)} disabled={busy}
                                            className="underline hover:no-underline shrink-0">
                                            {t('webhooks.rotateSecret')}
                                        </button>
                                    </div>
                                )}
                                {result && (
                                    <div
                                        role="status"
                                        className={`flex flex-wrap items-center gap-2 p-2 rounded-lg text-xs border ${result.ok ? 'bg-success/10 border-success/30 text-success' : 'bg-danger/10 border-danger/30 text-danger'}`}
                                    >
                                        <span className="flex-1 min-w-0 break-words">{result.text}</span>
                                        <button type="button" onClick={() => setDrawerHook(hook)} className="underline hover:no-underline shrink-0">
                                            {t('webhooks.openLog')}
                                        </button>
                                    </div>
                                )}
                            </li>
                        )
                    })}
                </ul>
            )}
        </div>
    )
}
