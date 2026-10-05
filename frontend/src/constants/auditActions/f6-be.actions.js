/**
 * Audit-Aktionen der Webhook-Verwaltung (WS-F6-BE, backend/app/routers/webhooks.py, F6 3.2-3.8).
 * resource_type "webhook", resource_name = Name des Webhooks; die Details nennen nur den Host der Ziel-URL,
 * nie Pfad, Query oder Secret. Labels: `audit.actions.WEBHOOK_*`, `audit.resourceTypes.webhook` (Fragment f6-be).
 */
export default [
    { action: 'WEBHOOK_CREATE', group: 'settings', history: false },
    { action: 'WEBHOOK_UPDATE', group: 'settings', history: false },
    { action: 'WEBHOOK_SECRET_ROTATE', group: 'settings', history: false },
    { action: 'WEBHOOK_DELETE', group: 'settings', history: false },
    { action: 'WEBHOOK_DELIVERY_RETRY', group: 'settings', history: false },
]

export const resourceTypes = ['webhook']
