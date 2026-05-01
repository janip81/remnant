export interface TagRule {
  tag: string
  keywords: string[]
  source: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const tagRulesApi = {
  list: (): Promise<TagRule[]> =>
    request('/api/tag-rules'),

  add: (tag: string, keyword: string): Promise<{ ok: boolean }> =>
    request('/api/tag-rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tag, keyword }),
    }),

  delete: (tag: string, keyword: string): Promise<{ ok: boolean }> =>
    request(`/api/tag-rules/${encodeURIComponent(tag)}/${encodeURIComponent(keyword)}`, {
      method: 'DELETE',
    }),
}
