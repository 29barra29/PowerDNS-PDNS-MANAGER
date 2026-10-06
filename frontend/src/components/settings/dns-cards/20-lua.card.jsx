import LuaSettingsCard from '../../lua/LuaSettingsCard'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'lua', order: 20 }

// Karte "LUA-Records" im Admin-Tab "DNS-Optionen" (F15 2.1/6.7): Policy und PowerDNS-Konfiguration je Server.
export default function LuaCard() {
    return <LuaSettingsCard />
}
