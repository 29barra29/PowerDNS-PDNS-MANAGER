import { useState, useEffect, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { AlertCircle, CheckCircle2 } from 'lucide-react'
import api from '../api'
import PageSpinner from '../components/PageSpinner'
import { useUpdateAvailability } from '../hooks/useUpdateAvailability'
import { SettingsContext } from '../components/settings/settingsContext'
import { getVisibleTabs, resolveTabId } from '../components/settings/SettingsTabRegistry'

// Shell der Einstellungsseite (Plan B.14, W0-INT-FE1b): Laden von Profil/Admin-Info, Seiten-Banner,
// Tab-Leiste mit Deep-Link `?tab=<id>` und Kontext fuer die Tabs. Der Inhalt der Tabs liegt in
// components/settings/tabs/*.tab.jsx (Registry: components/settings/SettingsTabRegistry.js).
export default function SettingsPage() {
    const { t } = useTranslation()
    const { updateAvailable } = useUpdateAvailability()
    const [searchParams, setSearchParams] = useSearchParams()
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')
    const [profile, setProfile] = useState(null)
    // Admin-only Daten (install_path, app_base_url) – nicht öffentlich
    const [adminInfo, setAdminInfo] = useState(null)
    // Bereits geöffnete Tabs bleiben gemountet (Zustand bleibt beim Wechsel erhalten wie in 2.4.1)
    const [mountedTabs, setMountedTabs] = useState(() => new Set())

    const isAdmin = profile?.role === 'admin'
    // Aktiver Tab kommt aus ?tab=; unbekannte oder für Nicht-Admins gesperrte Tabs fallen auf "profile" zurück
    const activeTab = resolveTabId(searchParams.get('tab'), isAdmin)
    const tabs = getVisibleTabs(isAdmin)

    const setActiveTab = useCallback((id) => {
        setSearchParams((prev) => {
            const next = new URLSearchParams(prev)
            next.set('tab', id)
            return next
        }, { replace: true })
    }, [setSearchParams])

    const reloadProfile = useCallback(async () => {
        const data = await api.getMe()
        setProfile(data)
        return data
    }, [])

    useEffect(() => {
        api.getMe()
            .then(setProfile)
            .catch((err) => setError(err.message))
            .finally(() => setLoading(false))
    }, [])

    useEffect(() => {
        if (!isAdmin) return
        // install_path / app_base_url nur für Admins laden – steht nicht mehr im öffentlichen /app-info
        api.getAdminInfo()
            .then(setAdminInfo)
            .catch(() => setAdminInfo(null))
    }, [isAdmin])

    // Erfolgsmeldung verschwindet nach 4 s automatisch (verdeckt nichts dauerhaft)
    useEffect(() => {
        if (!success) return
        const timer = setTimeout(() => setSuccess(''), 4000)
        return () => clearTimeout(timer)
    }, [success])

    useEffect(() => {
        if (loading || mountedTabs.has(activeTab)) return
        // eslint-disable-next-line react-hooks/set-state-in-effect -- merkt sich geöffnete Tabs (keep-alive)
        setMountedTabs((prev) => new Set(prev).add(activeTab))
    }, [activeTab, loading, mountedTabs])

    const notify = useMemo(() => ({
        error: (msg) => setError(msg || ''),
        success: (msg) => setSuccess(msg || ''),
        clear: () => { setError(''); setSuccess('') },
    }), [])

    const ctx = useMemo(() => ({
        profile, setProfile, isAdmin, adminInfo, reloadProfile, notify, activeTab, setActiveTab,
    }), [profile, isAdmin, adminInfo, reloadProfile, notify, activeTab, setActiveTab])

    if (loading) return <PageSpinner />

    return (
        <SettingsContext.Provider value={ctx}>
            <div className="space-y-6">
                <div>
                    <h1 className="text-2xl font-bold text-text-primary">{t('settings.title')}</h1>
                    <p className="text-text-muted text-sm mt-1">{t('settings.subtitle')}</p>
                </div>

                {error && (
                    <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-center gap-3">
                        <AlertCircle className="w-5 h-5 shrink-0" />
                        <p className="text-sm">{error}</p>
                        <button onClick={() => setError('')} className="ml-auto text-xs hover:underline">×</button>
                    </div>
                )}
                {success && (
                    <div className="p-4 rounded-xl bg-success/10 border border-success/30 text-success flex items-center gap-3">
                        <CheckCircle2 className="w-5 h-5 shrink-0" />
                        <p className="text-sm">{success}</p>
                        <button onClick={() => setSuccess('')} className="ml-auto text-xs hover:underline">×</button>
                    </div>
                )}

                {/* Tabs */}
                {/* overflow-x-auto + shrink-0/whitespace-nowrap auf den Buttons:
                    auf Mobile scrollt die Tab-Leiste selbst, nicht die ganze Seite. */}
                <div className="flex gap-1 p-1 bg-bg-secondary rounded-xl border border-border overflow-x-auto">
                    {tabs.map((tab) => (
                        <button
                            key={tab.id}
                            type="button"
                            onClick={() => setActiveTab(tab.id)}
                            className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all whitespace-nowrap shrink-0 ${activeTab === tab.id
                                ? 'bg-accent/20 text-accent-light'
                                : 'text-text-muted hover:text-text-primary hover:bg-bg-hover'
                                }`}
                        >
                            <tab.icon className="w-4 h-4 shrink-0" />
                            <span className="text-left">{t(tab.labelKey)}</span>
                            {tab.id === 'updates' && updateAvailable ? (
                                <span
                                    className="w-2 h-2 rounded-full bg-red-500 shrink-0"
                                    title={t('layout.newVersionDot')}
                                    aria-hidden
                                />
                            ) : null}
                        </button>
                    ))}
                </div>

                {tabs.map(({ id, Component }) => {
                    const active = id === activeTab
                    if (!active && !mountedTabs.has(id)) return null
                    return (
                        <div key={id} hidden={!active}>
                            <Component active={active} />
                        </div>
                    )
                })}
            </div>
        </SettingsContext.Provider>
    )
}
