export function toggleDriver(ids, id, checked) {
  return checked ? [...new Set([...ids, id])] : ids.filter((value) => value !== id)
}

export function selectDisplayed(rows, checked) {
  return checked ? rows.map((row) => row.id) : []
}

export function registrationStatus(row) {
  return row.is_active ? 'Идэвхтэй' : 'Идэвхгүй'
}

export function registrationValidity(row, now = Date.now()) {
  const utc = (value) => value && Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`)
  const start = utc(row.valid_from), end = utc(row.valid_to)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return 'Огноо шалгах'
  if (now < start) return 'Хугацаа эхлээгүй'
  if (now > end) return 'Хугацаа дууссан'
  return 'Хүчинтэй'
}

export const statusReasons = {
  change: 'Өөрчлөх боломжтой', unchanged: 'Төлөв хэвээр', expired: 'Хугацаа дууссан',
  not_started: 'Хугацаа эхлээгүй', invalid_dates: 'Огноо зөрчилтэй',
  active_duplicate: 'Өөр идэвхтэй бүртгэлтэй',
  selected_duplicate: 'Сонголтод ижил дугаар давхардсан — нэг мөрөө сонгоно уу',
}
