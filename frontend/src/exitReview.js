export function exitFacts(facts) {
  if (!facts) return []
  const read = { accepted: 'Уншилт зөвшөөрөгдсөн', rejected: 'Уншилт ирсэн ч татгалзсан', not_linked: 'Холбогдсон гарах уншилтгүй' }
  const command = { SUCCESS: 'Нээх команд илгээсэн', FAILED: 'Команд амжилтгүй', UNKNOWN: 'Үр дүн тодорхойгүй', PENDING: 'Команд хүлээгдэж байна', SKIPPED: 'Командыг илгээгээгүй' }
  return [
    ['Гарах уншилт', read[facts.read_state] || 'Тодорхойгүй'],
    ['Төлбөр', facts.paid ? 'Төлөгдсөн' : facts.is_free ? 'Үнэ төлбөргүй эрхтэй' : 'Төлөгдөөгүй'],
    ['Зогсолт', facts.closed ? 'Бүртгэл хаасан' : 'Нээлттэй'],
    ['Нээх команд', facts.command ? (command[facts.command.status] || 'Үр дүн тодорхойгүй') : 'Команд бүртгэгдээгүй'],
    ['Төхөөрөмжийн хариу', facts.acknowledged ? 'Зөвшөөрсөн (ACK)' : 'Зөвшөөрсөн хариу алга'],
    ['Бодитоор гарсан эсэх', 'Тусдаа баталгаа алга'],
  ]
}

export function reviewDate(value) {
  if (!value) return '—'
  const iso = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? 'Цаг тодорхойгүй' : date.toLocaleString('mn-MN', { hour12: false })
}

export function reviewReady({ proof, expiresAt, now, confirmed, reason, busy }) {
  return Boolean(proof && now < expiresAt && confirmed && reason.trim().length >= 3 && !busy)
}
