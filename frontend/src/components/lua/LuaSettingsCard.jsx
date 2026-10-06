import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Braces, Loader2, RefreshCw } from 'lucide-react'
import api from '../../api'

// Admin-Karte "LUA-Records" im Einstellungs-Tab "DNS-Optionen" (F15 2.1 / 6.5-5): Policy (admin | manage | disabled)
// und die PowerDNS-Konfiguration je Server (GET /lua/server-status?refresh=true, nur Whitelist-Werte).
// Erfolg und Fehler zeigt die Karte selbst an; ein Fehler beim Status laesst die Policy bedienbar.
const POLICIES = [
    { id: 'admin', titleKey: 'lua.settings.policyAdmin', descKey: 'lua.settings.policyAdminDesc' },
    { id: 'manage', titleKey: 'lua.settings.policyManage', descKey: 'lua.settings.policyManageDesc' },
    { id: 'disabled', titleKey: 'lua.settings.policyDisabled', descKey: 'lua.settings.policyDisabledDesc' },
]

function luaValueClass(value) {
    if (value === 'yes' || value === 'shared') return 'text-success'
    if (value === 'no') return 'text-danger'
    return 'text-text-muted'
}

function boolValueClass(value) {
    if (value === true) return 'text-success'
    if (value === false) return 'text-danger'
    return 'text-text-muted'
}

export default function LuaSettingsCard() {
    const { t } = useTranslation()
    const groupName = useId()
    const [policy, setPolicy] = useState('admin')
    const [savedPolicy, setSavedPolicy] = useState(null)
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [status, setStatus] = useState(null)
    const [statusLoading, setStatusLoading] = useState(true)
    const [statusError, setStatusError] = useState('')
    const [msg, setMsg] = useState('')
    const [err, setErr] = useState('')

    // Einstellungen und Server-Status parallel laden (Zustand erst nach der Antwort setzen)
    useEffect(() => {
        const ctrl = new AbortController()
        api.getLuaSettings({ signal: ctrl.signal })
            .then((d) => {
                const p = d?.policy || 'admin'
                setPolicy(p)
                setSavedPolicy(p)
                setLoading(false)
            })
            .catch((e) => {
                if (e?.name === 'AbortError') return
                setErr(e.message)
                setLoading(false)
            })
        api.getLuaServerStatus(true, { signal: ctrl.signal })
            .then((st) => {
                setStatus(st || null)
                setStatusLoading(false)
            })
            .catch((e) => {
                if (e?.name === 'AbortError') return
                setStatusError(e.message)
                setStatusLoading(false)
            })
        return () => ctrl.abort()
    }, [])

    useEffect(() => {
        if (!msg) return undefined
        const timer = setTimeout(() => setMsg(''), 4000)
        return () => clearTimeout(timer)
    }, [msg])

    async function refreshStatus() {
        if (statusLoading) return
        setStatusLoading(true)
        setStatusError('')
        try {
            setStatus(await api.getLuaServerStatus(true))
        } catch (e) {
            setStatusError(e.message)
        } finally {
            setStatusLoading(false)
        }
    }

    async function save(e) {
        e.preventDefault()
        if (saving || policy === savedPolicy) return
        setSaving(true)
        setErr('')
        setMsg('')
        try {
            const res = await api.updateLuaSettings({ policy })
            const value = res?.settings?.policy || policy
            setPolicy(value)
            setSavedPolicy(value)
            setMsg(t('lua.settings.saved'))
        } catch (e2) {
            setErr(e2.message)
        } finally {
            setSaving(false)
        }
    }

    const servers = status?.servers || []
    const yesNo = (v) => (v === true ? t('lua.settings.valueYes') : v === false ? t('lua.settings.valueNo') : t('lua.settings.valueUnknown'))

    return (
        <form onSubmit={save} className="glass-card p-6 space-y-4">
            <h2 className="text-lg font-bold flex items-center gap-2">
                <Braces className="w-5 h-5" aria-hidden="true" />
                {t('lua.settings.title')}
            </h2>
            <p className="text-sm text-text-muted">{t('lua.settings.intro')}</p>
            {err && (
                <div role="alert" className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm break-words">{err}</div>
            )}
            {msg && (
                <div role="status" className="p-3 rounded-lg bg-success/10 border border-success/30 text-success text-sm">{msg}</div>
            )}

            {loading ? (
                <p className="text-sm text-text-muted flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> {t('common.loading')}
                </p>
            ) : (
                <fieldset className="space-y-2">
                    <legend className="sr-only">{t('lua.settings.title')}</legend>
                    {POLICIES.map((p) => (
                        <label
                            key={p.id}
                            className={`flex items-start gap-3 rounded-lg border p-3 cursor-pointer transition-colors ${policy === p.id ? 'border-accent/50 bg-accent/5' : 'border-border hover:bg-bg-hover/40'}`}
                        >
                            <input
                                type="radio"
                                name={groupName}
                                value={p.id}
                                checked={policy === p.id}
                                onChange={() => setPolicy(p.id)}
                                className="mt-1"
                            />
                            <span className="min-w-0">
                                <span className="block text-sm font-medium text-text-primary">{t(p.titleKey)}</span>
                                <span className="block text-xs text-text-muted">{t(p.descKey)}</span>
                            </span>
                        </label>
                    ))}
                </fieldset>
            )}

            <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold text-text-primary">{t('lua.settings.serversTitle')}</h3>
                    <button
                        type="button"
                        onClick={refreshStatus}
                        disabled={statusLoading}
                        className="text-xs px-3 py-1.5 rounded-md border border-border hover:bg-bg-hover inline-flex items-center gap-1.5 disabled:opacity-50"
                    >
                        <RefreshCw className={`w-3.5 h-3.5 ${statusLoading ? 'animate-spin' : ''}`} aria-hidden="true" />
                        {t('lua.settings.refresh')}
                    </button>
                </div>
                {statusLoading && !status ? (
                    <p className="text-sm text-text-muted flex items-center gap-2">
                        <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> {t('lua.statusLoading')}
                    </p>
                ) : statusError ? (
                    <p className="text-sm text-text-muted">{t('lua.statusError', { error: statusError })}</p>
                ) : servers.length === 0 ? (
                    <p className="text-sm text-text-muted">{t('lua.settings.noServers')}</p>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-xs min-w-[560px]">
                            <thead>
                                <tr className="border-b border-border/50 text-text-muted text-left">
                                    <th className="p-2 font-medium">{t('lua.settings.colServer')}</th>
                                    <th className="p-2 font-medium font-mono">{t('lua.settings.colLua')}</th>
                                    <th className="p-2 font-medium">{t('lua.settings.colGeoip')}</th>
                                    <th className="p-2 font-medium font-mono">{t('lua.settings.colEcs')}</th>
                                    <th className="p-2 font-medium">{t('lua.settings.colExecLimit')}</th>
                                    <th className="p-2 font-medium">{t('lua.settings.colNote')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {servers.map((s) => (
                                    <tr key={s.name} className="border-b border-border/30">
                                        <td className="p-2 font-mono text-text-primary">{s.name}</td>
                                        <td className={`p-2 font-mono ${luaValueClass(s.lua_records)}`}>
                                            {s.lua_records || t('lua.settings.valueUnknown')}
                                        </td>
                                        <td className={`p-2 ${boolValueClass(s.geoip_backend)}`}>{yesNo(s.geoip_backend)}</td>
                                        <td className={`p-2 ${boolValueClass(s.edns_subnet_processing)}`}>{yesNo(s.edns_subnet_processing)}</td>
                                        <td className="p-2 text-text-secondary">{s.exec_limit ?? '—'}</td>
                                        <td className="p-2 text-text-muted break-words">{s.error || ''}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
                <p className="text-xs text-text-muted">{t('lua.settings.enableHint')}</p>
            </div>

            <div className="flex justify-end">
                <button
                    type="submit"
                    disabled={loading || saving || policy === savedPolicy}
                    className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                >
                    {saving && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                    {t('common.save')}
                </button>
            </div>
        </form>
    )
}
