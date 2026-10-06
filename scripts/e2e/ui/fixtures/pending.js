// Pruefungen fuer Verhalten, das erst ein paralleler Welle-3-Workstream liefert (Stand beim Schreiben: integrierte
// Welle 2). Ohne E2E_UI_PENDING=1 (run.sh --pending) werden sie nur als Annotation "pending" im Bericht vermerkt;
// mit dem Schalter laufen sie mit. Nach dem Merge des genannten Workstreams: Schalter setzen, gruen -> hier entfernen.
const { test } = require('@playwright/test')
const { PENDING } = require('./env')

/** Teilpruefung, die WS <owner> liefert (z. B. 'WS-W2-NACHARBEIT: UserSecurityModal mit useDialogFocus'). */
async function pendingCheck(owner, fn) {
  if (PENDING) {
    await fn()
    return
  }
  test.info().annotations.push({ type: 'pending', description: `TODO nach Merge von ${owner}` })
}

/** Ganze Spec erst nach dem Merge von <owner> ausfuehren (sonst Skip mit TODO). */
function skipUnlessPending(owner) {
  test.skip(!PENDING, `TODO: wartet auf ${owner} (Welle 3); mit run.sh --pending ausfuehren`)
}

module.exports = { pendingCheck, skipUnlessPending, PENDING }
