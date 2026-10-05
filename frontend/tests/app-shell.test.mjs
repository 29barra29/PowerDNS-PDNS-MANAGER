// Vertragspruefung der App-Huelle (W0-INT-FE1a, Plan B.14/F8-B01/B02): statisch gelesen, weil die
// Dateien JSX und Vite-Features nutzen. Schuetzt die Slot-Mechanik und das Code-Splitting vor
// versehentlichen Rueckbauten in spaeteren Wellen.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(HERE, '..', 'src')
const read = (rel) => fs.readFileSync(path.join(SRC, rel), 'utf-8')

test('App.jsx: nur LoginPage statisch, alle anderen Seiten per lazyWithReload', () => {
    const app = read('App.jsx')
    const staticPages = [...app.matchAll(/^import\s+\w+\s+from\s+'\.\/pages\/(\w+)'/gm)].map((m) => m[1])
    assert.deepEqual(staticPages, ['LoginPage'])
    const pages = fs.readdirSync(path.join(SRC, 'pages')).filter((f) => f.endsWith('.jsx')).map((f) => f.replace(/\.jsx$/, ''))
    for (const p of pages) {
        if (p === 'LoginPage') continue
        assert.match(app, new RegExp(`lazyWithReload\\(\\(\\) => import\\('\\./pages/${p}'\\)\\)`), `${p} nicht lazy`)
    }
})

test('App.jsx: RequireAdmin fuer /audit und /users, ForcePasswordChange, Dialog-Slot', () => {
    const app = read('App.jsx')
    assert.match(app, /path="audit"\s+element=\{<RequireAdmin><AuditLogPage \/><\/RequireAdmin>\}/)
    assert.match(app, /path="users"\s+element=\{<RequireAdmin><UsersPage \/><\/RequireAdmin>\}/)
    assert.match(app, /PASSWORD_CHANGE_EVENT/)
    assert.match(app, /<ForcePasswordChange/)
    assert.match(app, /<Dialogs \/>/)
    // Sprachwahl nur ueber applyLanguage (F8-A04)
    assert.doesNotMatch(app, /changeLanguage\(/)
})

test('Layout.jsx: Suspense um Outlet, Banner-Slot, Sprache ueber applyLanguage', () => {
    const layout = read('components/Layout.jsx')
    assert.match(layout, /<Suspense fallback=\{<PageSpinner \/>\}>\s*<Outlet \/>\s*<\/Suspense>/)
    assert.match(layout, /<Banners user=\{user\} \/>/)
    assert.doesNotMatch(layout, /changeLanguage\(/)
    assert.match(read('components/LanguageDropdown.jsx'), /await applyLanguage\(code\)/)
})

test('Slot-Hosts: Glob-Muster und Dateikonvention', () => {
    assert.match(read('components/banners/Banners.jsx'), /import\.meta\.glob\('\.\/\*\.banner\.jsx', \{ eager: true \}\)/)
    assert.match(read('components/dialogs/Dialogs.jsx'), /import\.meta\.glob\('\.\/\*\.dialog\.jsx', \{ eager: true \}\)/)
    for (const [dir, suffix] of [['components/banners', '.banner.jsx'], ['components/dialogs', '.dialog.jsx']]) {
        for (const f of fs.readdirSync(path.join(SRC, dir))) {
            if (f.endsWith(suffix)) assert.match(f, /^\d{2}-[a-z0-9-]+\.(banner|dialog)\.jsx$/, `${dir}/${f}: Name NN-<name>${suffix}`)
        }
    }
})

test('i18n.js: Lazy-Locales, en statisch, Sprach-API', () => {
    const src = read('i18n.js')
    assert.match(src, /import en from '\.\/locales\/en\.json'/)
    assert.doesNotMatch(src, /import \w+ from '\.\/locales\/(de|sr|hr|bs|hu)\.json'/)
    assert.match(src, /import\.meta\.glob\(\['\.\/locales\/\*\.json', '!\.\/locales\/en\.json'\]\)/)
    for (const name of ['applyLanguage', 'getStoredLanguage', 'i18nReady', 'LANGUAGES', 'SUPPORTED']) {
        assert.match(src, new RegExp(`export (async function|function|const) ${name}\\b`), name)
    }
    assert.match(read('main.jsx'), /i18nReady\.finally\(/)
})
