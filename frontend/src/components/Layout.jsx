import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { Globe, LayoutDashboard, Search, ScrollText, Users, LogOut, Shield, Settings } from 'lucide-react'
import { useState, useEffect } from 'react'
import api from '../api'

export default function Layout() {
    const navigate = useNavigate()
    const [user, setUser] = useState(null)

    useEffect(() => {
        const stored = localStorage.getItem('user')
        if (stored) setUser(JSON.parse(stored))
    }, [])

    const handleLogout = () => {
        api.logout()
        navigate('/login')
    }

    const isAdmin = user?.role === 'admin'

    // Basis-Links für alle Benutzer
    const links = [
        { to: '/', icon: LayoutDashboard, label: 'Übersicht' },
        { to: '/zones', icon: Globe, label: 'Meine Zonen' },
        { to: '/search', icon: Search, label: 'Suche' },
    ]

    // Admin-only Links
    if (isAdmin) {
        links[1] = { to: '/zones', icon: Globe, label: 'Alle Zonen' }
        links.push({ to: '/audit', icon: ScrollText, label: 'Protokoll' })
        links.push({ to: '/users', icon: Users, label: 'Benutzer' })
        links.push({ to: '/settings', icon: Settings, label: 'Einstellungen' })
    }

    return (
        <div className="flex h-screen overflow-hidden">
            {/* Sidebar */}
            <aside className="w-64 bg-bg-secondary border-r border-border flex flex-col shrink-0">
                {/* Logo */}
                <div className="p-5 border-b border-border">
                    <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center shadow-lg shadow-accent/10">
                            <Shield className="w-5 h-5 text-white" />
                        </div>
                        <div>
                            <h1 className="text-sm font-bold text-text-primary">DNS Manager</h1>
                            <p className="text-xs text-text-muted">v2.0</p>
                        </div>
                    </div>
                </div>

                {/* Navigation */}
                <nav className="flex-1 p-3 space-y-1">
                    {links.map(({ to, icon: Icon, label }) => (
                        <NavLink
                            key={to + label}
                            to={to}
                            end={to === '/'}
                            className={({ isActive }) =>
                                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all duration-200 ${isActive
                                    ? 'bg-accent/20 text-accent-light font-medium'
                                    : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'
                                }`
                            }
                        >
                            <Icon className="w-4 h-4" />
                            {label}
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
                            <p className="text-xs text-text-muted capitalize">{user?.role === 'admin' ? '👑 Admin' : '👤 Benutzer'}</p>
                        </div>
                        <button
                            onClick={handleLogout}
                            className="p-1.5 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                            title="Abmelden"
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
