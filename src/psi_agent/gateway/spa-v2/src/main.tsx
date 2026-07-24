import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles/globals.css'
import './styles/highlight.css'

const root = document.getElementById('app')
if (!root) {
  throw new Error('Missing #app mount point')
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
