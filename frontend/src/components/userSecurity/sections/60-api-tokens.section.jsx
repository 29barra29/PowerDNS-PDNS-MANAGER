import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Trash2 } from 'lucide-react'
import api from '../../../api'
import { useDateFormat } from '../../../lib/useDateFormat'
import { tokenView } from '../../../lib/panelTokens'

// Abschnitt F "API-Tokens" (F14 §2.7) im Dialog "Passwort & Sicherheit": Panel-API-Tokens des Benutzers ansehen
// und widerrufen (einzeln oder alle), nie bearbeiten. Gilt fuer alle Konten (auch externe).
// Vertrag { user, ctx } laut UserSecurityModal.jsx; nach jeder Aktion ctx.refreshSummary() (Zaehler des
// Widerruf-Blocks) und ctx.changed() (Benutzerliste laedt panel_token_count neu).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const section = { id: 'api-tokens', order: 60, titleKey: 'users.apiTokensSection' }

const DANGER_BTN = 'inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium bg-danger/15 text-danger border border-danger/40 hover:bg-danger/25 disabled:opacity-40 disabled:cursor-not-allowed'
const STATUS_KEY = { active: 'panelTokens.statusActive', paused: 'panelTokens.statusPaused', expired: 'panelTokens.statusExpired' }
const STATUS_TONE = { active: 'text-success', paused: 'text-text-muted', expired: 'text-danger' }

export default function ApiTokensSection({ user, ctx }) {
    const { t } = useTranslation()
    const { fmtDate, fmtDateTime } = useDateFormat()
    const [tokens, setTokens] = useState(null) // null = laedt
    const [loadError, setLoadError] = useState('')

    const load = useCallback((signal) => api.getUserPanelTokens(user.id, { signal })
        .then((res) => {
            setTokens(Array.isArray(res?.tokens) ? res.tokens : [])
            setLoadError('')
        })
        .catch((err) => {
            if (err?.name !== 'AbortError') setLoadError(err.message)
        }), [user.id])

    // Laden beim Oeffnen und nach Aenderungen am Benutzer (z. B. "Alle Zugaenge widerrufen" setzt ctx.setUser)
    useEffect(() => {
        const ctrl = new AbortController()
        queueMicrotask(() => load(ctrl.signal))
        return () => ctrl.abort()
    }, [load, user])

    function afterChange(remaining) {
        ctx.setUser({ panel_token_count: remaining })
        ctx.refreshSummary()
        ctx.changed()
    }

    async function revokeOne(tok) {
        if (ctx.busy) return
        if (!window.confirm(t('users.apiTokenRevokeConfirm', { name: tok.name, user: ctx.userName }))) return
        const res = await ctx.run(`token-${tok.id}`, () => api.revokeUserPanelToken(user.id, tok.id))
        if (!res) {
            load()
            return
        }
        const left = (tokens || []).filter((x) => x.id !== tok.id)
        setTokens(left)
        ctx.setInfo(t('users.apiTokensRevoked', { count: 1 }))
        afterChange(left.filter((x) => tokenView(x).status === 'active').length)
    }

    async function revokeAll() {
        const count = tokens?.length || 0
        if (ctx.busy || !count) return
        if (!window.confirm(t('users.apiTokensRevokeAllConfirm', { count, user: ctx.userName }))) return
        const res = await ctx.run('tokens-all', () => api.revokeAllUserPanelTokens(user.id))
        if (!res) {
            load()
            return
        }
        setTokens([])
        ctx.setInfo(t('users.apiTokensRevoked', { count: res.revoked ?? 0 }))
        afterChange(0)
    }

    function zonesSummary(tok) {
        if (!Array.isArray(tok.scope_zones)) {
            return user.role === 'admin' ? t('panelTokens.zonesAll') : t('panelTokens.zonesAllMine')
        }
        return t('panelTokens.zoneCount', { count: tok.scope_zones.length })
    }

    return (
        <div className="space-y-3">
            <p className="text-xs text-text-muted">{t('users.apiTokensHint')}</p>
            {loadError && <p className="text-xs text-danger">{loadError}</p>}
            {tokens === null && !loadError && (
                <p className="text-xs text-text-muted flex items-center gap-2">
                    <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" /> {t('pageSpinner.loading')}
                </p>
            )}
            {tokens && tokens.length === 0 && <p className="text-sm text-text-muted">{t('users.apiTokensEmpty')}</p>}
            {tokens && tokens.length > 0 && (
                <ul className="space-y-2">
                    {tokens.map((tok) => {
                        const view = tokenView(tok)
                        const busy = ctx.busy === `token-${tok.id}`
                        return (
                            <li key={tok.id} className="rounded-lg border border-border/60 p-2.5 text-xs space-y-1">
                                <div className="flex items-start justify-between gap-2">
                                    <div className="min-w-0">
                                        <div className="text-sm font-semibold text-text-primary break-words">{tok.name}</div>
                                        <div className="font-mono text-[11px] text-text-muted">{tok.token_prefix}</div>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => revokeOne(tok)}
                                        disabled={!!ctx.busy}
                                        className="p-1.5 rounded-lg text-danger hover:bg-danger/10 disabled:opacity-40"
                                        title={t('panelTokens.revoke')}
                                        aria-label={`${t('panelTokens.revoke')}: ${tok.name}`}
                                    >
                                        {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Trash2 className="w-4 h-4" aria-hidden="true" />}
                                    </button>
                                </div>
                                <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-text-secondary">
                                    <span className={STATUS_TONE[view.status]}>{t(STATUS_KEY[view.status])}</span>
                                    <span>{tok.permission === 'read' ? t('panelTokens.permRead') : t('panelTokens.permManage')}</span>
                                    <span title={Array.isArray(tok.scope_zones) ? tok.scope_zones.join(', ') : undefined}>{zonesSummary(tok)}</span>
                                    {view.admin === 'active' && <span>{t('panelTokens.adminBadge')}</span>}
                                    <span>
                                        {tok.expires_at ? t('panelTokens.expiresOn', { date: fmtDate(tok.expires_at) }) : t('panelTokens.expiryNever')}
                                    </span>
                                </div>
                                <div className="text-text-muted">
                                    {t('panelTokens.colLastUsed')}:{' '}
                                    {!tok.last_used_at
                                        ? t('panelTokens.lastUsedNever')
                                        : tok.last_used_ip
                                            ? t('panelTokens.lastUsedFrom', { date: fmtDateTime(tok.last_used_at), ip: tok.last_used_ip })
                                            : fmtDateTime(tok.last_used_at)}
                                </div>
                            </li>
                        )
                    })}
                </ul>
            )}
            {tokens && tokens.length > 0 && (
                <button type="button" onClick={revokeAll} disabled={!!ctx.busy} className={DANGER_BTN}>
                    {ctx.busy === 'tokens-all'
                        ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                        : <Trash2 className="w-4 h-4" aria-hidden="true" />}
                    {t('users.apiTokensRevokeAll')}
                </button>
            )}
        </div>
    )
}
