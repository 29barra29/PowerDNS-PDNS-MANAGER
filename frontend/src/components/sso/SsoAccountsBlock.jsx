import { useTranslation } from 'react-i18next'
import { Check, Field, LinesInput, Notice, Select } from './SsoFields'
import { invalidGroupDns, jitNeedsConfirmation } from './ssoModel'

// Block "Konten und Rechte" (OIDC und LDAP, F10 §2.5) mit den Plan-Regeln:
//   [S5]  JIT ohne Gruppen (bzw. bei OIDC ohne E-Mail-Domains) nur mit Bestaetigung jit_allow_any_account
//   [S1]  LDAP-Gruppen nur als vollstaendige DN (Hinweis settings.sso.allowedGroupsHint, Pruefung je Zeile)
export default function SsoAccountsBlock({ kind, form, setForm, disabled }) {
    const { t } = useTranslation()
    const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }))
    const p = `sso-${kind}`
    const groupsHint = kind === 'ldap' ? t('settings.sso.allowedGroupsHint') : t('settings.sso.allowedGroupsHintOidc')
    const badAllowed = kind === 'ldap' ? invalidGroupDns(form.allowed_groups) : []
    const badAdmin = kind === 'ldap' ? invalidGroupDns(form.admin_groups) : []
    const needsConfirm = jitNeedsConfirmation(kind, form)

    return (
        <fieldset className="space-y-4 rounded-xl border border-border/60 p-4" disabled={disabled}>
            <legend className="px-1 text-sm font-semibold text-text-primary">{t('settings.sso.accountsTitle')}</legend>

            <Check id={`${p}-jit`} checked={form.jit_enabled} onChange={set('jit_enabled')}
                label={t('settings.sso.jitEnabled')} hint={t('settings.sso.jitEnabledHint')} />
            <Check id={`${p}-linking`} checked={form.allow_linking} onChange={set('allow_linking')}
                label={t('settings.sso.allowLinking')} hint={t('settings.sso.allowLinkingHint')} />

            <Field id={`${p}-allowed-groups`} label={t('settings.sso.allowedGroups')} hint={`${t('settings.sso.allowedGroupsEmpty')} ${groupsHint}`}>
                <LinesInput id={`${p}-allowed-groups`} value={form.allowed_groups} onChange={set('allowed_groups')} />
            </Field>
            {badAllowed.length > 0 && (
                <Notice tone="warning">{t('settings.sso.groupDnInvalid', { groups: badAllowed.join('; ') })}</Notice>
            )}

            {kind === 'oidc' && (
                <Field id={`${p}-domains`} label={t('settings.sso.allowedEmailDomains')} hint={t('settings.sso.allowedEmailDomainsHint')}>
                    <LinesInput id={`${p}-domains`} value={form.allowed_email_domains} onChange={set('allowed_email_domains')} rows={2} />
                </Field>
            )}

            {needsConfirm && (
                <div className="space-y-2">
                    <Notice tone="warning">{t('settings.sso.jitOpenWarning')}</Notice>
                    <Check id={`${p}-jit-any`} checked={form.jit_allow_any_account} onChange={set('jit_allow_any_account')}
                        label={t('settings.sso.jitAllowAny')} hint={t('settings.sso.jitAllowAnyHint')} />
                </div>
            )}

            <Field id={`${p}-admin-groups`} label={t('settings.sso.adminGroups')} hint={groupsHint}>
                <LinesInput id={`${p}-admin-groups`} value={form.admin_groups} onChange={set('admin_groups')} rows={2} />
            </Field>
            {badAdmin.length > 0 && (
                <Notice tone="warning">{t('settings.sso.groupDnInvalid', { groups: badAdmin.join('; ') })}</Notice>
            )}

            <Field id={`${p}-role-mode`} label={t('settings.sso.roleMode')} hint={t('settings.sso.roleModeHint')}>
                <Select id={`${p}-role-mode`} value={form.role_mode} onChange={set('role_mode')} options={[
                    { value: 'off', label: t('settings.sso.roleModeOff') },
                    { value: 'promote', label: t('settings.sso.roleModePromote') },
                    { value: 'sync', label: t('settings.sso.roleModeSync') },
                ]} />
            </Field>
        </fieldset>
    )
}
