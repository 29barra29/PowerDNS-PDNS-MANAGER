import LuaValue from '../../components/lua/LuaValue'

// Wert-Renderer fuer LUA-Records (F15 2.4, Slot B.14): Ziel-Typ-Badge + Lua-Code statt des Rohinhalts.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const renderer = { id: 'lua', when: (record) => record?.type === 'LUA' }

export default function LuaRenderer({ record }) {
    return <LuaValue content={record?.content} />
}
