import { useTranslation } from 'react-i18next'

// Aufklappbare Syntax-Hilfe im Record-Dialog (F15 6.5-2). Die Signaturen sind Code (Schreibweise wie in der
// PowerDNS-Doku) und werden nicht uebersetzt.
const FUNCTIONS = [
    ['ifportup', 'ifportup(port, {ips})'],
    ['ifurlup', 'ifurlup(url, {{ips}, {ips}}, {options})'],
    ['pickrandom', 'pickrandom({ips})'],
    ['pickwrandom', 'pickwrandom({{weight, ip}, …})'],
    ['pickhashed', 'pickhashed({ips})'],
    ['pickclosest', 'pickclosest({ips})'],
    ['country', "country('DE')"],
    ['continent', "continent('EU')"],
    ['netmask', "netmask({'10.0.0.0/8'})"],
    ['view', 'view({…})'],
    ['latlon', 'latlon()'],
]

export default function LuaSyntaxHelp() {
    const { t } = useTranslation()
    return (
        <details className="rounded-lg border border-border/60 p-3 text-xs text-text-secondary">
            <summary className="cursor-pointer font-medium text-text-primary">{t('lua.helpTitle')}</summary>
            <div className="mt-2 space-y-1.5 leading-relaxed">
                <p>{t('lua.helpExpr')}</p>
                <p>{t('lua.helpStatements')}</p>
                <p>{t('lua.helpQuotes')}</p>
                <p>{t('lua.helpLines')}</p>
                <p>{t('lua.helpResult')}</p>
            </div>
            <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left">
                    <caption className="text-left font-medium text-text-primary mb-1">{t('lua.helpFunctions')}</caption>
                    <tbody>
                        {FUNCTIONS.map(([name, signature]) => (
                            <tr key={name} className="border-t border-border/40">
                                <td className="py-1 pr-3 font-mono whitespace-nowrap text-text-primary">{signature}</td>
                                <td className="py-1">{t(`lua.fn.${name}`)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </details>
    )
}
