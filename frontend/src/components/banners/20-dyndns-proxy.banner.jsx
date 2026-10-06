import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import api from '../../api'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const banner = { id: 'dyndns-proxy', adminOnly: true }

// Admin-Hinweis (Plan [S6]): Der Panel-Aufruf kam von einer privaten Peer-IP (Reverse-Proxy), aber
// TRUST_PROXY_HEADERS ist aus. Dann sieht DynDNS ohne myip-Parameter die Proxy-IP statt der Router-IP
// (Updates scheitern mit badip) und die Rate-Limits je IP treffen alle Router gemeinsam.
// Quelle: GET /dyndns/info -> proxy_warning (nur fuer Admins). Ausgeblendet, wenn DynDNS abgeschaltet ist.
// "Ausblenden" gilt fuer die laufende Browser-Sitzung (sessionStorage, in try/catch).
const DISMISS_KEY = 'dns_manager_dyndns_proxy_banner_dismissed'

function readDismissed() {
    try {
        return window.sessionStorage.getItem(DISMISS_KEY) === '1'
    } catch {
        return false
    }
}

export default function DyndnsProxyBanner({ isAdmin }) {
    const { t } = useTranslation()
    const [show, setShow] = useState(false)
    const [dismissed, setDismissed] = useState(readDismissed)

    useEffect(() => {
        if (!isAdmin || dismissed) return undefined
        const ctrl = new AbortController()
        api.getDyndnsInfo({ signal: ctrl.signal })
            .then((info) => setShow(!!info?.enabled && info?.proxy_warning === true))
            .catch(() => {}) // still: Banner ist nur ein Hinweis (403/Netzwerk -> nichts anzeigen)
        return () => ctrl.abort()
    }, [isAdmin, dismissed])

    if (!isAdmin || dismissed || !show) return null

    function dismiss() {
        try { window.sessionStorage.setItem(DISMISS_KEY, '1') } catch { /* Speicher gesperrt: nur bis zum Neuladen */ }
        setDismissed(true)
    }

    return (
        <div role="status" className="mb-4 p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-200 flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
            <div className="flex-1 min-w-0 text-sm space-y-1">
                <p className="font-medium">{t('layout.dyndnsProxyBannerTitle')}</p>
                <p className="break-words">{t('layout.dyndnsProxyBannerBody')}</p>
                <Link to="/settings?tab=integrations" className="inline-block text-xs underline hover:no-underline">
                    {t('layout.dyndnsProxyBannerLink')}
                </Link>
            </div>
            <button type="button" onClick={dismiss} className="text-xs hover:underline shrink-0" aria-label={t('layout.dyndnsProxyBannerDismiss')}>
                ×
            </button>
        </div>
    )
}
