import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { i18nReady } from './i18n'
import './index.css'
import App from './App.jsx'

// Erst rendern, wenn die Initialsprache geladen ist (kein Flackern en -> Zielsprache).
// i18nReady faengt Ladefehler selbst ab (dann eben en), deshalb finally.
i18nReady.finally(() => {
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </StrictMode>,
  )
})
