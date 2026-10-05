import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import OneTimeSecretModal from '../../OneTimeSecretModal'
import WebhooksCard from '../../webhooks/WebhooksCard'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'webhooks', order: 40 }

// Karte "Webhooks" (F6 §6) im Tab "API & Sicherheit". Liste, Formular und Zustellprotokoll liegen in
// components/webhooks/ (WS-F6-FE). Das Einmal-Secret (neuer Webhook bzw. "Secret erneuern") zeigt der Slot
// ueber OneTimeSecretModal an (f76: Kopieren wartet auf die Zwischenablage, Schliessen nur per Bestaetigung).
export default function WebhooksSettingsCard() {
    const { t } = useTranslation()
    const [secret, setSecret] = useState(null) // null | { title, secret }
    return (
        <>
            {secret && (
                <OneTimeSecretModal
                    title={secret.title}
                    body={t('webhooks.secretBody')}
                    secret={secret.secret}
                    onDone={() => setSecret(null)}
                />
            )}
            <WebhooksCard onSecret={setSecret} />
        </>
    )
}
