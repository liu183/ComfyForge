const BASE = '/api/v1'

async function request(url, options = {}) {
  const res = await fetch(BASE + url, options)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = j.detail || JSON.stringify(j)
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  return res.json()
}

export function getHealth() {
  return request('/health')
}

export function getCapabilities() {
  return request('/capabilities')
}

export async function createTask(type, params) {
  return request('/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, params })
  })
}

export async function createTaskWithImage(type, params, file) {
  const fd = new FormData()
  fd.append('type', type)
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') fd.append(k, v)
  }
  fd.append('image', file)
  return request('/tasks/upload', { method: 'POST', body: fd })
}

export function getTasks(status) {
  const q = status ? `?status=${status}` : ''
  return request('/tasks' + q)
}

export function getTask(id) {
  return request('/tasks/' + id)
}
