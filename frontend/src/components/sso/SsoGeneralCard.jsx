import { useTranslation } from 'react-i18next'
import { Settings2 } from 'lucide-react'
import { Card, Check, CopyValue, Field, Notice, SaveBar, WarningList } from './SsoFields'
import { sessionLifetimeHours } from './ssoModel'

// Karte "Allgemein" des SSO-Tabs (F10 §2.5): Redirect-URI, lokale Anmeldung, 2FA nach OIDC, Hinweise.
// Sitzungsdauer (E-F10-2): Warnung, wenn AUTH_COOKIE_MAX_AGE > 24 h (Feld general.session_max_age, Antrag an
// WS-F10-APP-BE); ohne das Feld bleibt nur die allgemeine Empfehlung.
export default function SsoGeneralCard({ form, setForm, info, busy, onSave, warnings }) {
    const { t } = useTranslation()
    const g = info || {}
    const hours = sessionLifetimeHours(g.session_max_age)
    const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }))

    return (
        <Card icon={Settings2} title={t('settings.sso.generalTitle')} subtitle={t('settings.sso.subtitle')}>
            {g.insecure_allowed && <Notice tone="danger">{t('settings.sso.insecureAllowed')}</Notice>}

            {g.redirect_uri ? (
                <Field id="sso-redirect-uri" label={t('settings.sso.redirectUri')} hint={t('settings.sso.redirectUriHint')}>
                    <CopyValue value={g.redirect_uri} />
                </Field>
            ) : (
                <Notice tone="warning">{t('settings.sso.baseUrlMissing')}</Notice>
            )}

            <div className="space-y-3">
                <Check
                    id="sso-local-login"
                    checked={form.local_login_enabled}
                    onChange={set('local_login_enabled')}
                    label={t('settings.sso.localLoginEnabled')}
                    hint={t('settings.sso.localLoginEnabledHint')}
                />
                {g.emergency_login_url && (
                    <p className="text-xs text-text-muted pl-6 break-all">{t('settings.sso.emergencyUrl', { url: g.emergency_login_url })}</p>
                )}
                <Check
                    id="sso-require-totp"
                    checked={form.require_totp}
                    onChange={set('require_totp')}
                    label={t('settings.sso.requireTotp')}
                    hint={t('settings.sso.requireTotpHint')}
                />
            </div>

            {hours !== null
                ? <Notice tone="warning">{t('settings.sso.sessionLifetimeWarning', { hours })}</Notice>
                : <p className="text-xs text-text-muted">{t('settings.sso.sessionLifetimeHint')}</p>}

            <WarningList warnings={warnings} />
            <SaveBar busy={busy} saveKey="general" onSave={onSave} />
        </Card>
    )
}
