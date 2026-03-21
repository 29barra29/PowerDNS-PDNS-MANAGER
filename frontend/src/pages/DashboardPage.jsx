import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Server, Globe, Activity, AlertCircle, Loader2, CheckCircle2 } from 'lucide-react'
import api from '../api'

export default function DashboardPage() {
    const { t } = useTranslation()
    const [servers, setServers] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [user, setUser] = useState(() => api.getUser())

    useEffect(() => {
        const u = api.getUser()
        if (u) setUser(u)
        loadDashboard()
    }, [])

    async function loadDashboard() {
        try {
            const data = await api.getServers()
            setServers(data.servers || [])
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64">
                <Loader2 className="w-8 h-8 text-accent animate-spin" />
            </div>
        )
    }

    const isAdmin = user?.role === 'admin'
    const allOnline = servers.every(s => s.is_reachable)
    const onlineCount = servers.filter(s => s.is_reachable).length
    const totalZones = servers.reduce((acc, s) => acc + (s.zone_count || 0), 0)

    // ========================
    // Benutzer-Dashboard (vereinfacht)
    // ========================
    if (!isAdmin) {
        return (
            <div className="space-y-6">
                <div>
                    <h1 className="text-2xl font-bold text-text-primary">
                        {t('dashboard.welcome', { name: user?.display_name || user?.username })}
                    </h1>
                    <p className="text-text-muted text-sm mt-1">{t('dashboard.subtitle')}</p>
                </div>

                {/* Serverstatus - einfach */}
                <div className="glass-card p-6">
                    <div className="flex items-center gap-4">
                        <div className={`w-14 h-14 rounded-2xl flex items-center justify-center ${allOnline ? 'bg-success/20' : 'bg-warning/20'
                            }`}>
                            {allOnline
                                ? <CheckCircle2 className="w-7 h-7 text-success" />
                                : <AlertCircle className="w-7 h-7 text-warning" />
                            }
                        </div>
                        <div>
                            <h2 className="text-lg font-semibold text-text-primary">
                                {allOnline ? t('dashboard.allRunning') : t('dashboard.limitedOperation')}
                            </h2>
                            <p className="text-sm text-text-muted">
                                {allOnline
                                    ? t('dashboard.allServersOnline')
                                    : t('dashboard.serversReachable', { count: onlineCount, total: servers.length })
                                }
                            </p>
                        </div>
                    </div>
                </div>

                {/* Schnellzugriff */}
                <div>
                    <h2 className="text-lg font-semibold text-text-primary mb-3">{t('dashboard.quickAccess')}</h2>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <a href="/zones" className="glass-card p-5 hover:bg-bg-hover/50 transition-all group cursor-pointer block">
                            <div className="flex items-center gap-3">
                                <div className="w-10 h-10 rounded-lg bg-accent/20 flex items-center justify-center group-hover:bg-accent/30 transition-colors">
                                    <Globe className="w-5 h-5 text-accent-light" />
                                </div>
                                <div>
                                    <p className="font-medium text-text-primary">{t('layout.myZones')}</p>
                                    <p className="text-xs text-text-muted">{t('dashboard.manageRecords')}</p>
                                </div>
                            </div>
                        </a>
                        <a href="/search" className="glass-card p-5 hover:bg-bg-hover/50 transition-all group cursor-pointer block">
                            <div className="flex items-center gap-3">
                                <div className="w-10 h-10 rounded-lg bg-purple-500/20 flex items-center justify-center group-hover:bg-purple-500/30 transition-colors">
                                    <Activity className="w-5 h-5 text-purple-400" />
                                </div>
                                <div>
                                    <p className="font-medium text-text-primary">{t('layout.search')}</p>
                                    <p className="text-xs text-text-muted">{t('dashboard.searchRecords')}</p>
                                </div>
                            </div>
                        </a>
                    </div>
                </div>
            </div>
        )
    }

    // ========================
    // Admin-Dashboard (voll)
    // ========================
    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-text-primary">{t('dashboard.overview')}</h1>
                <p className="text-text-muted text-sm mt-1">{t('dashboard.serverStatus')}</p>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-center gap-3">
                    <AlertCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm">{error}</p>
                </div>
            )}

            {/* Stats cards */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="glass-card p-5">
                    <div className="flex items-center gap-3 mb-3">
                        <div className="w-10 h-10 rounded-lg bg-accent/20 flex items-center justify-center">
                            <Server className="w-5 h-5 text-accent-light" />
                        </div>
                        <div>
                            <p className="text-2xl font-bold text-text-primary">{servers.length}</p>
                            <p className="text-xs text-text-muted">{t('dashboard.servers')}</p>
                        </div>
                    </div>
                </div>

                <div className="glass-card p-5">
                    <div className="flex items-center gap-3 mb-3">
                        <div className="w-10 h-10 rounded-lg bg-success/20 flex items-center justify-center">
                            <Activity className="w-5 h-5 text-success" />
                        </div>
                        <div>
                            <p className="text-2xl font-bold text-text-primary">{onlineCount} / {servers.length}</p>
                            <p className="text-xs text-text-muted">{t('dashboard.online')}</p>
                        </div>
                    </div>
                </div>

                <div className="glass-card p-5">
                    <div className="flex items-center gap-3 mb-3">
                        <div className="w-10 h-10 rounded-lg bg-purple-500/20 flex items-center justify-center">
                            <Globe className="w-5 h-5 text-purple-400" />
                        </div>
                        <div>
                            <p className="text-2xl font-bold text-text-primary">{totalZones}</p>
                            <p className="text-xs text-text-muted">{t('dashboard.zones')}</p>
                        </div>
                    </div>
                </div>
            </div>

            {/* Server cards */}
            <div>
                <h2 className="text-lg font-semibold text-text-primary mb-4">{t('dashboard.servers')}</h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    {servers.map(s => (
                        <div key={s.name} className="glass-card p-5">
                            <div className="flex items-center justify-between mb-4">
                                <div className="flex items-center gap-3">
                                    <div className={`w-2.5 h-2.5 rounded-full ${s.is_reachable ? 'bg-success animate-pulse' : 'bg-danger'}`} />
                                    <h3 className="font-semibold text-text-primary">{s.name}</h3>
                                </div>
                                <span className={`text-xs px-2.5 py-1 rounded-full ${s.is_reachable
                                        ? 'bg-success/10 text-success border border-success/30'
                                        : 'bg-danger/10 text-danger border border-danger/30'
                                    }`}>
                                    {s.is_reachable ? t('dashboard.online') : t('dashboard.offline')}
                                </span>
                            </div>
                            <div className="grid grid-cols-2 gap-3 text-sm">
                                <div>
                                    <p className="text-text-muted text-xs">{t('dashboard.daemonVersion')}</p>
                                    <p className="text-text-primary">{s.version || '-'}</p>
                                </div>
                                <div>
                                    <p className="text-text-muted text-xs">{t('dashboard.zones')}</p>
                                    <p className="text-text-primary">{s.zone_count ?? '-'}</p>
                                </div>
                                <div>
                                    <p className="text-text-muted text-xs">{t('dashboard.type')}</p>
                                    <p className="text-text-primary">{s.daemon_type || '-'}</p>
                                </div>
                                <div>
                                    <p className="text-text-muted text-xs">{t('dashboard.url')}</p>
                                    <p className="text-text-primary text-xs truncate">{s.url}</p>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    )
}
