// Behind oauth2-proxy an expired session makes /api/* answer 401 (--api-route). Reload the
// page so the proxy sends the browser through the login; the failed request is never
// replayed, so a write the user started must be redone after logging in. At most one
// reload per minute, so a 401 that survives the login can't loop.
const RELOAD_KEY = 'remnant-auth-reload-at'
const RELOAD_GAP_MS = 60_000

function reloadOnce(): boolean {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_KEY) || 0)
    if (Date.now() - last < RELOAD_GAP_MS) return false
    sessionStorage.setItem(RELOAD_KEY, String(Date.now()))
  } catch {
    return false // no storage: can't bound reloads, so don't reload at all
  }
  window.location.reload()
  return true
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, { ...init, credentials: 'same-origin' })
  if (res.status === 401) {
    throw new Error(reloadOnce() ? 'Session expired — logging in again'
                                 : 'Not authenticated — reload the page to log in')
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res
}
