import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@radix-ui/themes/styles.css'
import '../style/theme.css'
import './index.css'
import { Theme } from '@radix-ui/themes'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Theme
      appearance="dark"
      accentColor="amber"
      grayColor="sand"
      radius="medium"
      panelBackground="translucent"
    >
      <App />
    </Theme>
  </StrictMode>,
)
