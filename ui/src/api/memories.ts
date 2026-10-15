export type MemoryCategory = 'project' | 'feedback' | 'reference' | 'user' | 'session' | 'incident' | ''

export type MemoryState = 'active' | 'candidate' | 'superseded' | 'rejected'

export interface Memory {
  id: string
  memory: string
  agent_id: string
  category: MemoryCategory
  tags: string[]
  created_at: string
  updated_at: string
  score: number | null
  state: MemoryState
  source: string
  review_reason: string
  project: string
}

export interface Stats {
  total: number
  first_added: string | null
  last_added: string | null
  by_agent: Record<string, number>
  by_category: Record<string, number>
  by_tag: Record<string, number>
}

export interface FetchParams {
  q?: string
  sort?: 'newest' | 'oldest' | 'relevance'
  agent_id?: string
  category?: string
  state?: MemoryState | ''
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const api = {
  stats: (): Promise<Stats> =>
    request('/api/stats'),

  list: (params: FetchParams = {}): Promise<Memory[]> => {
    const qs = new URLSearchParams()
    if (params.q) qs.set('q', params.q)
    if (params.sort) qs.set('sort', params.sort)
    if (params.agent_id) qs.set('agent_id', params.agent_id)
    if (params.category) qs.set('category', params.category)
    if (params.state) qs.set('state', params.state)
    return request(`/api/memories${qs.size ? `?${qs}` : ''}`)
  },

  add: (content: string): Promise<{ ok: boolean }> =>
    request('/api/memories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    }),

  update: (id: string, content: string): Promise<{ ok: boolean }> =>
    request(`/api/memories/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ memory: content }),
    }),

  updateTags: (id: string, tags: string[]): Promise<{ ok: boolean }> =>
    request(`/api/memories/${id}/tags`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags }),
    }),

  review: (id: string, action: 'promote' | 'reject', note = ''): Promise<{ ok: boolean; state?: string; error?: string }> =>
    request(`/api/memories/${id}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, note }),
    }),

  delete: (id: string): Promise<{ ok: boolean }> =>
    request(`/api/memories/${id}`, { method: 'DELETE' }),

  exportUrl: '/api/memories/export',
}
