// Step-up-Zustand (WS-F10-APP-FE, Plan [S8]): src/components/sso/stepUpStore.js
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import {
    STEP_UP_RETURN_KEY, _resetStepUpForTests, cancelStepUp, getStepUpState, isSafeReturnPath, isStepUpAbort,
    openStepUp, requestStepUp, saveReturnPath, submitStepUp, subscribeStepUp, takeReturnPath, withStepUp,
} from '../src/components/sso/stepUpStore.js'

beforeEach(() => _resetStepUpForTests())

const tick = () => new Promise((r) => setTimeout(r, 0))

function httpError(code, message = code) {
    const err = new Error(message)
    err.code = code
    err.status = 403
    return err
}

test('openStepUp/requestStepUp/submit: Waiter bekommen die Eingabe, Dialog schliesst', async () => {
    let calls = 0
    const unsub = subscribeStepUp(() => { calls++ })
    openStepUp({ code: 'stepup_required' })
    assert.equal(getStepUpState().open, true)
    const p = requestStepUp()
    submitStepUp({ current_password: 'pw', totp_code: '123 456' })
    assert.deepEqual(await p, { current_password: 'pw', totp_code: '123456' })
    assert.equal(getStepUpState().open, false)
    assert.ok(calls >= 3)
    unsub()
})

test('withStepUp: erster Versuch ohne, nach stepup_required mit Eingabe, stepup_failed fragt erneut', async () => {
    const seen = []
    const fn = async (creds) => {
        seen.push(creds)
        if (seen.length === 1) throw httpError('stepup_required')
        if (seen.length === 2) throw httpError('stepup_failed', 'Passwort ist falsch')
        return 'ok'
    }
    const p = withStepUp(fn)
    await tick()
    assert.equal(getStepUpState().open, true)
    submitStepUp({ current_password: 'falsch' })
    await tick()
    assert.equal(getStepUpState().error, 'Passwort ist falsch')
    submitStepUp({ current_password: 'richtig', totp_code: '000111' })
    assert.equal(await p, 'ok')
    assert.deepEqual(seen, [null, { current_password: 'falsch' }, { current_password: 'richtig', totp_code: '000111' }])
})

test('withStepUp proactive: fragt vorab; Abbruch -> stepup_cancelled ohne API-Aufruf', async () => {
    let called = 0
    const p = withStepUp(async () => { called++; return 1 }, { proactive: true })
    await tick()
    cancelStepUp()
    await assert.rejects(p, (err) => isStepUpAbort(err))
    assert.equal(called, 0)

    const p2 = withStepUp(async (creds) => creds, { proactive: true })
    await tick()
    submitStepUp({ current_password: 'pw' })
    assert.deepEqual(await p2, { current_password: 'pw' })
})

test('withStepUp: reauth_required wird markiert durchgereicht, andere Fehler unveraendert', async () => {
    await assert.rejects(withStepUp(async () => { throw httpError('reauth_required') }), (err) => err.stepUpHandled === true)
    assert.equal(getStepUpState().code, 'reauth_required')
    cancelStepUp()
    const other = new Error('kaputt')
    await assert.rejects(withStepUp(async () => { throw other }), (err) => err === other)
})

test('withStepUp: Wiederholungen begrenzt', async () => {
    const p = withStepUp(async () => { throw httpError('stepup_failed') }, { maxAttempts: 1 })
    await tick()
    submitStepUp({ current_password: 'x' })
    await assert.rejects(p, (err) => err.code === 'stepup_failed')
})

test('Rueckkehrpfad: nur interne Pfade, nicht /login, Ablauf nach 15 min', () => {
    assert.equal(isSafeReturnPath('/settings?tab=sso'), true)
    assert.equal(isSafeReturnPath('//evil.example'), false)
    assert.equal(isSafeReturnPath('https://evil.example'), false)
    assert.equal(isSafeReturnPath('/\\evil'), false)
    assert.equal(isSafeReturnPath('/login?local=1'), false)
    assert.equal(isSafeReturnPath('/loginx'), true)

    const store = new Map()
    const storage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) }
    assert.equal(saveReturnPath(storage, '/settings?tab=sso', 1000), true)
    assert.equal(takeReturnPath(storage, 2000), '/settings?tab=sso')
    assert.equal(store.has(STEP_UP_RETURN_KEY), false, 'Eintrag wird verbraucht')
    saveReturnPath(storage, '/users', 0)
    assert.equal(takeReturnPath(storage, 16 * 60 * 1000), null)
    assert.equal(saveReturnPath(storage, '//x', 0), false)
    store.set(STEP_UP_RETURN_KEY, '{kaputt')
    assert.equal(takeReturnPath(storage, 0), null)
    assert.equal(takeReturnPath(null), null)
    const throwing = { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') }, removeItem() {} }
    assert.equal(saveReturnPath(throwing, '/x'), false)
    assert.equal(takeReturnPath(throwing), null)
})
