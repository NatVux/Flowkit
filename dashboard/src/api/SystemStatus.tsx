import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from './flowkit'

const POLL_MS = 10_000

export interface SystemStatus {
  loaded: boolean
  server: boolean
  extension: boolean
  flowTab: boolean
  cooldownSeconds: number  // 0 when no cooldown
  gemini: boolean
  /** Flow calls can go out: server, extension and a Flow tab are all up. */
  flowReady: boolean
  refresh: () => void
}

const initial: Omit<SystemStatus, 'refresh'> = {
  loaded: false, server: false, extension: false, flowTab: false, cooldownSeconds: 0, gemini: false, flowReady: false,
}

const SystemStatusContext = createContext<SystemStatus | null>(null)

/** Polls /health, /api/flow/status and /api/ai/status every 10 s for the status bar and the buttons. */
export function SystemStatusProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(initial)

  const refresh = useCallback(async () => {
    const [health, flow, ai] = await Promise.allSettled([api.health(), api.flowStatus(), api.aiStatus()])
    if (health.status === 'rejected') {
      setState({ ...initial, loaded: true })
      return
    }
    const extension = health.value.extension_connected
    const flowTab = flow.status === 'fulfilled'
      ? flow.value.flow_tab_available
      : Boolean(health.value.ws?.flow_tab_available)
    const throttle = flow.status === 'fulfilled' ? flow.value.generation_throttle : undefined
    const cooldownSeconds = throttle?.cooldown_active ? Math.ceil(throttle.cooldown_remaining_s) : 0
    const gemini = ai.status === 'fulfilled' && ai.value.enabled && ai.value.configured
    setState({
      loaded: true, server: true, extension, flowTab, cooldownSeconds, gemini,
      flowReady: extension && flowTab,
    })
  }, [])

  useEffect(() => {
    const first = setTimeout(refresh, 0)
    const id = setInterval(refresh, POLL_MS)
    return () => { clearTimeout(first); clearInterval(id) }
  }, [refresh])

  return <SystemStatusContext.Provider value={{ ...state, refresh }}>{children}</SystemStatusContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSystemStatus(): SystemStatus {
  const ctx = useContext(SystemStatusContext)
  if (!ctx) throw new Error('useSystemStatus must be used within a SystemStatusProvider')
  return ctx
}

// eslint-disable-next-line react-refresh/only-export-components
export function formatSeconds(total: number): string {
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}
