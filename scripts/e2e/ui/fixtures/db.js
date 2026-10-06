// Direkter Zugriff auf die Panel-Datenbank des E2E-Stacks (wie ctx.db in scripts/e2e/checks), nur fuer Zustaende,
// die sich ueber die API nicht herstellen lassen: externes SSO-Konto, unlesbares Geheimnis, abgelaufener Token.
// Zugangsdaten sind die Testwerte aus compose.e2e.yaml (internes Netz), gesetzt in compose.ui.yaml.
const mysql = require('mysql2/promise')

async function db(sql, params = []) {
  const conn = await mysql.createConnection({
    host: process.env.E2E_DB_HOST || 'db',
    port: Number(process.env.E2E_DB_PORT || 3306),
    user: process.env.E2E_DB_USER || 'root',
    password: process.env.E2E_DB_PASSWORD || '',
    database: process.env.E2E_DB_NAME || 'dns_manager',
  })
  try {
    const [rows] = await conn.query(sql, params)
    return rows
  } finally {
    await conn.end()
  }
}

module.exports = { db }
