// API клиент — JWT токентой fetch wrapper
const TOKEN_KEY = 'parking_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t) => localStorage.setItem(TOKEN_KEY, t)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

export async function api(path, { method = 'GET', body, form, formData, blob } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let payload
  if (formData) {
    // Файл хуулах (multipart) — Content-Type-ыг браузер өөрөө boundary-тай тавина
    payload = formData
  } else if (form) {
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
    if (!res.ok) throw new Error(errMessage(null, res.status, await res.text().catch(() => '')))
    return res.blob()
  }
  // .json() биш .text() — nginx-ийн алдааны HTML хуудсыг ч харуулж чадна
  const raw = await res.text().catch(() => '')
  let data = {}
  try { data = raw ? JSON.parse(raw) : {} } catch { /* JSON биш — доор боловсруулна */ }
  if (!res.ok) throw new Error(errMessage(data.detail, res.status, raw))
  return data
}

// backend хүртэл ХҮРЭЭГҮЙ (nginx/proxy) алдааны товч тайлбар — эдгээр хариу нь
// JSON биш HTML тул урьд нь бүгд «Алдаа гарлаа» болж, шалтгаан нь далд үлддэг байв
const HTTP_HINT = {
  400: 'Хүсэлт буруу',
  403: 'Энэ үйлдлийг хийх эрхгүй',
  404: 'Хаяг олдсонгүй',
  413: 'Файл хэт том (сервер 10MB-аас их файл хүлээж авахгүй)',
  429: 'Хэт олон хүсэлт — хэсэг хүлээгээд дахин оролдоно уу',
  500: 'Серверийн дотоод алдаа — админд мэдэгдэнэ үү',
  502: 'Сервер хариу өгсөнгүй (дахин ачаалж байгаа эсвэл унтарсан)',
  503: 'Үйлчилгээ түр боломжгүй',
  504: 'Серверийн хариу хэтэрхий удлаа (timeout)',
}

// Backend-ийн алдааг ойлгомжтой мессеж болгоно. FastAPI validation (422)-ийн
// detail нь [{loc, msg, type}, ...] массив тул урьд нь «[object Object]» болдог
// байсан — талбар бүрийн ойлгомжтой мессежийг гаргана.
function errMessage(detail, status, raw) {
  if (!detail) {
    // detail огт байхгүй = backend-ийн JSON биш (nginx/proxy-ийн HTML г.м).
    // Статус болон биений эхлэлийг харуулж «юуны алдаа вэ» гэдгийг мэдэгдэнэ.
    const hint = HTTP_HINT[status]
    const body = String(raw || '').replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 120)
    if (!status) return 'Алдаа гарлаа'
    return `Алдаа гарлаа — HTTP ${status}${hint ? ` (${hint})` : ''}${body ? `\n${body}` : ''}`
  }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((e) => {
      if (typeof e === 'string') return e
      const field = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : ''
      return field ? `${field}: ${e.msg}` : e.msg
    }).filter(Boolean).join('; ') || 'Талбар буруу бөглөгдсөн'
  }
  return detail.msg || JSON.stringify(detail)
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
// Богино "сар/өдөр цаг:мин" (ж: 7/22 20:58) — locale-оос хамаарахгүй тогтмол формат
export const fmtShort = (s) => {
  if (!s) return '-'
  const d = new Date(s + (s.endsWith('Z') ? '' : 'Z'))
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
export const fmtDur = (m) => {
  if (m === null || m === undefined) return '-'
  const h = Math.floor(m / 60), mm = m % 60
  return h ? `${h}ц ${mm}м` : `${mm}м`
}
