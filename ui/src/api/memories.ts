export type MemoryCategory = 'project' | 'feedback' | 'reference' | 'user' | 'session' | 'incident' | ''

export interface Memory {
  id: string
  memory: string
  agent_id: string
  category: MemoryCategory
  created_at: string
  updated_at: string
  score: number | null
}

export interface Stats {
  total: number
  first_added: string | null
  last_added: string | null
  by_agent: Record<string, number>
  by_category: Record<string, number>
}

export interface FetchParams {
  q?: string
  sort?: 'newest' | 'oldest' | 'relevance'
  agent_id?: string
  category?: string
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

  delete: (id: string): Promise<{ ok: boolean }> =>
    request(`/api/memories/${id}`, { method: 'DELETE' }),

  exportUrl: '/api/memories/export',
}
