import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertCircle, CheckCircle2, LogIn, RefreshCw } from 'lucide-react'
import api from '../../../api'
import PageSpinner from '../../PageSpinner'
import { useSettings } from '../settingsContext'
import SsoGeneralCard from '../../sso/SsoGeneralCard'
import SsoOidcCard from '../../sso/SsoOidcCard'
import SsoLdapCard from '../../sso/SsoLdapCard'
import {
    SECTION_PAYLOAD, changedSensitiveFields, generalFormFromSettings, isExternalAccount, ldapFormFromSettings,
    oidcFormFromSettings, sectionBlockers,
} from '../../sso/ssoModel'
import { isStepUpAbort, withStepUp } from '../../sso/stepUpStore'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'sso', order: 75, labelKey: 'settings.sso.tab', icon: LogIn, adminOnly: true }

const FORM_FROM = { general: generalFormFromSettings, oidc: oidcFormFromSettings, ldap: ldapFormFromSettings }

/*
 * Tab "Anmeldung / SSO" (F10 §2.5/§6.4, WS-F10-APP-FE), nur Admins (Backend: get_admin_session_user).
 * Drei Karten mit eigenem Speichern; gesendet wird nur der eigene Abschnitt (PUT /settings/sso).
 * Plan-Regeln: Step-up vor sensiblen Aenderungen [S8] (lokale Konten werden vorab nach dem Passwort gefragt,
 * externe Konten brauchen eine frische Anmeldung – reauth_required), JIT-Freigabe [S5], ID-Attribut [S16],
 * Gruppen-DN [S1], Sitzungsdauer (E-F10-2).
 */
export default function SsoTab({ active }) {
    const { t } = useTranslation()
    const { profile, isAdmin } = useSettings()
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState('')
    const [saved, setSaved] = useState(null)
    const [forms, setForms] = useState(null)
    const [banner, setBanner] = useState(null)
    const [busy, setBusy] = useState('')
    const [warnings, setWarnings] = useState({ general: [], oidc: [], ldap: [] })
    const [tests, setTests] = useState({ oidc: null, ldap: null })
    const [ldapTestUser, setLdapTestUser] = useState('')
    const [ldapTestPassword, setLdapTestPassword] = useState('')
    const loadedOnce = useRef(false)
    const external = isExternalAccount(profile)

    const load = useCallback(async () => {
        setLoading(true)
        setLoadError('')
        try {
            const data = await api.getSsoSettings()
            setSaved(data)
            setForms({
                general: generalFormFromSettings(data?.general),
                oidc: oidcFormFromSettings(data?.oidc),
                ldap: ldapFormFromSettings(data?.ldap),
            })
        } catch (err) {
            setLoadError(err?.message || '')
        } finally {
            setLoading(false)
        }
    }, [])

    // Erst laden, wenn der Tab sichtbar wird (Tabs bleiben danach gemountet)
    useEffect(() => {
        if (!active || !isAdmin || loadedOnce.current) return
        loadedOnce.current = true
        queueMicrotask(() => load())
    }, [active, isAdmin, load])

    const setSectionForm = useCallback((section) => (updater) => {
        setForms((prev) => ({ ...prev, [section]: typeof updater === 'function' ? updater(prev[section]) : updater }))
    }, [])

    async function handleSave(section) {
        if (busy || !forms) return
        const blockers = sectionBlockers(section, forms[section])
        if (blockers.length) {
            setBanner({ type: 'error', text: blockers.map((k) => t(k)).join(' ') })
            return
        }
        const payload = SECTION_PAYLOAD[section](forms[section])
        // Lokale Admins vorab fragen; externe Konten bekommen ggf. reauth_required vom Backend
        const proactive = !external && changedSensitiveFields(section, saved?.[section], payload).length > 0
        setBusy(section)
        setBanner(null)
        try {
            const res = await withStepUp((stepUp) => api.updateSsoSettings({ [section]: payload }, stepUp), { proactive })
            const next = res?.settings || saved
            setSaved(next)
            // Nur den gespeicherten Abschnitt neu setzen; ungespeicherte Eingaben der anderen Karten bleiben
            setForms((prev) => ({ ...prev, [section]: FORM_FROM[section](next?.[section]) }))
            setWarnings((prev) => ({ ...prev, [section]: Array.isArray(res?.warnings) ? res.warnings : [] }))
            setBanner({ type: 'success', text: t('settings.sso.saved') })
        } catch (err) {
            if (isStepUpAbort(err)) return
            if (err?.stepUpHandled) {
                setBanner({ type: 'error', text: t('settings.sso.reauthNeeded') })
                return
            }
            setBanner({ type: 'error', text: err?.message || t('settings.sso.saveFailed') })
        } finally {
            setBusy('')
        }
    }

    async function handleTest(section) {
        if (busy || !forms) return
        const body = { target: section, [section]: SECTION_PAYLOAD[section](forms[section]) }
        if (section === 'ldap') {
            if (ldapTestUser.trim()) body.test_username = ldapTestUser.trim()
            if (ldapTestPassword) body.test_password = ldapTestPassword
        }
        setBusy(`test-${section}`)
        setTests((prev) => ({ ...prev, [section]: null }))
        try {
            const res = await api.testSsoSettings(body)
            setTests((prev) => ({ ...prev, [section]: res }))
        } catch (err) {
            setTests((prev) => ({ ...prev, [section]: { success: false, error: err?.message || t('settings.sso.testFailed'), warnings: [], details: {} } }))
        } finally {
            // Testpasswort nie stehen lassen
            if (section === 'ldap') setLdapTestPassword('')
            setBusy('')
        }
    }

    if (!isAdmin) return null
    if (loading && !forms) return <PageSpinner />

    if (loadError && !forms) {
        return (
            <div className="glass-card p-6 space-y-3">
                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm flex items-start gap-2">
                    <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                    <span>{t('settings.sso.loadFailed')}{loadError ? ` (${loadError})` : ''}</span>
                </div>
                <button type="button" onClick={load} className="px-4 py-2 rounded-lg border border-border hover:bg-bg-hover text-sm flex items-center gap-2">
                    <RefreshCw className="w-4 h-4" aria-hidden="true" />
                    {t('settings.sso.reload')}
                </button>
            </div>
        )
    }
    if (!forms) return null

    const stepUpHint = external ? t('settings.sso.stepUpHintExternal') : t('settings.sso.stepUpHint')

    return (
        <div className="space-y-6">
            <div>
                <h2 className="text-xl font-semibold text-text-primary">{t('settings.sso.title')}</h2>
                <p className="text-sm text-text-muted">{t('settings.sso.subtitle')}</p>
            </div>

            {banner && (
                <div
                    role={banner.type === 'error' ? 'alert' : 'status'}
                    className={`p-3 rounded-lg border text-sm flex items-start gap-2 ${banner.type === 'error'
                        ? 'bg-danger/10 border-danger/30 text-danger'
                        : 'bg-success/10 border-success/30 text-success'}`}
                >
                    {banner.type === 'error'
                        ? <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                        : <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />}
                    <p className="flex-1 break-words">{banner.text}</p>
                    <button type="button" onClick={() => setBanner(null)} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            <SsoGeneralCard
                form={forms.general}
                setForm={setSectionForm('general')}
                info={saved?.general}
                busy={busy}
                onSave={() => handleSave('general')}
                warnings={warnings.general}
            />
            <SsoOidcCard
                form={forms.oidc}
                setForm={setSectionForm('oidc')}
                general={saved?.general}
                busy={busy}
                onSave={() => handleSave('oidc')}
                onTest={() => handleTest('oidc')}
                test={tests.oidc}
                warnings={warnings.oidc}
                blockers={sectionBlockers('oidc', forms.oidc)}
                stepUpHint={stepUpHint}
            />
            <SsoLdapCard
                form={forms.ldap}
                setForm={setSectionForm('ldap')}
                saved={saved?.ldap}
                general={saved?.general}
                busy={busy}
                onSave={() => handleSave('ldap')}
                onTest={() => handleTest('ldap')}
                test={tests.ldap}
                warnings={warnings.ldap}
                blockers={sectionBlockers('ldap', forms.ldap)}
                stepUpHint={stepUpHint}
                testUser={ldapTestUser}
                setTestUser={setLdapTestUser}
                testPassword={ldapTestPassword}
                setTestPassword={setLdapTestPassword}
            />
        </div>
    )
}
