// RFC 6238 (TOTP, SHA1, 30 s, 6 Stellen) – nur node:crypto.
const crypto = require('node:crypto')

function base32Decode(input) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  const clean = String(input || '').toUpperCase().replace(/[^A-Z2-7]/g, '')
  let bits = 0
  let value = 0
  const out = []
  for (const ch of clean) {
    value = (value << 5) | alphabet.indexOf(ch)
    bits += 5
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff)
      bits -= 8
    }
  }
  return Buffer.from(out)
}

function totp(secret, { time = Date.now(), step = 30, digits = 6 } = {}) {
  const counter = Math.floor(time / 1000 / step)
  const buf = Buffer.alloc(8)
  buf.writeBigUInt64BE(BigInt(counter))
  const hmac = crypto.createHmac('sha1', base32Decode(secret)).update(buf).digest()
  const offset = hmac[hmac.length - 1] & 0x0f
  const code = (hmac.readUInt32BE(offset) & 0x7fffffff) % 10 ** digits
  return String(code).padStart(digits, '0')
}

module.exports = { totp, base32Decode }
