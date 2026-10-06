import { useCallback, useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
    AlertTriangle, ChevronDown, ChevronRight, Loader2, Pencil, Plus, Power, RefreshCw, Router, ShieldAlert, Trash2,
} from 'lucide-react'
import api from '../../../api'
import { useDateFormat } from '../../../lib/useDateFormat.js'
import OneTimeSecretModal from '../../OneTimeSecretModal'
import DyndnsGuide from '../../dyndns/DyndnsGuide'
import DyndnsTokenModal from '../../dyndns/DyndnsTokenModal'
import { canToggleActive, dyndnsActionError, isSecretRevoked } from '../../dyndns/dyndnsRevoked'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'dyndns', order: 50 }

// Karte "DynDNS" (F9 §2.1/§2.2/§2.4) im Tab "API & Sicherheit" fuer alle angemeldeten Benutzer:
// eigene DynDNS-Tokens (Hostnamen-Scope A/AAAA), Einmal-Anzeige mit Anleitung (OneTimeSecretModal, f76),
// Einrichtungsanleitung; fuer Admins zusaetzlich Schalter (Endpunkt an/aus, private IPs) und "Alle DynDNS-Tokens".
// Fehler der Karte erscheinen in der Karte (loadErr), Fehler des Dialogs im Dialog.
// Gesperrtes Secret (`secret_revoked`, A2): kein "Aktivieren", Hinweis mit "Neues Secret" (nur Besitzer); ein
// trotzdem eintreffender 409 erscheint uebersetzt (components/dyndns/dyndnsRevoked.js).

const STATUS_CLASS = {
    ok: 'bg-success/10 border-success/30 text-success',
    no_zone: 'bg-amber-500/10 border-amber-500/30 text-amber-200',
    forbidden: 'bg-amber-500/10 border-amber-500/30 text-amber-200',
}
const STATUS_KEY = { ok: 'dyndns.statusOk', no_zone: 'dyndns.statusNoZone', forbidden: 'dyndns.statusForbidden' }

const listOf = (data, key) => (Array.isArray(data) ? data : (Array.isArray(data?.[key]) ? data[key] : []))
const stripDot = (s) => String(s || '').replace(/\.$/, '')

// Ergebniscode aus last_result ('good 203.0.113.5' -> 'good')
function resultCode(value) {
    const code = String(value || '').trim().split(/\s+/)[0]
    return code || ''
}

function TokenRow({ token, busy, onEdit, onToggle, onRotate, onDelete }) {
    const { t } = useTranslation()
    const { fmtDateTime } = useDateFormat()
    const statusByHost = new Map((token.hostname_status || []).map((s) => [stripDot(s.hostname), s.status]))
    const code = resultCode(token.last_result)
    const ips = [token.last_ip_v4, token.last_ip_v6].filter(Boolean).join(', ')
    const revoked = isSecretRevoked(token)
    const toggleLabel = token.is_active === false ? t('dyndns.activate') : t('dyndns.deactivate')
    return (
        <li className="border border-border/60 rounded-lg p-3 space-y-2">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                    <p className="text-sm font-medium text-text-primary break-words">
                        {token.name}
                        {token.is_active === false && (
                            <span className="ml-2 align-middle text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-text-muted/20 text-text-muted">
                                {t('dyndns.inactive')}
                            </span>
                        )}
                        {revoked && (
                            <span className="ml-2 align-middle text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-danger/15 text-danger">
                                {t('dyndns.secretRevokedBadge')}
                            </span>
                        )}
                    </p>
                    <p className="text-xs font-mono text-text-muted break-all">{token.token_prefix}…</p>
                </div>
                <div className="flex items-center gap-1">
                    <button type="button" disabled={busy} onClick={onEdit} className="p-1.5 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover disabled:opacity-50" title={t('common.edit')} aria-label={`${t('common.edit')}: ${token.name}`}>
                        <Pencil className="w-4 h-4" aria-hidden="true" />
                    </button>
                    {canToggleActive(token) && (
                        <button type="button" disabled={busy} onClick={onToggle} className="p-1.5 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover disabled:opacity-50" title={toggleLabel} aria-label={`${toggleLabel}: ${token.name}`}>
                            <Power className="w-4 h-4" aria-hidden="true" />
                        </button>
                    )}
                    <button type="button" disabled={busy} onClick={onRotate} className="p-1.5 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover disabled:opacity-50" title={t('dyndns.rotate')} aria-label={`${t('dyndns.rotate')}: ${token.name}`}>
                        <RefreshCw className="w-4 h-4" aria-hidden="true" />
                    </button>
                    <button type="button" disabled={busy} onClick={onDelete} className="p-1.5 rounded text-danger hover:bg-danger/10 disabled:opacity-50" title={t('common.delete')} aria-label={`${t('common.delete')}: ${token.name}`}>
                        <Trash2 className="w-4 h-4" aria-hidden="true" />
                    </button>
                </div>
            </div>
            {revoked && (
                <div role="note" className="flex flex-wrap items-start gap-2 p-2 rounded-lg bg-danger/10 border border-danger/30 text-xs text-text-primary">
                    <ShieldAlert className="w-4 h-4 shrink-0 text-danger" aria-hidden="true" />
                    <p className="flex-1 min-w-[12rem] break-words">{t('dyndns.secretRevokedHint')}</p>
                    <button type="button" disabled={busy} onClick={onRotate} className="px-2 py-1 rounded bg-accent/20 text-accent-light hover:bg-accent/30 disabled:opacity-50 inline-flex items-center gap-1">
                        <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
                        {t('dyndns.secretRevokedRotate')}
                    </button>
                </div>
            )}
            <ul className="flex flex-wrap gap-1.5" aria-label={t('dyndns.colHostnames')}>
                {(token.hostnames || []).map((h) => {
                    const status = statusByHost.get(stripDot(h)) || 'ok'
                    const label = t(STATUS_KEY[status] || 'dyndns.statusOk')
                    return (
                        <li key={h} title={label} className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-mono ${STATUS_CLASS[status] || STATUS_CLASS.ok}`}>
                            {status !== 'ok' && <AlertTriangle className="w-3 h-3" aria-hidden="true" />}
                            <span className="break-all">{stripDot(h)}</span>
                            {status !== 'ok' && <span className="sr-only">({label})</span>}
                        </li>
                    )
                })}
            </ul>
            <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-x-4 gap-y-1 text-xs">
                <div><dt className="text-text-muted">{t('dyndns.colTypes')}</dt><dd className="text-text-primary">{(token.allowed_types || []).join(', ')}</dd></div>
                <div><dt className="text-text-muted">{t('dyndns.colTtl')}</dt><dd className="text-text-primary">{token.ttl}</dd></div>
                <div><dt className="text-text-muted">{t('dyndns.colPtr')}</dt><dd className="text-text-primary">{token.update_ptr ? t('common.yes') : t('common.no')}</dd></div>
                <div>
                    <dt className="text-text-muted">{t('dyndns.colLastUsed')}</dt>
                    <dd className="text-text-primary break-words">
                        {token.last_used_at ? fmtDateTime(token.last_used_at) : t('dyndns.never')}
                        {token.last_used_at && token.last_used_ip && <span className="block font-mono text-text-muted">{token.last_used_ip}</span>}
                    </dd>
                </div>
                <div>
                    <dt className="text-text-muted">{t('dyndns.colLastResult')}</dt>
                    <dd className="text-text-primary">
                        {code
                            ? <span className="font-mono" title={t(`dyndns.result.${code}`, { defaultValue: code })}>{code}<span className="sr-only"> ({t(`dyndns.result.${code}`, { defaultValue: code })})</span></span>
                            : '–'}
                    </dd>
                </div>
                <div><dt className="text-text-muted">{t('dyndns.colLastIps')}</dt><dd className="font-mono text-text-primary break-all">{ips || '–'}</dd></div>
            </dl>
        </li>
    )
}

function AdminBlock({ info, onChanged }) {
    const { t } = useTranslation()
    const { fmtDateTime } = useDateFormat()
    const enabledId = useId()
    const privateId = useId()
    const [form, setForm] = useState(null) // { enabled, allow_private_ips }
    const [saved, setSaved] = useState(null)
    const [busy, setBusy] = useState(false)
    const [msg, setMsg] = useState('')
    const [err, setErr] = useState('')
    const [showAll, setShowAll] = useState(false)
    const [allTokens, setAllTokens] = useState(null)
    const [allErr, setAllErr] = useState('')
    const [rowBusy, setRowBusy] = useState(null)

    useEffect(() => {
        const ctrl = new AbortController()
        api.getDyndnsSettings({ signal: ctrl.signal })
            .then((d) => {
                const v = { enabled: d?.enabled !== false, allow_private_ips: !!d?.allow_private_ips }
                setForm(v)
                setSaved(v)
            })
            .catch((e) => { if (e?.name !== 'AbortError') setErr(e.message) })
        return () => ctrl.abort()
    }, [])

    useEffect(() => {
        if (!msg) return undefined
        const timer = setTimeout(() => setMsg(''), 4000)
        return () => clearTimeout(timer)
    }, [msg])

    const loadAll = useCallback(async () => {
        setAllErr('')
        try {
            setAllTokens(listOf(await api.getAllDyndnsTokens(), 'tokens'))
        } catch (e) {
            setAllErr(e.message)
        }
    }, [])

    async function save(e) {
        e.preventDefault()
        if (busy || !form) return
        setBusy(true)
        setErr('')
        setMsg('')
        try {
            const res = await api.updateDyndnsSettings({ enabled: form.enabled, allow_private_ips: form.allow_private_ips })
            const v = res?.settings
                ? { enabled: res.settings.enabled !== false, allow_private_ips: !!res.settings.allow_private_ips }
                : form
            setForm(v)
            setSaved(v)
            setMsg(t('dyndns.adminSaved'))
            onChanged?.()
        } catch (e2) {
            setErr(e2.message)
        } finally {
            setBusy(false)
        }
    }

    function toggleAll() {
        const next = !showAll
        setShowAll(next)
        if (next) loadAll()
    }

    async function toggleForeign(tok) {
        setRowBusy(tok.id)
        setAllErr('')
        try {
            await api.updateDyndnsToken(tok.id, { is_active: tok.is_active === false })
            await loadAll()
            onChanged?.()
        } catch (e) {
            setAllErr(dyndnsActionError(e, t))
        } finally {
            setRowBusy(null)
        }
    }

    async function deleteForeign(tok) {
        if (!window.confirm(t('dyndns.deleteForeignConfirm', { name: tok.name, owner: tok.owner_username || `#${tok.owner_user_id}` }))) return
        setRowBusy(tok.id)
        setAllErr('')
        try {
            await api.deleteDyndnsToken(tok.id)
            await loadAll()
            onChanged?.()
        } catch (e) {
            setAllErr(e.message)
        } finally {
            setRowBusy(null)
        }
    }

    const dirty = form && saved && (form.enabled !== saved.enabled || form.allow_private_ips !== saved.allow_private_ips)

    return (
        <div className="rounded-xl border border-border/80 bg-bg-secondary/40 p-4 space-y-3">
            <h3 className="text-sm font-semibold text-text-primary">{t('dyndns.adminTitle')}</h3>
            {err && <div role="alert" className="p-2 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs break-words">{err}</div>}
            {msg && <div role="status" className="p-2 rounded-lg bg-success/10 border border-success/30 text-success text-xs">{msg}</div>}
            {info?.trust_proxy_headers === false && (
                <p className="flex items-start gap-2 text-xs text-amber-200">
                    <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                    {t('dyndns.adminProxyHint')}
                </p>
            )}
            {form ? (
                <form onSubmit={save} className="space-y-2">
                    <div>
                        <label htmlFor={enabledId} className="flex items-start gap-2 text-sm text-text-secondary cursor-pointer">
                            <input id={enabledId} type="checkbox" checked={form.enabled} onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))} className="w-4 h-4 rounded mt-0.5" />
                            <span>{t('dyndns.adminEnabled')}</span>
                        </label>
                        <p className="text-xs text-text-muted pl-6">{t('dyndns.adminEnabledHint')}</p>
                    </div>
                    <div>
                        <label htmlFor={privateId} className="flex items-start gap-2 text-sm text-text-secondary cursor-pointer">
                            <input id={privateId} type="checkbox" checked={form.allow_private_ips} onChange={(e) => setForm((f) => ({ ...f, allow_private_ips: e.target.checked }))} className="w-4 h-4 rounded mt-0.5" />
                            <span>{t('dyndns.adminAllowPrivate')}</span>
                        </label>
                        <p className="text-xs text-text-muted pl-6">{t('dyndns.adminAllowPrivateHint')}</p>
                    </div>
                    <div className="flex justify-end">
                        <button type="submit" disabled={busy || !dirty} className="px-3 py-1.5 rounded-lg bg-accent/20 text-accent-light text-sm disabled:opacity-50 flex items-center gap-2 hover:bg-accent/30">
                            {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                            {t('dyndns.adminSave')}
                        </button>
                    </div>
                </form>
            ) : !err && (
                <p className="text-xs text-text-muted flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />{t('common.loading')}</p>
            )}

            <div>
                <button type="button" onClick={toggleAll} aria-expanded={showAll} className="flex items-center gap-1 text-sm text-text-secondary hover:text-text-primary">
                    {showAll ? <ChevronDown className="w-4 h-4" aria-hidden="true" /> : <ChevronRight className="w-4 h-4" aria-hidden="true" />}
                    {t('dyndns.adminAllTokens')}
                </button>
                {showAll && (
                    <div className="mt-2 space-y-2">
                        {allErr && <div role="alert" className="p-2 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs break-words">{allErr}</div>}
                        {allTokens === null && !allErr && (
                            <p className="text-xs text-text-muted flex items-center gap-2"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />{t('common.loading')}</p>
                        )}
                        {allTokens && allTokens.length === 0 && <p className="text-xs text-text-muted">{t('dyndns.adminAllTokensEmpty')}</p>}
                        {allTokens && allTokens.length > 0 && (
                            <div className="overflow-x-auto">
                                <table className="w-full text-xs">
                                    <thead>
                                        <tr className="text-left text-text-muted border-b border-border">
                                            <th className="py-1.5 pr-3 font-medium">{t('dyndns.colOwner')}</th>
                                            <th className="py-1.5 pr-3 font-medium">{t('dyndns.colName')}</th>
                                            <th className="py-1.5 pr-3 font-medium">{t('dyndns.colHostnames')}</th>
                                            <th className="py-1.5 pr-3 font-medium">{t('dyndns.colLastUsed')}</th>
                                            <th className="py-1.5 font-medium text-right">{t('dyndns.colActions')}</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {allTokens.map((tok) => (
                                            <tr key={tok.id} className="border-b border-border/40 align-top">
                                                <td className="py-1.5 pr-3 text-text-primary">{tok.owner_username || `#${tok.owner_user_id}`}</td>
                                                <td className="py-1.5 pr-3 text-text-primary">
                                                    {tok.name}
                                                    <span className="block font-mono text-text-muted">{tok.token_prefix}…</span>
                                                    {tok.is_active === false && <span className="text-[10px] uppercase text-text-muted">{t('dyndns.inactive')}</span>}
                                                    {isSecretRevoked(tok) && (
                                                        <span className="block text-[10px] uppercase text-danger" title={t('dyndns.secretRevokedForeignHint')}>
                                                            {t('dyndns.secretRevokedBadge')}
                                                            <span className="sr-only"> ({t('dyndns.secretRevokedForeignHint')})</span>
                                                        </span>
                                                    )}
                                                </td>
                                                <td className="py-1.5 pr-3 font-mono text-text-primary break-all">{(tok.hostnames || []).map(stripDot).join(', ')}</td>
                                                <td className="py-1.5 pr-3 text-text-primary">{tok.last_used_at ? fmtDateTime(tok.last_used_at) : t('dyndns.never')}</td>
                                                <td className="py-1.5 text-right whitespace-nowrap">
                                                    {canToggleActive(tok) && (
                                                        <button type="button" disabled={rowBusy === tok.id} onClick={() => toggleForeign(tok)} className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover disabled:opacity-50" title={tok.is_active === false ? t('dyndns.activate') : t('dyndns.deactivate')} aria-label={`${tok.is_active === false ? t('dyndns.activate') : t('dyndns.deactivate')}: ${tok.name}`}>
                                                            <Power className="w-4 h-4" aria-hidden="true" />
                                                        </button>
                                                    )}
                                                    <button type="button" disabled={rowBusy === tok.id} onClick={() => deleteForeign(tok)} className="p-1 rounded text-danger hover:bg-danger/10 disabled:opacity-50" title={t('common.delete')} aria-label={`${t('common.delete')}: ${tok.name}`}>
                                                        <Trash2 className="w-4 h-4" aria-hidden="true" />
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    )
}

export default function DyndnsCard() {
    const { t } = useTranslation()
    const { isAdmin } = useSettings()
    const [loaded, setLoaded] = useState(false)
    const [info, setInfo] = useState(null)
    const [zones, setZones] = useState([])
    const [tokens, setTokens] = useState([])
    const [loadErr, setLoadErr] = useState('')
    const [notice, setNotice] = useState('')
    const [busyId, setBusyId] = useState(null)
    const [modal, setModal] = useState(null) // { mode: 'create'|'edit', token }
    const [oneTime, setOneTime] = useState(null) // { secret, hostname, title }

    const refresh = useCallback(async () => {
        const [infoRes, tokRes, zoneRes] = await Promise.allSettled([
            api.getDyndnsInfo(), api.getDyndnsTokens(), api.getDyndnsZones(),
        ])
        const errors = []
        if (infoRes.status === 'fulfilled') setInfo(infoRes.value || null)
        else errors.push(infoRes.reason?.message)
        if (tokRes.status === 'fulfilled') setTokens(listOf(tokRes.value, 'tokens'))
        else errors.push(tokRes.reason?.message)
        if (zoneRes.status === 'fulfilled') setZones(listOf(zoneRes.value, 'zones'))
        else errors.push(zoneRes.reason?.message)
        setLoadErr([...new Set(errors.filter(Boolean))].join(' · '))
        setLoaded(true)
    }, [])

    useEffect(() => {
        queueMicrotask(() => { refresh() })
    }, [refresh])

    useEffect(() => {
        if (!notice) return undefined
        const timer = setTimeout(() => setNotice(''), 4000)
        return () => clearTimeout(timer)
    }, [notice])

    const enabled = info?.enabled !== false
    const maxTokens = info?.max_tokens ?? 50
    const limits = {
        ttlMin: info?.ttl_min ?? 60,
        ttlMax: info?.ttl_max ?? 86400,
        ttlDefault: info?.ttl_default ?? 60,
        maxHostnames: info?.max_hostnames ?? 20,
    }
    const canCreate = loaded && enabled && zones.length > 0 && tokens.length < maxTokens
    const guideHost = stripDot(tokens[0]?.hostnames?.[0]) || undefined

    function showSecret(result, title) {
        const tok = result?.token || {}
        if (result?.plaintext_token) {
            setOneTime({ secret: result.plaintext_token, hostname: stripDot(tok.hostnames?.[0]) || undefined, title })
        }
    }

    async function onSaved(res) {
        setModal(null)
        if (!res) return
        setLoadErr('')
        if (res.kind === 'created') {
            showSecret(res.result, t('dyndns.plaintextTitle'))
            setNotice(t('dyndns.created'))
        } else {
            setNotice(t('dyndns.saved'))
        }
        await refresh()
    }

    async function runRowAction(tok, fn, doneKey) {
        setBusyId(tok.id)
        setLoadErr('')
        try {
            const result = await fn()
            if (doneKey) setNotice(t(doneKey))
            await refresh()
            return result
        } catch (e) {
            setLoadErr(dyndnsActionError(e, t))
            return null
        } finally {
            setBusyId(null)
        }
    }

    function toggleToken(tok) {
        runRowAction(tok, () => api.updateDyndnsToken(tok.id, { is_active: tok.is_active === false }), 'dyndns.saved')
    }

    async function rotateToken(tok) {
        if (!window.confirm(t('dyndns.rotateConfirm', { name: tok.name }))) return
        const result = await runRowAction(tok, () => api.rotateDyndnsToken(tok.id), 'dyndns.rotated')
        if (result) showSecret(result, t('dyndns.plaintextTitle'))
    }

    function deleteToken(tok) {
        if (!window.confirm(t('dyndns.deleteConfirm', { name: tok.name }))) return
        runRowAction(tok, () => api.deleteDyndnsToken(tok.id), 'dyndns.deleted')
    }

    return (
        <div className="glass-card p-6 space-y-4">
            {modal && (
                <DyndnsTokenModal
                    mode={modal.mode}
                    token={modal.token}
                    zones={zones}
                    limits={limits}
                    onClose={() => setModal(null)}
                    onSaved={onSaved}
                />
            )}
            {oneTime && (
                <OneTimeSecretModal
                    title={oneTime.title}
                    body={t('dyndns.plaintextWarning')}
                    secret={oneTime.secret}
                    doneLabel={t('dyndns.plaintextDone')}
                    onDone={() => setOneTime(null)}
                >
                    <div className="max-h-[45vh] overflow-y-auto rounded-lg border border-border/70 p-3">
                        <h3 className="text-sm font-semibold text-text-primary mb-2">{t('dyndns.guideTitle')}</h3>
                        <DyndnsGuide baseUrl={info?.base_url} token={oneTime.secret} hostname={oneTime.hostname} />
                    </div>
                </OneTimeSecretModal>
            )}

            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                    <h2 className="text-lg font-bold flex items-center gap-2"><Router className="w-5 h-5" aria-hidden="true" />{t('dyndns.title')}</h2>
                    <p className="text-sm text-text-muted">{t('dyndns.subtitle')}</p>
                </div>
                <button
                    type="button"
                    disabled={!canCreate}
                    onClick={() => setModal({ mode: 'create', token: null })}
                    title={loaded && tokens.length >= maxTokens ? t('dyndns.maxTokensReached', { max: maxTokens }) : undefined}
                    className="px-3 py-2 rounded-lg bg-accent/20 text-accent-light text-sm flex items-center gap-1 hover:bg-accent/30 disabled:opacity-50"
                >
                    <Plus className="w-4 h-4" aria-hidden="true" /> {t('dyndns.createToken')}
                </button>
            </div>

            {loadErr && <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm break-words">{loadErr}</div>}
            {notice && <div role="status" className="p-3 rounded-lg bg-success/10 border border-success/30 text-success text-sm">{notice}</div>}
            {loaded && !enabled && (
                <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-200 text-sm flex items-start gap-2">
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    {t('dyndns.disabledByAdmin')}
                </div>
            )}
            {loaded && enabled && zones.length === 0 && !loadErr && <p className="text-sm text-text-muted">{t('dyndns.noZones')}</p>}
            {loaded && tokens.length >= maxTokens && <p className="text-xs text-text-muted">{t('dyndns.maxTokensReached', { max: maxTokens })}</p>}

            {isAdmin && <AdminBlock info={info} onChanged={refresh} />}

            <div className="space-y-2">
                <h3 className="text-sm font-semibold text-text-primary">{t('dyndns.tokensTitle')}</h3>
                {!loaded ? (
                    <p className="text-sm text-text-muted flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />{t('common.loading')}</p>
                ) : tokens.length === 0 ? (
                    <div className="flex flex-col items-center gap-2 py-6 text-text-muted">
                        <Router className="w-10 h-10 opacity-40" aria-hidden="true" />
                        <p className="text-sm">{t('dyndns.noTokens')}</p>
                    </div>
                ) : (
                    <ul className="space-y-2">
                        {tokens.map((tok) => (
                            <TokenRow
                                key={tok.id}
                                token={tok}
                                busy={busyId === tok.id}
                                onEdit={() => setModal({ mode: 'edit', token: tok })}
                                onToggle={() => toggleToken(tok)}
                                onRotate={() => rotateToken(tok)}
                                onDelete={() => deleteToken(tok)}
                            />
                        ))}
                    </ul>
                )}
            </div>

            <details className="rounded-lg border border-border/70 p-3 group">
                <summary className="cursor-pointer text-sm font-semibold text-text-primary">{t('dyndns.guideTitle')}</summary>
                <div className="mt-3">
                    <DyndnsGuide baseUrl={info?.base_url} hostname={guideHost} />
                </div>
            </details>
        </div>
    )
}
