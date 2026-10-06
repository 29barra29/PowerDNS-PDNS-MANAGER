import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, CheckCircle2, Key, Loader2, Pause, Pencil, Play, Plus, Shield, Trash2 } from 'lucide-react'
import api from '../../../api'
import InfoHint from '../../InfoHint'
import OneTimeSecretModal from '../../OneTimeSecretModal'
import PanelTokenFormModal from '../../panelTokens/PanelTokenFormModal'
import { useDateFormat } from '../../../lib/useDateFormat'
import { tokenView } from '../../../lib/panelTokens'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'panel-tokens', order: 30 }

const API_PREFIX = '/api/v1'

const STATUS_TONE = {
    active: 'bg-success/10 text-success border-success/30',
    paused: 'bg-bg-hover text-text-muted border-border',
    expired: 'bg-danger/10 text-danger border-danger/30',
}
const STATUS_KEY = { active: 'panelTokens.statusActive', paused: 'panelTokens.statusPaused', expired: 'panelTokens.statusExpired' }

function Badge({ tone, title, children }) {
    return (
        <span title={title} className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border whitespace-nowrap ${tone}`}>
            {children}
        </span>
    )
}

// Karte "API-Token (Panel)" im Tab "API & Sicherheit" (F14 §2.1, §2.5, §2.6, §6.4) – fuer alle angemeldeten
// Benutzer. Liste mit Berechtigung, Zonen, Admin-/Weitreichend-Badges, Ablauf, "zuletzt benutzt", Status;
// Erstellen/Bearbeiten im PanelTokenFormModal, Klartext genau einmal im OneTimeSecretModal (f76).
// Datumswerte ausschliesslich ueber useDateFormat (F8-A12).
export default function PanelTokensCard() {
    const { t } = useTranslation()
    const { fmtDateTime, fmtDate } = useDateFormat()
    const [tokens, setTokens] = useState([])
    const [loading, setLoading] = useState(true)
    const [cardError, setCardError] = useState('')
    const [cardInfo, setCardInfo] = useState('')
    const [me, setMe] = useState(null)
    const [modal, setModal] = useState(null) // null | { mode: 'create' } | { mode: 'edit', token }
    const [oneTime, setOneTime] = useState(null) // null | { name, secret }
    const [busyId, setBusyId] = useState(null)
    const [openZones, setOpenZones] = useState(null) // id des Tokens mit aufgeklappter Zonenliste

    const isAdmin = me?.role === 'admin'

    const load = useCallback(async () => {
        setLoading(true)
        try {
            const [list, meRes] = await Promise.all([api.listPanelTokens(), api.getMe()])
            setTokens(Array.isArray(list?.tokens) ? list.tokens : [])
            setMe(meRes || null)
            setCardError('')
        } catch (err) {
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
        if (!cardInfo) return undefined
        const timer = setTimeout(() => setCardInfo(''), 4000)
        return () => clearTimeout(timer)
    }, [cardInfo])

    function showInfo(text) {
        setCardError('')
        setCardInfo(text)
    }

    function onSaved(result) {
        setModal(null)
        if (result?.mode === 'create') {
            setOneTime({ name: result.token?.name || '', secret: result.plaintext_token })
        } else if (result?.token) {
            showInfo(t('panelTokens.saved', { name: result.token.name }))
        }
        load()
    }

    function closeOneTime() {
        const name = oneTime?.name || ''
        setOneTime(null)
        showInfo(t('panelTokens.created', { name }))
    }

    async function runAction(token, fn, infoKey) {
        setBusyId(token.id)
        setCardError('')
        try {
            await fn()
            if (infoKey) showInfo(t(infoKey, { name: token.name }))
        } catch (err) {
            setCardError(err.message)
        } finally {
            setBusyId(null)
            load()
        }
    }

    function togglePause(token) {
        if (token.is_active) {
            if (!window.confirm(t('panelTokens.pauseConfirm', { name: token.name }))) return
            runAction(token, () => api.updatePanelToken(token.id, { is_active: false }), 'panelTokens.paused')
        } else {
            runAction(token, () => api.updatePanelToken(token.id, { is_active: true }), 'panelTokens.resumed')
        }
    }

    function revoke(token) {
        if (!window.confirm(t('panelTokens.revokeConfirm', { name: token.name }))) return
        runAction(token, () => api.deletePanelToken(token.id), 'panelTokens.revoked')
    }

    const originExample = typeof window !== 'undefined' ? window.location.origin : 'https://panel.example.com'

    // --- Zellen (Tabelle und gestapelte Ansicht nutzen dieselben Bausteine) -------------------------
    function permissionBadge(tok) {
        return tok.permission === 'read'
            ? <Badge tone="bg-bg-hover text-text-secondary border-border">{t('panelTokens.permRead')}</Badge>
            : <Badge tone="bg-accent/15 text-accent-light border-accent/30">{t('panelTokens.permManage')}</Badge>
    }

    function zonesCell(tok, view) {
        if (!Array.isArray(tok.scope_zones)) {
            return <span className="text-sm">{isAdmin ? t('panelTokens.zonesAll') : t('panelTokens.zonesAllMine')}</span>
        }
        const open = openZones === tok.id
        return (
            <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-1">
                    <button
                        type="button"
                        onClick={() => setOpenZones(open ? null : tok.id)}
                        className="text-sm text-accent-light hover:underline"
                        aria-expanded={open}
                        title={open ? t('panelTokens.zonesHide') : t('panelTokens.zonesShow')}
                    >
                        {t('panelTokens.zoneCount', { count: tok.scope_zones.length })}
                    </button>
                    {view.inaccessible.length > 0 && (
                        <Badge
                            tone="bg-warning/10 text-warning border-warning/30"
                            title={t('panelTokens.zonesInaccessible', { zones: view.inaccessible.join(', ') })}
                        >
                            <AlertTriangle className="w-3 h-3" aria-hidden="true" /> {view.inaccessible.length}
                        </Badge>
                    )}
                </div>
                {open && (
                    <ul className="font-mono text-xs text-text-secondary space-y-0.5">
                        {tok.scope_zones.map((z) => (
                            <li key={z} className={view.inaccessible.includes(z) ? 'text-warning' : ''}>{z}</li>
                        ))}
                    </ul>
                )}
            </div>
        )
    }

    function extraBadges(view) {
        return (
            <>
                {view.admin === 'active' && (
                    <Badge tone="bg-purple-500/10 text-purple-300 border-purple-500/30">
                        <Shield className="w-3 h-3" aria-hidden="true" /> {t('panelTokens.adminBadge')}
                    </Badge>
                )}
                {view.admin === 'inactive' && (
                    <Badge tone="bg-bg-hover text-text-muted border-border" title={t('panelTokens.adminInactiveHint')}>
                        {t('panelTokens.adminInactiveBadge')}
                    </Badge>
                )}
                {view.broad && (
                    <Badge tone="bg-amber-500/10 text-amber-300 border-amber-500/30" title={t('panelTokens.broadAccessHint')}>
                        {t('panelTokens.broadAccessBadge')}
                    </Badge>
                )}
            </>
        )
    }

    function expiryCell(tok, view) {
        return (
            <div className="space-y-1 text-sm">
                <div>{tok.expires_at ? t('panelTokens.expiresOn', { date: fmtDate(tok.expires_at) }) : t('panelTokens.expiryNever')}</div>
                {view.expiresSoon !== null && (
                    <Badge tone="bg-amber-500/10 text-amber-300 border-amber-500/30">
                        {t('panelTokens.expiresSoon', { count: view.expiresSoon })}
                    </Badge>
                )}
                {view.status === 'expired' && <p className="text-[11px] text-danger">{t('panelTokens.expiredResumeHint')}</p>}
            </div>
        )
    }

    function lastUsedCell(tok) {
        if (!tok.last_used_at) return <span className="text-sm text-text-muted">{t('panelTokens.lastUsedNever')}</span>
        const date = fmtDateTime(tok.last_used_at)
        return (
            <span className="text-sm">
                {tok.last_used_ip ? t('panelTokens.lastUsedFrom', { date, ip: tok.last_used_ip }) : date}
            </span>
        )
    }

    function statusBadge(view) {
        return <Badge tone={STATUS_TONE[view.status]}>{t(STATUS_KEY[view.status])}</Badge>
    }

    function actions(tok) {
        const busy = busyId === tok.id
        const iconBtn = 'p-1.5 rounded-lg hover:bg-bg-hover disabled:opacity-40'
        return (
            <div className="flex items-center gap-1">
                <button type="button" disabled={busy} onClick={() => setModal({ mode: 'edit', token: tok })}
                    className={`${iconBtn} text-text-secondary`} title={t('panelTokens.edit')} aria-label={`${t('panelTokens.edit')}: ${tok.name}`}>
                    <Pencil className="w-4 h-4" aria-hidden="true" />
                </button>
                <button type="button" disabled={busy} onClick={() => togglePause(tok)}
                    className={`${iconBtn} text-text-secondary`}
                    title={tok.is_active ? t('panelTokens.pause') : t('panelTokens.resume')}
                    aria-label={`${tok.is_active ? t('panelTokens.pause') : t('panelTokens.resume')}: ${tok.name}`}>
                    {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                        : tok.is_active ? <Pause className="w-4 h-4" aria-hidden="true" /> : <Play className="w-4 h-4" aria-hidden="true" />}
                </button>
                <button type="button" disabled={busy} onClick={() => revoke(tok)}
                    className={`${iconBtn} text-danger hover:bg-danger/10`} title={t('panelTokens.revoke')} aria-label={`${t('panelTokens.revoke')}: ${tok.name}`}>
                    <Trash2 className="w-4 h-4" aria-hidden="true" />
                </button>
            </div>
        )
    }

    function nameCell(tok) {
        return (
            <div className="min-w-0">
                <div className="font-semibold text-text-primary break-words">{tok.name}</div>
                <div className="font-mono text-[11px] text-text-muted">{tok.token_prefix}</div>
            </div>
        )
    }

    const createButton = (
        <button
            type="button"
            onClick={() => setModal({ mode: 'create' })}
            disabled={loading && !me}
            className="px-3 py-2 rounded-lg bg-gradient-to-r from-accent to-purple-600 text-white text-sm font-medium inline-flex items-center gap-1.5 disabled:opacity-50"
        >
            <Plus className="w-4 h-4" aria-hidden="true" /> {t('panelTokens.create')}
        </button>
    )

    return (
        <div className="glass-card p-6 space-y-4">
            {modal && (
                <PanelTokenFormModal
                    mode={modal.mode}
                    token={modal.token}
                    isAdmin={isAdmin}
                    zonePermissions={me?.zone_permissions || {}}
                    onClose={() => setModal(null)}
                    onSaved={onSaved}
                />
            )}
            {oneTime && (
                <OneTimeSecretModal
                    title={t('panelTokens.createdTitle')}
                    body={t('panelTokens.createdBody')}
                    secret={oneTime.secret}
                    onDone={closeOneTime}
                >
                    <div>
                        <p className="text-xs text-text-muted mb-1">{t('settings.integrations.apiInfoCodeLabel')}</p>
                        <pre className="p-2 rounded-lg bg-bg-primary border border-border text-[11px] overflow-x-auto whitespace-pre-wrap break-all">
                            {t('settings.integrations.curlExample', { origin: originExample })}
                        </pre>
                    </div>
                </OneTimeSecretModal>
            )}

            <div className="flex flex-wrap items-start justify-between gap-3">
                <h2 className="text-lg font-bold flex items-center gap-2"><Key className="w-5 h-5" aria-hidden="true" />{t('settings.integrations.panelTokens')}</h2>
                {tokens.length > 0 && createButton}
            </div>
            <p className="text-sm text-text-muted">{t('settings.integrations.panelTokensHelp')}</p>
            <InfoHint title={t('settings.integrations.apiInfoTitle')}>
                <p>{t('settings.integrations.apiInfoP1', { prefix: API_PREFIX })}</p>
                <p>{t('settings.integrations.apiInfoP2')}</p>
                <p>{t('settings.integrations.apiInfoP3')}</p>
                <p className="pt-1 font-mono text-[11px] text-text-primary/90">{t('settings.integrations.apiInfoCodeLabel')}</p>
                <pre className="mt-1 p-2 rounded-lg bg-bg-primary border border-border text-[11px] overflow-x-auto whitespace-pre-wrap break-all">
                    {t('settings.integrations.curlExample', { origin: originExample })}
                </pre>
            </InfoHint>
            <p className="text-xs text-text-muted">{t('settings.integrations.apiDocHint')}</p>

            {cardError && (
                <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm flex flex-wrap items-center justify-between gap-2">
                    <span className="break-words">{cardError}</span>
                    <button type="button" onClick={() => load()} className="text-xs underline">{t('common.retry')}</button>
                </div>
            )}
            {cardInfo && (
                <div role="status" className="p-3 rounded-lg bg-success/10 border border-success/30 text-success text-sm flex items-start gap-2">
                    <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    <span className="break-words">{cardInfo}</span>
                </div>
            )}

            {loading && tokens.length === 0 ? (
                <p className="flex items-center gap-2 text-sm text-text-muted">
                    <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> {t('pageSpinner.loading')}
                </p>
            ) : tokens.length === 0 ? (
                <div className="flex flex-col items-center text-center gap-3 py-6">
                    <Key className="w-10 h-10 opacity-30" aria-hidden="true" />
                    <p className="text-sm text-text-muted max-w-md">{t('panelTokens.empty')}</p>
                    {createButton}
                </div>
            ) : (
                <>
                    {/* Desktop: Tabelle */}
                    <div className="hidden lg:block overflow-x-auto">
                        <table className="w-full text-left align-top">
                            <thead>
                                <tr className="text-xs text-text-muted border-b border-border/60">
                                    <th className="py-2 pr-3 font-medium">{t('panelTokens.colName')}</th>
                                    <th className="py-2 pr-3 font-medium">{t('panelTokens.colPermission')}</th>
                                    <th className="py-2 pr-3 font-medium">{t('panelTokens.colZones')}</th>
                                    <th className="py-2 pr-3 font-medium">{t('panelTokens.colExpires')}</th>
                                    <th className="py-2 pr-3 font-medium">{t('panelTokens.colLastUsed')}</th>
                                    <th className="py-2 pr-3 font-medium">{t('panelTokens.colStatus')}</th>
                                    <th className="py-2 font-medium"><span className="sr-only">{t('panelTokens.colActions')}</span></th>
                                </tr>
                            </thead>
                            <tbody>
                                {tokens.map((tok) => {
                                    const view = tokenView(tok)
                                    return (
                                        <tr key={tok.id} className={`border-b border-border/40 align-top ${busyId === tok.id ? 'opacity-60' : ''}`}>
                                            <td className="py-2 pr-3">
                                                {nameCell(tok)}
                                                <div className="flex flex-wrap gap-1 mt-1">{extraBadges(view)}</div>
                                            </td>
                                            <td className="py-2 pr-3">{permissionBadge(tok)}</td>
                                            <td className="py-2 pr-3">{zonesCell(tok, view)}</td>
                                            <td className="py-2 pr-3">{expiryCell(tok, view)}</td>
                                            <td className="py-2 pr-3">{lastUsedCell(tok)}</td>
                                            <td className="py-2 pr-3">{statusBadge(view)}</td>
                                            <td className="py-2">{actions(tok)}</td>
                                        </tr>
                                    )
                                })}
                            </tbody>
                        </table>
                    </div>

                    {/* Schmal: gestapelte Karten */}
                    <ul className="lg:hidden space-y-3">
                        {tokens.map((tok) => {
                            const view = tokenView(tok)
                            return (
                                <li key={tok.id} className={`rounded-xl border border-border/60 p-3 space-y-2 ${busyId === tok.id ? 'opacity-60' : ''}`}>
                                    <div className="flex items-start justify-between gap-2">
                                        {nameCell(tok)}
                                        {actions(tok)}
                                    </div>
                                    <div className="flex flex-wrap gap-1">
                                        {statusBadge(view)}
                                        {permissionBadge(tok)}
                                        {extraBadges(view)}
                                    </div>
                                    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                                        <dt className="text-text-muted">{t('panelTokens.colZones')}</dt>
                                        <dd>{zonesCell(tok, view)}</dd>
                                        <dt className="text-text-muted">{t('panelTokens.colExpires')}</dt>
                                        <dd>{expiryCell(tok, view)}</dd>
                                        <dt className="text-text-muted">{t('panelTokens.colLastUsed')}</dt>
                                        <dd>{lastUsedCell(tok)}</dd>
                                    </dl>
                                </li>
                            )
                        })}
                    </ul>
                </>
            )}
        </div>
    )
}
