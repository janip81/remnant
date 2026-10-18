// Behind oauth2-proxy an expired session makes /api/* answer 401 (--api-route). Reload the
// page so the proxy sends the browser through the login; the failed request is never
// replayed, so a write the user started must be redone after logging in.
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(path, { ...init, credentials: 'same-origin' })
  if (res.status === 401) {
    window.location.reload()
    throw new Error('Session expired — logging in again')
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res
}
