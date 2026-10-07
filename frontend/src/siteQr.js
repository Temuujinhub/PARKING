export const QR_URL_ERROR = 'QR линк буруу байна. Давхар залгасан холбоосыг арилгаад хэвлэсэн самбарын нэг бүтэн /checkout/… эсвэл /check-cost/… эсвэл /pay?site=… холбоос оруулна уу.'

// The API also verifies the configured payment origin. Keep the printed URL's
// identifier intact; replacing it can strand boards with a legacy site UUID.
export function printedQrError(value) {
  const text = (value || '').trim()
  if (!text) return ''
  if (text.length > 2048 || /[\s\x00-\x1f\x7f\\]/.test(text)
      || (text.match(/https?:\/\//gi) || []).length !== 1) return QR_URL_ERROR
  try {
    const url = new URL(text)
    if (!['https:', 'http:'].includes(url.protocol) || !url.hostname
        || url.username || url.password || url.hash) return QR_URL_ERROR
    if (url.pathname === '/pay') {
      const entries = [...url.searchParams]
      if (entries.length !== 1 || entries[0][0] !== 'site'
          || !/^[A-Za-z0-9_-]{1,64}$/.test(entries[0][1])) return QR_URL_ERROR
    } else if (url.search || !/^\/(checkout|check-cost)\/[A-Za-z0-9_-]{1,64}$/.test(url.pathname)) {
      return QR_URL_ERROR
    }
    return ''
  } catch { return QR_URL_ERROR }
}

// Content revision also busts the old hour-long PNG cache after a link repair.
export const qrImageUrl = (code, link = '', retry = 0) =>
  `/api/public/qr/${encodeURIComponent(code)}.png?v=${encodeURIComponent(link)}&retry=${retry}`
