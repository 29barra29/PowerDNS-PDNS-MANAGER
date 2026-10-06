import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Network } from 'lucide-react'
import { Card, Check, Field, LinesInput, Notice, SaveBar, SecretField, Select, TextInput, WarningList } from './SsoFields'
import SsoAccountsBlock from './SsoAccountsBlock'
import SsoPresets from './SsoPresets'
import SsoTestResult from './SsoTestResult'
import { applyPreset, isSafeUniqueIdAttr, SAFE_UNIQUE_ID_ATTRS } from './ssoModel'

// Karte "LDAP / Active Directory" des SSO-Tabs (F10 §2.5/§6.4) mit der Bestaetigung fuer ein ID-Attribut
// ausserhalb der Allowlist [S16]. Formularzustand haelt der Tab (sso.tab.jsx); `saved` = zuletzt geladener Stand.
export default function SsoLdapCard({
    form, setForm, saved, general, busy, onSave, onTest, test, warnings, blockers, stepUpHint,
    testUser, setTestUser, testPassword, setTestPassword,
}) {
    const { t } = useTranslation()
    const [serverPlaceholder, setServerPlaceholder] = useState('ldaps://dc1.example.local:636')
    const [presetInfo, setPresetInfo] = useState('')
    const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }))
    const insecure = !!general?.insecure_allowed
    const uniqueUnsafe = !isSafeUniqueIdAttr(form.unique_id_attr)

    function handlePreset(id, preset) {
        setForm((f) => applyPreset('ldap', id, f))
        setServerPlaceholder(preset.serverPlaceholder)
        setPresetInfo(t('settings.sso.presetApplied'))
    }

    // Ein anderes Attribut uebernimmt die gespeicherte Bestaetigung nicht (Backend-Regel [S16])
    function setUniqueIdAttr(value) {
        setForm((f) => ({
            ...f,
            unique_id_attr: value,
            unique_id_attr_confirmed: value.trim() === String(saved?.unique_id_attr || '') ? !!saved?.unique_id_attr_confirmed : false,
        }))
    }

    return (
        <Card icon={Network} title={t('settings.sso.ldapTitle')} subtitle={t('settings.sso.ldapHint')}>
            <div className="flex flex-wrap items-center justify-between gap-3">
                <SsoPresets kind="ldap" onApply={handlePreset} disabled={!!busy} />
                <span className="text-xs text-text-muted">{t('settings.sso.linkedAccounts', { count: form.linked_accounts })}</span>
            </div>
            {presetInfo && <Notice tone="info" role="status">{presetInfo}</Notice>}

            <Check id="sso-ldap-enabled" checked={form.enabled} onChange={set('enabled')} label={t('settings.sso.ldapEnabled')} />

            <Field id="sso-ldap-display-name" label={t('settings.sso.ldapDisplayName')} hint={t('settings.sso.ldapDisplayNameHint')}>
                <TextInput id="sso-ldap-display-name" value={form.display_name} onChange={set('display_name')} maxLength={60} />
            </Field>

            <Field id="sso-ldap-servers" label={t('settings.sso.serverUrls')} hint={t('settings.sso.serverUrlsHint')}>
                <LinesInput id="sso-ldap-servers" value={form.server_urls} onChange={set('server_urls')} rows={2} placeholder={serverPlaceholder} />
            </Field>

            <div className="grid gap-4 md:grid-cols-2">
                <Field id="sso-ldap-security" label={t('settings.sso.security')}>
                    <Select id="sso-ldap-security" value={form.security} onChange={set('security')} options={[
                        { value: 'ldaps', label: t('settings.sso.securityLdaps') },
                        { value: 'starttls', label: t('settings.sso.securityStarttls') },
                        { value: 'none', label: t('settings.sso.securityNone'), disabled: !insecure && form.security !== 'none' },
                    ]} />
                </Field>
                <Field id="sso-ldap-timeout" label={t('settings.sso.timeout')}>
                    <input
                        id="sso-ldap-timeout"
                        type="number"
                        min={2}
                        max={30}
                        value={form.timeout}
                        onChange={(e) => set('timeout')(e.target.value)}
                        className="w-32 px-3 py-2 text-sm"
                    />
                </Field>
            </div>

            <Check
                id="sso-ldap-tls-verify"
                checked={form.tls_verify}
                onChange={set('tls_verify')}
                label={t('settings.sso.tlsVerify')}
                hint={t('settings.sso.tlsVerifyHint')}
                disabled={!insecure && form.tls_verify}
            />

            <Field id="sso-ldap-ca" label={t('settings.sso.caCert')} hint={t('settings.sso.caCertHint')}>
                <LinesInput id="sso-ldap-ca" value={form.ca_cert} onChange={set('ca_cert')} rows={4}
                    placeholder="-----BEGIN CERTIFICATE-----" />
            </Field>

            <div className="grid gap-4 md:grid-cols-2">
                <Field id="sso-ldap-bind-dn" label={t('settings.sso.bindDn')} hint={t('settings.sso.bindDnHint')}>
                    <TextInput id="sso-ldap-bind-dn" value={form.bind_dn} onChange={set('bind_dn')} mono maxLength={500} />
                </Field>
                <SecretField
                    id="sso-ldap-bind-password"
                    label={t('settings.sso.bindPassword')}
                    isSet={form.bind_password_set}
                    unreadable={form.bind_password_unreadable}
                    value={form.bind_password_new}
                    onChange={set('bind_password_new')}
                    remove={form.bind_password_remove}
                    onRemove={set('bind_password_remove')}
                />
            </div>

            <Field id="sso-ldap-base-dn" label={t('settings.sso.userBaseDn')}>
                <TextInput id="sso-ldap-base-dn" value={form.user_base_dn} onChange={set('user_base_dn')} mono maxLength={500}
                    placeholder="OU=Users,DC=example,DC=local" />
            </Field>
            <Field id="sso-ldap-user-filter" label={t('settings.sso.userFilter')} hint={t('settings.sso.userFilterHint')}>
                <TextInput id="sso-ldap-user-filter" value={form.user_filter} onChange={set('user_filter')} mono maxLength={500} />
            </Field>

            <details className="rounded-xl border border-border/60 p-4" open={uniqueUnsafe || undefined}>
                <summary className="cursor-pointer text-sm font-semibold text-text-primary">{t('settings.sso.advancedAttributes')}</summary>
                <div className="grid gap-4 md:grid-cols-2 mt-4">
                    <Field id="sso-ldap-attr-username" label={t('settings.sso.usernameAttr')}>
                        <TextInput id="sso-ldap-attr-username" value={form.username_attr} onChange={set('username_attr')} mono maxLength={64} />
                    </Field>
                    <Field id="sso-ldap-attr-email" label={t('settings.sso.emailAttr')}>
                        <TextInput id="sso-ldap-attr-email" value={form.email_attr} onChange={set('email_attr')} mono maxLength={64} />
                    </Field>
                    <Field id="sso-ldap-attr-name" label={t('settings.sso.nameAttr')}>
                        <TextInput id="sso-ldap-attr-name" value={form.name_attr} onChange={set('name_attr')} mono maxLength={64} />
                    </Field>
                    <Field id="sso-ldap-attr-unique" label={t('settings.sso.uniqueIdAttr')} hint={t('settings.sso.uniqueIdAttrHint')}>
                        <TextInput id="sso-ldap-attr-unique" value={form.unique_id_attr} onChange={setUniqueIdAttr} mono maxLength={64} />
                    </Field>
                </div>
                {uniqueUnsafe && (
                    <div className="space-y-2 mt-4">
                        <Notice tone="warning">
                            {form.unique_id_attr.trim()
                                ? t('settings.sso.uniqueIdConfirmWarning', { attr: form.unique_id_attr.trim(), safe: SAFE_UNIQUE_ID_ATTRS.join(', ') })
                                : t('settings.sso.uniqueIdConfirmWarningDn', { safe: SAFE_UNIQUE_ID_ATTRS.join(', ') })}
                        </Notice>
                        <Check id="sso-ldap-unique-confirm" checked={form.unique_id_attr_confirmed} onChange={set('unique_id_attr_confirmed')}
                            label={t('settings.sso.uniqueIdConfirm')} />
                    </div>
                )}
            </details>

            <Field id="sso-ldap-group-mode" label={t('settings.sso.groupMode')}>
                <Select id="sso-ldap-group-mode" value={form.group_mode} onChange={set('group_mode')} options={[
                    { value: 'memberof', label: t('settings.sso.groupModeMemberOf') },
                    { value: 'search', label: t('settings.sso.groupModeSearch') },
                    { value: 'none', label: t('settings.sso.groupModeNone') },
                ]} />
            </Field>
            {form.group_mode === 'search' && (
                <div className="grid gap-4 md:grid-cols-2">
                    <Field id="sso-ldap-group-base" label={t('settings.sso.groupBaseDn')}>
                        <TextInput id="sso-ldap-group-base" value={form.group_base_dn} onChange={set('group_base_dn')} mono maxLength={500} />
                    </Field>
                    <Field id="sso-ldap-group-filter" label={t('settings.sso.groupFilter')} hint={t('settings.sso.groupFilterHint')}>
                        <TextInput id="sso-ldap-group-filter" value={form.group_filter} onChange={set('group_filter')} mono maxLength={500} />
                    </Field>
                </div>
            )}

            <SsoAccountsBlock kind="ldap" form={form} setForm={setForm} />

            <div className="grid gap-4 md:grid-cols-2">
                <Field id="sso-ldap-test-user" label={t('settings.sso.testUser')}>
                    <TextInput id="sso-ldap-test-user" value={testUser} onChange={setTestUser} maxLength={256} />
                </Field>
                <Field id="sso-ldap-test-password" label={t('settings.sso.testPassword')} hint={t('settings.sso.testPasswordHint')}>
                    <input
                        id="sso-ldap-test-password"
                        type="password"
                        value={testPassword}
                        onChange={(e) => setTestPassword(e.target.value)}
                        className="w-full px-3 py-2 text-sm"
                        autoComplete="new-password"
                        maxLength={1024}
                    />
                </Field>
            </div>

            <p className="text-xs text-text-muted">{t('settings.sso.testUsesForm')}</p>
            {stepUpHint && <p className="text-xs text-text-muted">{stepUpHint}</p>}
            {blockers.length > 0 && <Notice tone="warning">{blockers.map((k) => <p key={k}>{t(k)}</p>)}</Notice>}
            <SaveBar busy={busy} saveKey="ldap" testKey="test-ldap" onSave={onSave} onTest={onTest} saveDisabled={blockers.length > 0} />
            <SsoTestResult result={test} target="ldap" />
            <WarningList warnings={warnings} />
        </Card>
    )
}
