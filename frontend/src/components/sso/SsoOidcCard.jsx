import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound } from 'lucide-react'
import { Card, Check, Field, Notice, SaveBar, SecretField, Select, TextInput, WarningList } from './SsoFields'
import SsoAccountsBlock from './SsoAccountsBlock'
import SsoPresets from './SsoPresets'
import SsoTestResult from './SsoTestResult'
import { applyPreset } from './ssoModel'

// Karte "OpenID Connect" des SSO-Tabs (F10 §2.5/§6.4). Formularzustand haelt der Tab (sso.tab.jsx).
export default function SsoOidcCard({ form, setForm, general, busy, onSave, onTest, test, warnings, blockers, stepUpHint }) {
    const { t } = useTranslation()
    const [issuerPlaceholder, setIssuerPlaceholder] = useState('https://sso.example.com/realms/<realm>')
    const [presetInfo, setPresetInfo] = useState('')
    const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }))
    const noBaseUrl = !general?.base_url

    function handlePreset(id, preset) {
        setForm((f) => applyPreset('oidc', id, f))
        setIssuerPlaceholder(preset.issuerPlaceholder)
        setPresetInfo(t('settings.sso.presetApplied'))
    }

    return (
        <Card icon={KeyRound} title={t('settings.sso.oidcTitle')} subtitle={t('settings.sso.oidcHint')}>
            <div className="flex flex-wrap items-center justify-between gap-3">
                <SsoPresets kind="oidc" onApply={handlePreset} disabled={!!busy} />
                <span className="text-xs text-text-muted">{t('settings.sso.linkedAccounts', { count: form.linked_accounts })}</span>
            </div>
            {presetInfo && <Notice tone="info" role="status">{presetInfo}</Notice>}

            <Check id="sso-oidc-enabled" checked={form.enabled} onChange={set('enabled')} label={t('settings.sso.oidcEnabled')} />
            {form.enabled && noBaseUrl && <Notice tone="warning">{t('settings.sso.baseUrlMissing')}</Notice>}

            <div className="grid gap-4 md:grid-cols-2">
                <Field id="sso-oidc-display-name" label={t('settings.sso.displayName')} hint={t('settings.sso.displayNameHint')}>
                    <TextInput id="sso-oidc-display-name" value={form.display_name} onChange={set('display_name')} maxLength={60} />
                </Field>
                <Field id="sso-oidc-client-id" label={t('settings.sso.clientId')}>
                    <TextInput id="sso-oidc-client-id" value={form.client_id} onChange={set('client_id')} mono maxLength={255} />
                </Field>
            </div>

            <Field id="sso-oidc-issuer" label={t('settings.sso.issuer')} hint={t('settings.sso.issuerHint')}>
                <TextInput id="sso-oidc-issuer" value={form.issuer} onChange={set('issuer')} mono maxLength={500} placeholder={issuerPlaceholder} />
            </Field>

            <SecretField
                id="sso-oidc-secret"
                label={t('settings.sso.clientSecret')}
                isSet={form.client_secret_set}
                unreadable={form.client_secret_unreadable}
                value={form.client_secret_new}
                onChange={set('client_secret_new')}
                remove={form.client_secret_remove}
                onRemove={set('client_secret_remove')}
            />

            <div className="grid gap-4 md:grid-cols-2">
                <Field id="sso-oidc-auth-method" label={t('settings.sso.tokenAuthMethod')}>
                    <Select id="sso-oidc-auth-method" value={form.token_auth_method} onChange={set('token_auth_method')} options={[
                        { value: 'client_secret_basic', label: t('settings.sso.tokenAuthBasic') },
                        { value: 'client_secret_post', label: t('settings.sso.tokenAuthPost') },
                        { value: 'none', label: t('settings.sso.tokenAuthNone') },
                    ]} />
                </Field>
                <Field id="sso-oidc-prompt" label={t('settings.sso.prompt')}>
                    <Select id="sso-oidc-prompt" value={form.prompt} onChange={set('prompt')} options={[
                        { value: '', label: t('settings.sso.promptNone') },
                        { value: 'login', label: t('settings.sso.promptLogin') },
                        { value: 'select_account', label: t('settings.sso.promptSelectAccount') },
                        { value: 'consent', label: t('settings.sso.promptConsent') },
                    ]} />
                </Field>
            </div>

            <Field id="sso-oidc-scopes" label={t('settings.sso.scopes')} hint={t('settings.sso.scopesHint')}>
                <TextInput id="sso-oidc-scopes" value={form.scopes} onChange={set('scopes')} mono maxLength={500} />
            </Field>
            <Check id="sso-oidc-userinfo" checked={form.use_userinfo} onChange={set('use_userinfo')} label={t('settings.sso.useUserinfo')} />

            <details className="rounded-xl border border-border/60 p-4">
                <summary className="cursor-pointer text-sm font-semibold text-text-primary">{t('settings.sso.advancedClaims')}</summary>
                <div className="grid gap-4 md:grid-cols-2 mt-4">
                    <Field id="sso-oidc-claim-username" label={t('settings.sso.usernameClaim')}>
                        <TextInput id="sso-oidc-claim-username" value={form.username_claim} onChange={set('username_claim')} mono maxLength={100} />
                    </Field>
                    <Field id="sso-oidc-claim-email" label={t('settings.sso.emailClaim')}>
                        <TextInput id="sso-oidc-claim-email" value={form.email_claim} onChange={set('email_claim')} mono maxLength={100} />
                    </Field>
                    <Field id="sso-oidc-claim-name" label={t('settings.sso.nameClaim')}>
                        <TextInput id="sso-oidc-claim-name" value={form.name_claim} onChange={set('name_claim')} mono maxLength={100} />
                    </Field>
                    <Field id="sso-oidc-claim-groups" label={t('settings.sso.groupsClaim')}>
                        <TextInput id="sso-oidc-claim-groups" value={form.groups_claim} onChange={set('groups_claim')} mono maxLength={100} />
                    </Field>
                </div>
                <p className="text-xs text-text-muted mt-2">{t('settings.sso.claimHint')}</p>
            </details>

            <SsoAccountsBlock kind="oidc" form={form} setForm={setForm} />

            <p className="text-xs text-text-muted">{t('settings.sso.testUsesForm')}</p>
            {stepUpHint && <p className="text-xs text-text-muted">{stepUpHint}</p>}
            {blockers.length > 0 && <Notice tone="warning">{blockers.map((k) => <p key={k}>{t(k)}</p>)}</Notice>}
            <SaveBar busy={busy} saveKey="oidc" testKey="test-oidc" onSave={onSave} onTest={onTest} saveDisabled={blockers.length > 0} />
            <SsoTestResult result={test} target="oidc" />
            <WarningList warnings={warnings} />
        </Card>
    )
}
