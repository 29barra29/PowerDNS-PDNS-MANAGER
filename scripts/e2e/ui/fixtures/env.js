// Laufzeit-Parameter der UI-Smoke-Tests (gesetzt von compose.ui.yaml bzw. run.sh).
const path = require('node:path')

const OUT = process.env.E2E_UI_OUT || path.join(__dirname, '..', 'out')

module.exports = {
  OUT,
  BASE_URL: (process.env.E2E_BASE_URL || 'http://backend:8000').replace(/\/+$/, ''),
  // Zweite Backend-Instanz mit leerer Datenbank (Einrichtungsassistent); leer = nicht verfuegbar
  SETUP_URL: (process.env.E2E_SETUP_URL || '').replace(/\/+$/, ''),
  RECEIVER_URL: (process.env.E2E_RECEIVER_URL || 'http://receiver:8080').replace(/\/+$/, ''),
  ADMIN_USER: process.env.E2E_ADMIN_USER || 'admin',
  ADMIN_PASSWORD: process.env.E2E_ADMIN_PASSWORD || '',
  // Specs fuer Welle-3-UI, die beim Schreiben noch nicht integriert war (F15, F5-FE, F4-C)
  PENDING: ['1', 'true', 'yes'].includes(String(process.env.E2E_UI_PENDING || '').toLowerCase()),
  ADMIN_STATE: path.join(OUT, 'state', 'admin.json'),
  // PowerDNS-Server im E2E-Netz (Panel-Namen)
  SERVERS: ['ns1', 'ns2'],
  // Direktzugang zu PowerDNS: "ns1=http://pdns1:8081|key,ns2=..."
  PDNS: Object.fromEntries(String(process.env.E2E_PDNS || '').split(',').filter(Boolean).map((entry) => {
    const [name, rest] = entry.split('=')
    const [url, key] = String(rest || '').split('|')
    return [name.trim(), { url: (url || '').replace(/\/+$/, ''), key: key || '' }]
  })),
}
