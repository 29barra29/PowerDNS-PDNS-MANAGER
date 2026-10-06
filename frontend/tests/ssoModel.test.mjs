// Reine SSO-Helfer (WS-F10-APP-FE): src/components/sso/ssoModel.js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    DEFAULT_PROVIDERS, LDAP_PRESETS, OIDC_PRESETS, SENSITIVE_FIELDS, applyPreset, authSourceKey,
    changedSensitiveFields, invalidGroupDns, isSafeUniqueIdAttr, jitNeedsConfirmation, ldapFormFromSettings,
    ldapPayload, loginVisibility, normalizeProviders, oidcFormFromSettings, oidcPayload, readLoginUrlFlags,
    safeAuthorizationUrl, secretPayload, sectionBlockers, sessionLifetimeHours, splitDn, ssoErrorKey,
} from '../src/components/sso/ssoModel.js'

test('ssoErrorKey: bekannte Codes, totp_unreadable, unbekannt -> generic', () => {
    assert.equal(ssoErrorKey('idp_error'), 'login.ssoError.idpError')
    assert.equal(ssoErrorKey('id_token'), 'login.ssoError.idToken')
    assert.equal(ssoErrorKey('totp_unreadable'), 'login.ssoError.totp_unreadable')
    assert.equal(ssoErrorKey('xyz'), 'login.ssoError.generic')
    assert.equal(ssoErrorKey('constructor'), 'login.ssoError.generic')
    assert.equal(ssoErrorKey(''), 'login.ssoError.generic')
})

test('readLoginUrlFlags: Parameter werden bereinigt, local/sso_2fa nur mit "1"', () => {
    const f = readLoginUrlFlags('?sso_error=idp_error&sso_detail=access_denied%3Cscript%3E&sso_2fa=1&local=1')
    assert.equal(f.ssoError, 'idp_error')
    assert.equal(f.ssoDetail, 'access_deniedscript')
    assert.equal(f.sso2fa, true)
    assert.equal(f.local, true)
    assert.equal(f.hadParams, true)
    const none = readLoginUrlFlags('')
    assert.deepEqual([none.ssoError, none.sso2fa, none.local, none.hadParams], ['', false, false, false])
    assert.equal(readLoginUrlFlags('?local=true').local, false)
    assert.equal(readLoginUrlFlags('?sso_detail=' + 'a'.repeat(80)).ssoDetail.length, 40)
})

test('normalizeProviders: nur start_url unter /api/, Defaults bei Fehlern', () => {
    assert.deepEqual(normalizeProviders(null), {
        local_login_enabled: true, providers: [], ldap: { enabled: false, label: '' }, linking: { oidc: false, ldap: false },
    })
    const p = normalizeProviders({
        local_login_enabled: false,
        providers: [
            { id: 'oidc', type: 'oidc', label: 'Firma', start_url: '/api/v1/auth/oidc/start' },
            { id: 'evil', type: 'oidc', label: 'X', start_url: 'https://evil.example/' },
        ],
        ldap: { enabled: true, label: 'Windows' },
        linking: { oidc: true },
    })
    assert.equal(p.local_login_enabled, false)
    assert.deepEqual(p.providers.map((x) => x.id), ['oidc'])
    assert.deepEqual(p.ldap, { enabled: true, label: 'Windows' })
    assert.deepEqual(p.linking, { oidc: true, ldap: false })
})

test('loginVisibility: Sichtbarkeitsvarianten F10 §2.1', () => {
    const oidc = { id: 'oidc', type: 'oidc', label: 'SSO', start_url: '/api/v1/auth/oidc/start' }
    // wie 2.4.1
    let v = loginVisibility(DEFAULT_PROVIDERS, {}, { forgot_password_enabled: true })
    assert.equal(v.showForm, true)
    assert.equal(v.showSsoButtons, false)
    assert.equal(v.showPasskey, true)
    assert.equal(v.showForgot, true)
    assert.equal(v.showEmergencyLink, false)
    // nur SSO, lokal aus, kein LDAP -> Primaer-Button, Notfall-Link, kein Passkey/Passwort vergessen
    const ssoOnly = { local_login_enabled: false, providers: [oidc], ldap: { enabled: false }, linking: {} }
    v = loginVisibility(ssoOnly, {}, { forgot_password_enabled: true })
    assert.equal(v.ssoPrimary, true)
    assert.equal(v.showForm, false)
    assert.equal(v.showEmergencyLink, true)
    assert.equal(v.showPasskey, false)
    assert.equal(v.showForgot, false)
    // Notfallzugang ?local=1
    v = loginVisibility(ssoOnly, { local: true }, { forgot_password_enabled: true })
    assert.equal(v.showForm, true)
    assert.equal(v.ssoPrimary, false)
    assert.equal(v.showBackToSso, true)
    assert.equal(v.showLocalModeHint, true)
    assert.equal(v.showPasskey, true)
    assert.equal(v.showForgot, true)
    // lokal aus, LDAP an -> Formular (LDAP) ohne Passkey, SSO-Button sekundaer
    v = loginVisibility({ ...ssoOnly, ldap: { enabled: true, label: 'AD' } }, {}, {})
    assert.equal(v.showForm, true)
    assert.equal(v.showLdapHint, true)
    assert.equal(v.ssoPrimary, false)
    assert.equal(v.showDivider, true)
    assert.equal(v.showPasskey, false)
    // 2FA-Schritt nach SSO: keine Buttons/Trenner
    v = loginVisibility({ ...ssoOnly, local_login_enabled: true }, { sso2fa: true }, {})
    assert.equal(v.showSsoButtons, false)
    assert.equal(v.showDivider, false)
})

test('isSafeUniqueIdAttr: Allowlist case-insensitiv, leer = unsicher', () => {
    assert.equal(isSafeUniqueIdAttr('objectGUID'), true)
    assert.equal(isSafeUniqueIdAttr('objectguid'), true)
    assert.equal(isSafeUniqueIdAttr('ipaUniqueID'), true)
    assert.equal(isSafeUniqueIdAttr('sAMAccountName'), false)
    assert.equal(isSafeUniqueIdAttr(''), false)
})

test('splitDn/invalidGroupDns: vollstaendige DN mit mind. zwei RDNs [S1]', () => {
    assert.deepEqual(splitDn('CN=a\\,b,OU=Groups'), ['CN=a\\,b', 'OU=Groups'])
    assert.equal(splitDn('pdns-admins'), null)
    assert.deepEqual(invalidGroupDns('CN=pdns-admins,OU=Groups,DC=example,DC=com\nCN=x\npdns\n\n'), ['CN=x', 'pdns'])
    assert.deepEqual(invalidGroupDns(''), [])
})

test('Formular <-> Payload OIDC: Gruppen als Liste, Secret behalten/entfernen/neu', () => {
    const form = oidcFormFromSettings({
        enabled: true, display_name: 'Firma', issuer: 'https://sso.example.com/realms/x', client_id: 'pdns',
        client_secret: '••••••••', client_secret_set: true, allowed_groups: ['a', 'b'], admin_groups: [],
        allowed_email_domains: ['example.com'], jit_enabled: true, jit_allow_any_account: false,
    })
    assert.equal(form.allowed_groups, 'a\nb')
    let body = oidcPayload(form)
    assert.ok(!('client_secret' in body), 'gespeichertes Secret wird nicht gesendet')
    assert.deepEqual(body.allowed_groups, ['a', 'b'])
    assert.deepEqual(body.allowed_email_domains, ['example.com'])
    body = oidcPayload({ ...form, client_secret_remove: true })
    assert.equal(body.client_secret, '')
    body = oidcPayload({ ...form, client_secret_new: '  s3cret ' })
    assert.equal(body.client_secret, '  s3cret ')
    assert.equal(secretPayload('   ', false), undefined)
})

test('ldapPayload: unique_id_attr_confirmed nur bei unsicherem Attribut [S16]', () => {
    const base = ldapFormFromSettings({ unique_id_attr: 'objectGUID', unique_id_attr_confirmed: false, timeout: 8, server_urls: ['ldaps://a:636'] })
    assert.ok(!('unique_id_attr_confirmed' in ldapPayload(base)))
    assert.deepEqual(ldapPayload(base).server_urls, ['ldaps://a:636'])
    const unsafe = { ...base, unique_id_attr: 'sAMAccountName', unique_id_attr_confirmed: true }
    assert.equal(ldapPayload(unsafe).unique_id_attr_confirmed, true)
    assert.equal(ldapPayload({ ...base, timeout: '12' }).timeout, 12)
    assert.equal(ldapPayload({ ...base, timeout: 'x' }).timeout, 8)
})

test('JIT-Gate und Blocker [S5, S16]', () => {
    const oidc = { jit_enabled: true, allowed_groups: '', allowed_email_domains: '', jit_allow_any_account: false }
    assert.equal(jitNeedsConfirmation('oidc', oidc), true)
    assert.deepEqual(sectionBlockers('oidc', oidc), ['settings.sso.jitAllowAnyRequired'])
    assert.equal(jitNeedsConfirmation('oidc', { ...oidc, allowed_email_domains: 'example.com' }), false)
    assert.equal(jitNeedsConfirmation('ldap', { ...oidc, allowed_email_domains: 'example.com' }), true)
    assert.deepEqual(sectionBlockers('oidc', { ...oidc, jit_allow_any_account: true }), [])
    assert.equal(jitNeedsConfirmation('oidc', { ...oidc, jit_enabled: false }), false)
    const ldap = { jit_enabled: false, unique_id_attr: 'uid', unique_id_attr_confirmed: false }
    assert.deepEqual(sectionBlockers('ldap', ldap), ['settings.sso.uniqueIdConfirmRequired'])
    assert.deepEqual(sectionBlockers('ldap', { ...ldap, unique_id_attr_confirmed: true }), [])
})

test('changedSensitiveFields: nur sensible, tatsaechlich geaenderte Felder [S8]', () => {
    const saved = { issuer: 'https://a', client_id: 'x', scopes: 'openid', allowed_groups: ['g'], enabled: true }
    assert.deepEqual(changedSensitiveFields('oidc', saved, { issuer: 'https://a', client_id: 'x', scopes: 'openid email', allowed_groups: ['g'], enabled: true }), [])
    assert.deepEqual(changedSensitiveFields('oidc', saved, { issuer: 'https://b', allowed_groups: ['g', 'h'], enabled: true }), ['issuer', 'allowed_groups'])
    assert.deepEqual(changedSensitiveFields('general', { local_login_enabled: true }, { local_login_enabled: false, require_totp: false }), ['local_login_enabled'])
    assert.ok(!SENSITIVE_FIELDS.oidc.includes('client_secret'), 'reines Secret-Rotieren ist nicht sensibel')
    // L-2 (WS-W3-NACHARBEIT): "2FA nach SSO" abschalten nur mit Step-up – wie im Backend
    assert.deepEqual(changedSensitiveFields('general', { local_login_enabled: true, require_totp: true }, { local_login_enabled: true, require_totp: false }), ['require_totp'])
})

test('sessionLifetimeHours: Warnung erst ueber 24 h (E-F10-2)', () => {
    assert.equal(sessionLifetimeHours(86400), null)
    assert.equal(sessionLifetimeHours(undefined), null)
    assert.equal(sessionLifetimeHours(7 * 86400), 168)
})

test('Vorlagen: Authentik, Keycloak, Active Directory (+ weitere) setzen nur Vorlagenfelder', () => {
    for (const id of ['generic', 'keycloak', 'authentik', 'entra']) assert.ok(OIDC_PRESETS[id], id)
    for (const id of ['ad', 'openldap', 'freeipa']) assert.ok(LDAP_PRESETS[id], id)
    const f = applyPreset('oidc', 'authentik', { issuer: 'https://keep', groups_claim: '' })
    assert.equal(f.issuer, 'https://keep')
    assert.equal(f.groups_claim, 'groups')
    const l = applyPreset('ldap', 'ad', { unique_id_attr: 'x', unique_id_attr_confirmed: true, server_urls: 'ldaps://dc:636' })
    assert.equal(l.unique_id_attr, 'objectGUID')
    assert.equal(l.unique_id_attr_confirmed, false)
    assert.equal(l.server_urls, 'ldaps://dc:636')
    assert.match(l.user_filter, /objectCategory=person/)
    for (const p of Object.values(LDAP_PRESETS)) assert.ok(isSafeUniqueIdAttr(p.values.unique_id_attr))
    assert.equal(applyPreset('oidc', 'nope', f), f)
})

test('safeAuthorizationUrl und authSourceKey', () => {
    assert.equal(safeAuthorizationUrl('https://idp.example/auth?x=1'), 'https://idp.example/auth?x=1')
    assert.equal(safeAuthorizationUrl('javascript:alert(1)'), null)
    assert.equal(safeAuthorizationUrl(''), null)
    assert.equal(authSourceKey('ldap'), 'settings.authSource.ldap')
    assert.equal(authSourceKey('oidc'), 'settings.authSource.oidc')
    assert.equal(authSourceKey(undefined), 'settings.authSource.local')
})
