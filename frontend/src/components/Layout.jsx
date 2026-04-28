import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Globe, LayoutDashboard, Search, ScrollText, Users, LogOut, Shield, Settings, Menu, X } from 'lucide-react'
import { useState, useEffect } from 'react'
import api from '../api'
import { useUpdateAvailability } from '../hooks/useUpdateAvailability'
import LanguageDropdown from './LanguageDropdown'

const SESSION_PING_MS = 30 * 60 * 1000 // Sitzung alle 30 Min. prüfen (Token/Cookie)

export default function Layout() {
    const { t, i18n } = useTranslation()
    const navigate = useNavigate()
    const location = useLocation()
    const [user, setUser] = useState(() => api.getUser())
    const [appInfo, setAppInfo] = useState({ app_name: 'PDNS Manager', app_version: '', app_logo_url: '' })
    const [sidebarOpen, setSidebarOpen] = useState(false)
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

    // Sidebar bei Route-Wechsel automatisch schliessen (Mobile-UX)
    /* eslint-disable react-hooks/set-state-in-effect -- close mobile sidebar on navigation */
    useEffect(() => {
        setSidebarOpen(false)
    }, [location.pathname])
    /* eslint-enable react-hooks/set-state-in-effect */

    // ESC schliesst die Mobile-Sidebar
    useEffect(() => {
        if (!sidebarOpen) return
        const onKey = (e) => { if (e.key === 'Escape') setSidebarOpen(false) }
        document.addEventListener('keydown', onKey)
        return () => document.removeEventListener('keydown', onKey)
    }, [sidebarOpen])

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
            {/* Mobile-Backdrop hinter der Sidebar */}
            {sidebarOpen && (
                <button
                    type="button"
                    aria-label={t('layout.closeMenu')}
                    className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm md:hidden"
                    onClick={() => setSidebarOpen(false)}
                />
            )}

            {/* Sidebar – Mobile: kollabierbar via translate, Desktop (md+): immer sichtbar */}
            <aside
                className={`bg-bg-secondary border-r border-border flex flex-col shrink-0
                    fixed inset-y-0 left-0 z-40 w-64 transition-transform duration-200 ease-out
                    ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
                    md:translate-x-0 md:static md:z-auto`}
            >
                {/* Logo */}
                <div className="p-5 border-b border-border flex items-center justify-between gap-2">
                    <div className="flex items-center gap-3 min-w-0">
                        {appInfo.app_logo_url ? (
                            <img src={appInfo.app_logo_url} alt="" className="w-9 h-9 rounded-lg object-contain bg-bg-secondary shadow-lg shadow-accent/10" />
                        ) : (
                            <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center shadow-lg shadow-accent/10">
                                <Shield className="w-5 h-5 text-white" />
                            </div>
                        )}
                        <div className="min-w-0">
                            <h1 className="text-sm font-bold text-text-primary truncate">{appInfo.app_name}</h1>
                            {appInfo.app_version ? <p className="text-xs text-text-muted">v{appInfo.app_version}</p> : <p className="text-xs text-text-muted opacity-0">v0</p>}
                        </div>
                    </div>
                    {/* Close-Button nur auf Mobile, wenn Sidebar offen ist */}
                    <button
                        type="button"
                        onClick={() => setSidebarOpen(false)}
                        className="md:hidden p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-hover transition-colors"
                        aria-label={t('layout.closeMenu')}
                    >
                        <X className="w-5 h-5" />
                    </button>
                </div>

                {/* Navigation */}
                <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
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
                        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-accent/40 to-purple-600/40 flex items-center justify-center text-xs font-bold text-accent-light uppercase shrink-0">
                            {user?.username?.charAt(0) || '?'}
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-text-primary truncate">{user?.display_name || user?.username}</p>
                            <p className="text-xs text-text-muted capitalize">{user?.role === 'admin' ? `👑 ${t('layout.admin')}` : `👤 ${t('layout.user')}`}</p>
                        </div>
                        <button
                            onClick={handleLogout}
                            className="p-1.5 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors shrink-0"
                            title={t('layout.logout')}
                        >
                            <LogOut className="w-4 h-4" />
                        </button>
                    </div>
                </div>
            </aside>

            {/* Main content */}
            <div className="flex-1 flex flex-col min-w-0">
                {/* Mobile-Topbar – nur sichtbar auf <md, mit Hamburger + Sprach-Dropdown */}
                <header className="md:hidden sticky top-0 z-20 bg-bg-secondary/90 backdrop-blur border-b border-border px-3 py-2 flex items-center justify-between gap-2">
                    <button
                        type="button"
                        onClick={() => setSidebarOpen(true)}
                        className="p-2 rounded-lg text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
                        aria-label={t('layout.openMenu')}
                    >
                        <Menu className="w-5 h-5" />
                    </button>
                    <span className="text-sm font-semibold text-text-primary truncate">{appInfo.app_name}</span>
                    <LanguageDropdown />
                </header>
                <main className="flex-1 overflow-auto p-4 md:p-6">
                    <Outlet />
                </main>
            </div>
        </div>
    )
}
