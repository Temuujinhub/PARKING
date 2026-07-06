// API клиент — JWT токентой fetch wrapper
const TOKEN_KEY = 'parking_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export async function api(path, { method = 'GET', body, form, blob } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let payload
  if (form) {
    payload = new URLSearchParams(form)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
  } else if (body !== undefined) {
    payload = JSON.stringify(body)
    headers['Content-Type'] = 'application/json'
  }

  const res = await fetch(path, { method, headers, body: payload })
  if (res.status === 401) {
    clearToken()
    if (!location.pathname.startsWith('/pay')) location.href = '/login'
    throw new Error('Нэвтрэлт дууссан')
  }
  if (blob) {
    if (!res.ok) throw new Error('Татахад алдаа гарлаа')
    return res.blob()
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || 'Алдаа гарлаа')
  return data
}

export function wsConnect(siteId = 'all', onMessage) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  let ws, timer, closed = false
  const connect = () => {
    ws = new WebSocket(`${proto}://${location.host}/ws/sites/${siteId}`)
    ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)) } catch {} }
    ws.onclose = () => { if (!closed) timer = setTimeout(connect, 3000) }
    ws.onopen = () => { /* keepalive */ }
  }
  connect()
  const ping = setInterval(() => { if (ws?.readyState === 1) ws.send('ping') }, 30000)
  return () => { closed = true; clearInterval(ping); clearTimeout(timer); ws?.close() }
}

export const fmt = (n) => (n === null || n === undefined ? '-' : Number(n).toLocaleString('mn-MN'))
export const fmtDate = (s) => (s ? new Date(s + (s.endsWith('Z') ? '' : 'Z')).toLocaleString('mn-MN', { hour12: false }) : '-')
export const fmtDur = (m) => {
  if (m === null || m === undefined) return '-'
  const h = Math.floor(m / 60), mm = m % 60
  return h ? `${h}ц ${mm}м` : `${mm}м`
}
