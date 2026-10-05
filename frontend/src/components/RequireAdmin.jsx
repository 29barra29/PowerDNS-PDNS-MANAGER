import { ShieldOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import api from '../api'

// Routen-Gate fuer Admin-Seiten (F8 §6.3.3, B01). Nur Kosmetik: das Backend prueft weiterhin selbst
// (get_admin_user); Nicht-Admins sehen statt der Seite eine "Kein Zugriff"-Karte und die Seite
// (inkl. ihrer API-Aufrufe) wird gar nicht erst gerendert.
export default function RequireAdmin({ children }) {
    const { t } = useTranslation()
    const user = api.getUser()
    if (user?.role === 'admin') return children
    return (
        <div className="glass-card p-8 max-w-lg mx-auto text-center space-y-3" role="alert">
            <ShieldOff className="w-10 h-10 mx-auto text-text-muted" aria-hidden="true" />
            <h1 className="text-xl font-bold text-text-primary">{t('common.noAccessTitle')}</h1>
            <p className="text-sm text-text-muted">{t('common.noAccessAdminOnly')}</p>
            <Link to="/" className="inline-block px-4 py-2 rounded-lg bg-accent text-white text-sm">
                {t('common.noAccessBack')}
            </Link>
        </div>
    )
}
