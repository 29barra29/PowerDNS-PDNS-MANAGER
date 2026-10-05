import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import api from '../../../api'
import SsoAccountLinkCard from '../../sso/SsoAccountLinkCard'
import { normalizeProviders, ssoErrorKey } from '../../sso/ssoModel'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'sso-link', order: 5 }

// Karte "Anmeldung ueber Firmenkonto" oben im Tab "API & Sicherheit" (F10 §2.6, WS-F10-APP-FE).
// Wertet die Rueckkehr der OIDC-Verknuepfung aus (?sso_linked=oidc bzw. ?sso_error=<code>) und entfernt die
// Parameter danach wieder (der Tab-Parameter bleibt). Ohne erlaubte Verknuepfung rendert die Karte nichts.
export default function SsoLinkCard() {
    const { t } = useTranslation()
    const { profile, reloadProfile, notify } = useSettings()
    const [searchParams, setSearchParams] = useSearchParams()
    const [providers, setProviders] = useState(null)
    // Rueckmeldung aus der URL einmalig lesen (Initialisierer statt Effekt)
    const [returnInfo] = useState(() => ({
        linked: searchParams.get('sso_linked') || '',
        error: (searchParams.get('sso_error') || '').replace(/[^a-z_]/g, '').slice(0, 40),
    }))
    const returnHandled = useRef(false)

    useEffect(() => {
        const ctrl = new AbortController()
        api.getSsoProviders({ signal: ctrl.signal })
            .then((res) => setProviders(normalizeProviders(res)))
            .catch((err) => {
                if (err?.name !== 'AbortError') setProviders(normalizeProviders(null))
            })
        return () => ctrl.abort()
    }, [])

    useEffect(() => {
        if (returnHandled.current || (!returnInfo.linked && !returnInfo.error)) return
        returnHandled.current = true
        if (returnInfo.linked) {
            notify.success(t('settings.ssoLinkedSuccess', { source: t(returnInfo.linked === 'ldap' ? 'settings.authSource.ldap' : 'settings.authSource.oidc') }))
            // Das Konto ist jetzt extern: Profil neu laden (Felder werden read-only)
            reloadProfile().catch(() => {})
        } else {
            notify.error(t(ssoErrorKey(returnInfo.error), { code: '–' }))
        }
        setSearchParams((prev) => {
            const next = new URLSearchParams(prev)
            next.delete('sso_linked')
            next.delete('sso_error')
            next.delete('sso_detail')
            return next
        }, { replace: true })
    }, [returnInfo, notify, reloadProfile, setSearchParams, t])

    async function handleLinked(message) {
        notify.success(message)
        try {
            await reloadProfile()
        } catch {
            /* Profil laedt beim naechsten Seitenaufruf */
        }
    }

    return <SsoAccountLinkCard user={profile} providers={providers} onLinked={handleLinked} />
}
