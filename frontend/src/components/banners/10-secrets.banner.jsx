import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import api from '../../api'
import { secretsBannerKind, secretsBannerSignature, unreadableServerNames } from '../../api/secrets'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const banner = { id: 'secrets', adminOnly: true }

// Admin-Banner Verschluesselung (F5 §2 D/I, §6.3): rot, wenn gespeicherte Geheimnisse nicht entschluesselt werden
// koennen oder das Backend im Klartext-Fallback laeuft. Nennt die PowerDNS-Server, deren API-Key nicht lesbar ist –
// sie sind nicht geladen und werden bei Zonen-Aenderungen uebersprungen [D4].
// Quelle: GET /settings/secrets/status (nur Admin + Browser-Session); Fehler (403, Netzwerk) still ignorieren.
// "Schliessen" gilt fuer die Browser-Sitzung und nur fuer genau diese Lage (Signatur aus Fingerprint, Anzahl, Modus).
const DISMISS_KEY = 'pdnsmgr.secretsBannerDismissed'

function readDismissed() {
    try {
        return window.sessionStorage.getItem(DISMISS_KEY) || ''
    } catch {
        return ''
    }
}

export default function SecretsBanner({ isAdmin }) {
    const { t } = useTranslation()
    const [status, setStatus] = useState(null)
    const [dismissed, setDismissed] = useState(readDismissed)

    useEffect(() => {
        if (!isAdmin) return undefined
        const ctrl = new AbortController()
        api.getSecretsStatus({ signal: ctrl.signal })
            .then((data) => setStatus(data && typeof data === 'object' ? data : null))
            .catch(() => {}) // still: nur ein Hinweis; Details zeigt die Statuskarte
        return () => ctrl.abort()
    }, [isAdmin])

    const kind = secretsBannerKind(status)
    if (!isAdmin || !kind) return null
    const signature = secretsBannerSignature(status)
    if (dismissed === signature) return null

    const servers = unreadableServerNames(status)

    function dismiss() {
        try { window.sessionStorage.setItem(DISMISS_KEY, signature) } catch { /* Speicher gesperrt: nur bis zum Neuladen */ }
        setDismissed(signature)
    }

    return (
        <div role="alert" className="mb-4 p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3">
            <ShieldAlert className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
            <div className="flex-1 min-w-0 text-sm space-y-1">
                <p className="font-medium break-words">
                    {kind === 'plaintext' ? t('layout.secretsBannerPlaintext') : t('layout.secretsBannerUnreadable')}
                </p>
                {servers.length > 0 && (
                    <p className="break-words">{t('layout.secretsBannerServers', { list: servers.join(', ') })}</p>
                )}
                <Link to="/settings?tab=security" className="inline-block text-xs underline hover:no-underline">
                    {t('layout.secretsBannerAction')}
                </Link>
            </div>
            <button type="button" onClick={dismiss} className="text-xs hover:underline shrink-0" aria-label={t('layout.secretsBannerDismiss')} title={t('layout.secretsBannerDismiss')}>
                ×
            </button>
        </div>
    )
}
