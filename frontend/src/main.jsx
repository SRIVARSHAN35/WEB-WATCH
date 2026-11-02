import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

const themeUserId = window.sessionStorage.getItem('webwatch_theme_user_id')
const savedTheme = themeUserId
  ? window.localStorage.getItem(`webwatch_theme_preference_${themeUserId}`)
  : null
if (['light', 'dark', 'system'].includes(savedTheme)) {
  document.documentElement.dataset.theme = savedTheme
} else {
  document.documentElement.dataset.theme = 'system'
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
