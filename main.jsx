import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { hydrate } from './src/api/hydrate.js'

// Data must be in place before any page module is evaluated, so the app is imported after hydration.
async function start() {
  await hydrate()                                   // resolves false (and changes nothing) if the backend is down
  const [{ default: App }, { SarthiProvider }] = await Promise.all([
    import('./SarthiApp.jsx'),
    import('./src/context/SarthiContext.jsx'),
  ])
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <SarthiProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </SarthiProvider>
    </React.StrictMode>,
  )
}

start()
