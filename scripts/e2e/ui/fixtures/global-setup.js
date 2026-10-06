// Vor allen Specs: Backend erreichbar? Admin-Sitzung als storageState ablegen (Standard fuer alle Tests).
const fs = require('node:fs')
const path = require('node:path')
const { request } = require('@playwright/test')
const { BASE_URL, ADMIN_STATE, ADMIN_PASSWORD } = require('./env')
const { PanelApi } = require('./api')

async function waitHealthy(url, timeoutMs) {
  const ctx = await request.newContext()
  const until = Date.now() + timeoutMs
  let last = ''
  try {
    while (Date.now() < until) {
      try {
        const res = await ctx.get(`${url}/health`, { timeout: 5000 })
        if (res.ok()) return
        last = `HTTP ${res.status()}`
      } catch (err) {
        last = err.message
      }
      await new Promise((r) => setTimeout(r, 1000)) // Abfrage-Intervall, keine feste Testwartezeit
    }
  } finally {
    await ctx.dispose()
  }
  throw new Error(`${url}/health nicht erreichbar: ${last}`)
}

module.exports = async () => {
  if (!ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD fehlt – scripts/e2e/ui/run.sh benutzen')
  await waitHealthy(BASE_URL, 120_000)
  const admin = await PanelApi.admin()
  try {
    fs.mkdirSync(path.dirname(ADMIN_STATE), { recursive: true })
    const state = await admin.storageState()
    fs.writeFileSync(ADMIN_STATE, JSON.stringify(state), { mode: 0o600 })
  } finally {
    await admin.dispose()
  }
}
