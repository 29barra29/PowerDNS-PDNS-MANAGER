import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Globe, LayoutDashboard, Search, ScrollText, Users, LogOut, Shield, Settings } from 'lucide-react'
import { useState, useEffect } from 'react'
import api from '../api'
import { useUpdateAvailability } from '../hooks/useUpdateAvailability'

const SESSION_PING_MS = 30 * 60 * 1000 // Sitzung alle 30 Min. prüfen (Token/Cookie)

export default function Layout() {
    const { t, i18n } = useTranslation()
    const navigate = useNavigate()
    const [user, setUser] = useState(() => api.getUser())
    const [appInfo, setAppInfo] = useState({ app_name: 'DNS Manager', app_version: '', app_logo_url: '' })
    const { updateAvailable } = useUpdateAvailability()

    /* eslint-disable react-hooks/set-state-in-effect -- sync user/appInfo from api on mount */
    useEffect(() => {
        const u = api.getUser()
        if (u) {
            setUser(u)
            if (u.preferred_language && u.preferred_language !== i18n.language) i18n.changeLanguage(u.preferred_language)
        }
        api.getAppInfo().then(setAppInfo).catch(console.error)
    }, []) // eslint-disable-line react-hooks/exhaustive-deps -- run once on mount
    /* eslint-enable react-hooks/set-state-in-effect */

    // Periodisch: Session gültig? (401 → api leitet zur Login-Seite)
    useEffect(() => {
        const id = setInterval(() => {
            api.getMe().catch(() => {})
        }, SESSION_PING_MS)
        return () => clearInterval(id)
    }, [])

    const handleLogout = () => {
        api.logout()
        navigate('/login')
    }

    const isAdmin = user?.role === 'admin'

    const links = [
        { to: '/', icon: LayoutDashboard, labelKey: 'layout.overview' },
        { to: '/zones', icon: Globe, labelKey: isAdmin ? 'layout.allZones' : 'layout.myZones' },
        { to: '/search', icon: Search, labelKey: 'layout.search' },
    ]
    if (isAdmin) {
        links.push({ to: '/audit', icon: ScrollText, labelKey: 'layout.audit' })
        links.push({ to: '/users', icon: Users, labelKey: 'layout.users' })
    }
    links.push({ to: '/settings', icon: Settings, labelKey: 'layout.settings' })

    const showSettingsUpdateDot = isAdmin && updateAvailable

    return (
        <div className="flex h-screen overflow-hidden">
            {/* Sidebar */}
            <aside className="w-64 bg-bg-secondary border-r border-border flex flex-col shrink-0">
                {/* Logo */}
                <div className="p-5 border-b border-border">
                    <div className="flex items-center gap-3">
                        {appInfo.app_logo_url ? (
                            <img src={appInfo.app_logo_url} alt="" className="w-9 h-9 rounded-lg object-contain bg-bg-secondary shadow-lg shadow-accent/10" />
                        ) : (
                            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center shadow-lg shadow-accent/10">
                                <Shield className="w-5 h-5 text-white" />
                            </div>
                        )}
                        <div>
                            <h1 className="text-sm font-bold text-text-primary">{appInfo.app_name}</h1>
                            {appInfo.app_version ? <p className="text-xs text-text-muted">v{appInfo.app_version}</p> : <p className="text-xs text-text-muted opacity-0">v0</p>}
                        </div>
                    </div>
                </div>

                {/* Navigation */}
                <nav className="flex-1 p-3 space-y-1">
                    {links.map(({ to, icon: Icon, labelKey }) => (  
                        <NavLink
                            key={to + labelKey}
                            to={to}
                            end={to === '/'}
                            className={({ isActive }) =>
                                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-200 ${isActive
                                    ? 'bg-accent/20 text-accent-light font-medium'
                                    : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                                }`
                            }
                        >
                            <Icon className="w-4 h-4 shrink-0" />
                            <span className="flex-1 min-w-0">{t(labelKey)}</span>
                            {to === '/settings' && showSettingsUpdateDot ? (
                                <span
                                    className="w-2 h-2 rounded-full bg-red-500 shrink-0 shadow-sm"
                                    title={t('layout.newVersionDot')}
                                    aria-label={t('layout.newVersionDot')}
                                />
                            ) : null}
                        </NavLink>
                    ))}
                </nav>

                {/* User */}
                <div className="p-3 border-t border-border">
                    <div className="flex items-center gap-3 px-3 py-2">
                        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-accent/40 to-purple-600/40 flex items-center justify-center text-xs font-bold text-accent-light uppercase">
                            {user?.username?.charAt(0) || '?'}
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-text-primary truncate">{user?.display_name || user?.username}</p>
                            <p className="text-xs text-text-muted capitalize">{user?.role === 'admin' ? `👑 ${t('layout.admin')}` : `👤 ${t('layout.user')}`}</p>
                        </div>
                        <button
                            onClick={handleLogout}
                            className="p-1.5 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                            title={t('layout.logout')}
                        >
                            <LogOut className="w-4 h-4" />
                        </button>
                    </div>
                </div>
            </aside>

            {/* Main content */}
            <main className="flex-1 overflow-auto p-6">
                <Outlet />
            </main>
        </div>
    )
}
