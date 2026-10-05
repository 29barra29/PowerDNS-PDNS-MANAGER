import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Database } from 'lucide-react'
import api from '../../../api'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'about', order: 100, labelKey: 'settings.about', icon: Database, adminOnly: false }

// Tab "Über" – mechanisch aus SettingsPage.jsx 2.4.1 übernommen.
export default function AboutTab({ active }) {
    const { t } = useTranslation()
    // Über-Tab: Version kommt aus API (eine zentrale Quelle: VERSION-Datei)
    const [appInfo, setAppInfo] = useState(null)

    // Wie 2.4.1: bei jedem Öffnen des Tabs neu laden
    useEffect(() => {
        if (active) api.getAppInfo().then(setAppInfo).catch(() => setAppInfo(null))
    }, [active])

    return (
        <div className="glass-card p-5">
            <h2 className="text-lg font-semibold text-text-primary mb-4">{t('settingsMore.aboutTitle')}</h2>
            <div className="space-y-3">
                {[
                    [t('settingsMore.version'), appInfo?.app_version || '–'],
                    [t('settingsMore.frontend'), 'React + Vite + Tailwind CSS'],
                    [t('settingsMore.backend'), 'Python FastAPI'],
                    [t('settingsMore.database'), 'MariaDB 11'],
                    [t('settingsMore.dnsEngine'), 'PowerDNS Authoritative'],
                    [t('settingsMore.auth'), 'JWT (Bearer Token)'],
                ].map(([k, v]) => (
                    <div key={k} className="flex justify-between p-3 bg-bg-primary rounded-lg border border-border">
                        <span className="text-sm text-text-muted">{k}</span>
                        <span className="text-sm font-medium text-text-primary">{v}</span>
                    </div>
                ))}
            </div>
            <div className="mt-4 p-4 bg-accent/5 rounded-xl border border-accent/20">
                <p className="text-sm text-text-secondary">
                    {t('settings.aboutText')}
                    {t('settingsMore.aboutOpenSource')} 🚀
                </p>
            </div>
        </div>
    )
}
