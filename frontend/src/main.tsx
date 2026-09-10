import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './App'
import './styles.css'
import { startChatSession } from './store/chat-session'

const stopChatSession = startChatSession()
if (import.meta.hot) import.meta.hot.dispose(stopChatSession)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>,
)
