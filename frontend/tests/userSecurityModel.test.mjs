// Reine Helfer der Benutzerverwaltung (WS-F2F3): src/components/userSecurity/userSecurityModel.js
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    createUserPayload, isLocalAccount, orphanZones, resetLinkState, securityBadges,
} from '../src/components/userSecurity/userSecurityModel.js'

test('isLocalAccount: fehlende auth_source gilt als lokal', () => {
    assert.equal(isLocalAccount({}), true)
    assert.equal(isLocalAccount({ auth_source: 'local' }), true)
    assert.equal(isLocalAccount({ auth_source: 'oidc' }), false)
    assert.equal(isLocalAccount(null), true)
})

test('resetLinkState: E-Mail vor SMTP vor aktivem Konto', () => {
    assert.deepEqual(resetLinkState({ email: '', is_active: true }, true), { enabled: false, reason: 'no_email' })
    assert.deepEqual(resetLinkState({ email: 'a@b.c', is_active: true }, false), { enabled: false, reason: 'no_smtp' })
    assert.deepEqual(resetLinkState({ email: 'a@b.c', is_active: false }, true), { enabled: false, reason: 'inactive' })
    assert.deepEqual(resetLinkState({ email: 'a@b.c', is_active: true }, true), { enabled: true, reason: null })
})

test('securityBadges: Reihenfolge und unlesbares 2FA', () => {
    assert.deepEqual(securityBadges({}), [])
    assert.deepEqual(
        securityBadges({ must_change_password: true, totp_enabled: true, passkey_count: 2 }).map((b) => b.key),
        ['mustChange', '2fa', 'passkeys'],
    )
    const [b] = securityBadges({ totp_enabled: true, totp_unreadable: true })
    assert.equal(b.key, 'totpUnreadable')
    assert.equal(b.tone, 'danger')
    assert.equal(securityBadges({ passkey_count: 3 })[0].count, 3)
})

test('orphanZones: nur Zuordnungen ohne geladene Zone, Vergleich normalisiert', () => {
    const all = [{ name: 'a.example.', server: 'ns1' }, { name: 'B.example.', server: 'ns1' }]
    assert.deepEqual(orphanZones(['a.example.', 'b.example', 'gone.example.'], all), ['gone.example.'])
    assert.deepEqual(orphanZones([], all), [])
    assert.deepEqual(orphanZones(['x.'], []), ['x.'])
})

test('createUserPayload: leere E-Mail wird nicht gesendet, Flag bleibt', () => {
    const p = createUserPayload({ username: ' anna ', password: 'pw-12345', email: '  ', role: 'user', must_change_password: true })
    assert.equal(p.username, 'anna')
    assert.equal('email' in p, false)
    assert.equal(p.must_change_password, true)
    assert.equal(p.display_name, undefined)
    const q = createUserPayload({ username: 'b', password: 'x', email: ' b@example.org ', role: 'admin', must_change_password: false })
    assert.equal(q.email, 'b@example.org')
    assert.equal(q.role, 'admin')
    assert.equal(q.must_change_password, false)
})
