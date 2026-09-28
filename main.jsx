import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './SarthiApp.jsx'
import { SarthiProvider } from './src/context/SarthiContext.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <SarthiProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </SarthiProvider>
  </React.StrictMode>,
)
