// Reine Helfer der Panel-Token-Verwaltung (src/lib/panelTokens.js, F14 §6.3) und API-Modul f14-app.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
    DEFAULT_EXPIRY_DAYS, buildCreatePayload, buildUpdatePayload, daysUntil, filterZoneOptions, initialForm,
    isBroad, isValidZone, mergeZoneNames, normalizeZoneInput, tokenView, validateForm, zoneOptionsFromPermissions,
} from '../src/lib/panelTokens.js'
import f14, { overrides } from '../src/api/f14-app.js'

const DAY = 86400000

test('normalizeZoneInput / isValidZone wie das Backend', () => {
    assert.equal(normalizeZoneInput('  Example.COM '), 'example.com.')
    assert.equal(normalizeZoneInput('a.example.'), 'a.example.')
    assert.equal(normalizeZoneInput('   '), '')
    assert.ok(isValidZone('example.com'))
    assert.ok(isValidZone('_acme.example.com.'))
    for (const bad of ['', 'foo bar', '*.x.de', 'a/b.de', 'x'.repeat(300), '-a.de']) {
        assert.equal(isValidZone(bad), false, bad)
    }
})

test('daysUntil / isBroad / tokenView', () => {
    const now = Date.parse('2026-10-06T12:00:00Z')
    assert.equal(daysUntil('2026-10-08T12:00:00+00:00', now), 2)
    assert.equal(daysUntil(null, now), null)
    assert.equal(daysUntil('kaputt', now), null)
    assert.ok(isBroad({ scope_zones: null, expires_at: null }))
    assert.ok(!isBroad({ scope_zones: ['a.'], expires_at: null }))
    assert.ok(!isBroad({ scope_zones: null, expires_at: '2027-01-01T00:00:00+00:00' }))

    const soon = tokenView({ status: 'active', scope_zones: ['a.'], expires_at: new Date(now + 3 * DAY).toISOString(),
        allow_admin: false, inaccessible_zones: ['a.'] }, now)
    assert.deepEqual(soon, { status: 'active', broad: false, expiresSoon: 3, admin: null, inaccessible: ['a.'] })
    assert.equal(tokenView({ status: 'active', expires_at: new Date(now + 30 * DAY).toISOString() }, now).expiresSoon, null)
    // abgelaufen/pausiert: kein "laeuft bald ab"
    assert.equal(tokenView({ status: 'expired', expires_at: new Date(now - DAY).toISOString() }, now).expiresSoon, null)
    assert.equal(tokenView({ status: 'paused', expires_at: new Date(now + DAY).toISOString() }, now).expiresSoon, null)
    assert.equal(tokenView({ allow_admin: true, admin_effective: true }).admin, 'active')
    assert.equal(tokenView({ allow_admin: true, admin_effective: false }).admin, 'inactive')
    assert.equal(tokenView({ status: 'revoked' }).status, 'active') // unbekannt -> neutral
})

test('Zonen-Optionen: Vereinigung, Rechte, Filter, Limit', () => {
    assert.deepEqual(mergeZoneNames(['B.example', 'a.example.'], ['a.example', '', null]), ['a.example.', 'b.example.'])
    assert.deepEqual(zoneOptionsFromPermissions({ 'Z.example': 'read', 'a.example.': 'manage', 'z.example.': 'manage' }), [
        { name: 'a.example.', readOnly: false },
        { name: 'z.example.', readOnly: false },
    ])
    assert.deepEqual(zoneOptionsFromPermissions({ 'r.example.': 'read' }), [{ name: 'r.example.', readOnly: true }])
    assert.deepEqual(zoneOptionsFromPermissions(null), [])
    const opts = Array.from({ length: 12 }, (_, i) => ({ name: `z${i}.example.` }))
    assert.deepEqual(filterZoneOptions(opts, 'z1', 2), { shown: [opts[1], opts[10]], rest: 1 })
    assert.equal(filterZoneOptions(opts, '', 5).rest, 7)
})

test('initialForm / validateForm', () => {
    const create = initialForm('create')
    assert.deepEqual(create, { name: '', scopeMode: 'selected', zones: [], permission: 'manage',
        expiry: String(DEFAULT_EXPIRY_DAYS), allowAdmin: false })
    assert.equal(validateForm(create), 'panelTokens.nameRequired')
    assert.equal(validateForm({ ...create, name: 'x' }), 'panelTokens.zonesSelectAtLeastOne')
    assert.equal(validateForm({ ...create, name: 'x', zones: ['a.'] }), '')
    assert.equal(validateForm({ ...create, name: 'x', scopeMode: 'all' }), '')
    const edit = initialForm('edit', { name: 'ci', scope_zones: ['b.', 'a.'], permission: 'read', allow_admin: false })
    assert.deepEqual(edit, { name: 'ci', scopeMode: 'selected', zones: ['a.', 'b.'], permission: 'read',
        expiry: 'unchanged', allowAdmin: false })
    assert.equal(initialForm('edit', { name: 'x', scope_zones: null }).scopeMode, 'all')
})

test('buildCreatePayload', () => {
    assert.deepEqual(buildCreatePayload({ name: ' ci ', scopeMode: 'selected', zones: ['B.example', 'a.example.'],
        permission: 'read', expiry: '30', allowAdmin: true }), {
        name: 'ci', scope_zones: ['a.example.', 'b.example.'], permission: 'read', expires_in_days: 30, allow_admin: false,
    })
    assert.deepEqual(buildCreatePayload({ name: 'adm', scopeMode: 'all', zones: ['x.'], permission: 'manage',
        expiry: 'never', allowAdmin: true }), {
        name: 'adm', scope_zones: null, permission: 'manage', expires_in_days: null, allow_admin: true,
    })
})

test('buildUpdatePayload sendet nur Unterschiede', () => {
    const tok = { name: 'ci', scope_zones: ['a.example.'], permission: 'manage', allow_admin: false, expires_at: null }
    const form = initialForm('edit', tok)
    assert.deepEqual(buildUpdatePayload(tok, form), {})
    assert.deepEqual(buildUpdatePayload(tok, { ...form, name: ' ci ' }), {})
    assert.deepEqual(buildUpdatePayload(tok, { ...form, name: 'neu', permission: 'read' }), { name: 'neu', permission: 'read' })
    assert.deepEqual(buildUpdatePayload(tok, { ...form, zones: ['b.example', 'a.example.'] }),
        { scope_zones: ['a.example.', 'b.example.'] })
    assert.deepEqual(buildUpdatePayload(tok, { ...form, scopeMode: 'all', allowAdmin: true }),
        { scope_zones: null, allow_admin: true })
    assert.deepEqual(buildUpdatePayload(tok, { ...form, expiry: 'never' }), { expires_in_days: null })
    assert.deepEqual(buildUpdatePayload(tok, { ...form, expiry: '7' }), { expires_in_days: 7 })
    // Admin-Token auf Zonen einschraenken -> Freigabe faellt weg
    const adm = { name: 'a', scope_zones: null, permission: 'manage', allow_admin: true }
    assert.deepEqual(buildUpdatePayload(adm, { ...initialForm('edit', adm), scopeMode: 'selected', zones: ['x.'] }),
        { scope_zones: ['x.'], allow_admin: false })
    // Altlast: degradierter Admin, Scope unveraendert -> allow_admin bleibt (kein Feld)
    assert.deepEqual(buildUpdatePayload(adm, { ...initialForm('edit', adm), name: 'b' }), { name: 'b' })
})

test('API-Modul f14-app: Ueberschreibungen und Pfade', async () => {
    assert.deepEqual(overrides, ['listPanelTokens', 'createPanelToken', 'deletePanelToken'])
    const calls = []
    const client = { request: async (...args) => { calls.push(args); return {} }, ...f14 }
    await client.listPanelTokens()
    await client.createPanelToken({ name: ' x ', scope_zones: ['b.', 'a.', 'a.'], expires_in_days: '30', permission: 'x' })
    await client.updatePanelToken(7, { is_active: false })
    await client.deletePanelToken('7/x')
    await client.getUserPanelTokens(5)
    await client.revokeUserPanelToken(5, 9)
    await client.revokeAllUserPanelTokens(5)
    assert.deepEqual(calls.map((c) => [c[0], c[1]]), [
        ['GET', '/auth/me/panel-tokens'],
        ['POST', '/auth/me/panel-tokens'],
        ['PUT', '/auth/me/panel-tokens/7'],
        ['DELETE', '/auth/me/panel-tokens/7%2Fx'],
        ['GET', '/auth/users/5/panel-tokens'],
        ['DELETE', '/auth/users/5/panel-tokens/9'],
        ['DELETE', '/auth/users/5/panel-tokens'],
    ])
    assert.deepEqual(calls[1][2], { name: 'x', scope_zones: ['a.', 'b.'], permission: 'manage', expires_in_days: 30,
        allow_admin: false })
    assert.deepEqual(calls[2][2], { is_active: false })
})
