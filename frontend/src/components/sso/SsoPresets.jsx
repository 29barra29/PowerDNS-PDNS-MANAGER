import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Wand2 } from 'lucide-react'
import { LDAP_PRESETS, OIDC_PRESETS } from './ssoModel'

// Vorlagen fuer den SSO-Tab (F10 §2.5/§6.4): OIDC Allgemein/Keycloak/Authentik/Entra ID, LDAP Active Directory/
// OpenLDAP/FreeIPA. Setzt nur Felder im Formular (Scopes, Claims, Filter, Attribute, Gruppenmodus); gespeichert
// wird erst mit "Speichern". Die Issuer-/Server-Adresse bleibt leer und bekommt nur einen Beispiel-Platzhalter.
//
// Props: kind ('oidc' | 'ldap'), onApply(presetId, preset)
export default function SsoPresets({ kind, onApply, disabled }) {
    const { t } = useTranslation()
    const table = kind === 'ldap' ? LDAP_PRESETS : OIDC_PRESETS
    const [choice, setChoice] = useState('')
    const id = `sso-${kind}-preset`

    function apply() {
        if (!choice || !table[choice]) return
        onApply(choice, table[choice])
        setChoice('')
    }

    return (
        <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[12rem]">
                <label htmlFor={id} className="block text-sm font-medium text-text-secondary mb-1.5">{t('settings.sso.preset')}</label>
                <select id={id} value={choice} onChange={(e) => setChoice(e.target.value)} className="w-full px-3 py-2 text-sm" disabled={disabled}>
                    <option value="">{t('settings.sso.presetChoose')}</option>
                    {Object.entries(table).map(([key, preset]) => (
                        <option key={key} value={key}>{t(preset.labelKey)}</option>
                    ))}
                </select>
            </div>
            <button
                type="button"
                onClick={apply}
                disabled={disabled || !choice}
                className="px-3 py-2 rounded-lg border border-border hover:bg-bg-hover text-sm flex items-center gap-1.5 disabled:opacity-50"
            >
                <Wand2 className="w-4 h-4" aria-hidden="true" />
                {t('settings.sso.presetApply')}
            </button>
        </div>
    )
}
