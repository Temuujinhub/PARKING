/* Гараар оруулах бүх утгын НЭГДСЭН шалгалт.
 *
 * Зарчим: талбар бүрт (1) оруулах үед цэвэрлэх (sanitize — буруу тэмдэгтийг
 * бичихийг нь зөвшөөрөхгүй), (2) формат шалгах (is*), (3) илгээхийн өмнө
 * хязгаарлах (clamp) гэсэн 3 давхарга. Backend талд бас шалгалт бий —
 * энэ нь зөвхөн хэрэглэгчид ойлгомжтой болгох давхарга.
 */

// ── Улсын дугаар ────────────────────────────────────────────────
// Энгийн: 4 орон + 3 кирилл үсэг (1234УБА)
// Дипломат/тусгай: 2 кирилл үсэг + 4 орон (ДК1234)
export const PLATE_RE = /^\d{4}[А-ЯЁӨҮ]{3}$|^[А-ЯЁӨҮ]{2}\d{4}$/
export const normalizePlate = (v) =>
  String(v || '').toUpperCase().replace(/[^0-9А-ЯЁӨҮ]/g, '').slice(0, 7)
export const isPlate = (v) => PLATE_RE.test(normalizePlate(v))
export const PLATE_HINT = 'Формат: 4 тоо + 3 кирилл үсэг (1234УБА) эсвэл 2 үсэг + 4 тоо (ДК1234)'

// ── Утасны дугаар (МУ: 8 орон) ──────────────────────────────────
// Гар утас 8/9/5-аар, суурин утас 11/70/77-оор эхэлдэг тул ЭХНИЙ ОРНООР нь
// хязгаарлахгүй — зөвхөн урт болон тэмдэгтийг шалгана.
export const normalizePhone = (v) => {
  let d = String(v || '').replace(/\D/g, '')
  if (d.length === 11 && d.startsWith('976')) d = d.slice(3)   // +976 угтварыг хасна
  return d.slice(0, 8)
}
export const isPhone = (v) => !v || /^\d{8}$/.test(normalizePhone(v))
export const PHONE_HINT = '8 оронтой дугаар (жишээ: 99112233)'

// ── ТТД / Регистр ───────────────────────────────────────────────
// ААН-ийн ТТД 7 орон, иргэний регистр 2 кирилл үсэг + 8 орон.
// e-Barimt 3.0-д илүү урт ТТД бас тааралддаг тул 7–14 орныг зөвшөөрнө.
export const normalizeTin = (v) => String(v || '').replace(/\D/g, '').slice(0, 14)
export const isTin = (v) => !v || /^\d{7,14}$/.test(v)
export const normalizeRegister = (v) =>
  String(v || '').toUpperCase().replace(/[^0-9А-ЯЁӨҮ]/g, '').slice(0, 14)
export const isRegister = (v) => !v || /^\d{7,14}$/.test(v) || /^[А-ЯЁӨҮ]{2}\d{8}$/.test(v)
export const REGISTER_HINT = 'ААН: 7 оронтой ТТД · Иргэн: УБ12345678'

// ── Нэвтрэх нэр / нууц үг ───────────────────────────────────────
// Нэвтрэх нэр: латин үсэг, тоо, . _ - (кирилл/зайг оруулбал нэвтрэхэд эргэлздэг)
export const normalizeUsername = (v) => String(v || '').toLowerCase().replace(/[^a-z0-9._-]/g, '').slice(0, 60)
export const isUsername = (v) => /^[a-z0-9._-]{3,60}$/.test(String(v || ''))
export const USERNAME_HINT = '3–60 тэмдэгт: латин үсэг, тоо, . _ -'

// Нууц үг: 8+ тэмдэгт (backend-ийн _check_password-той ЯГ ижил дүрэм — эндээс
// илүү хатуу шалгавал хэрэглэгч «яагаад болохгүй байна» гэж эргэлздэг).
export const isPassword = (v) => String(v || '').length >= 8
export const PASSWORD_HINT = 'Хамгийн багадаа 8 тэмдэгт (үсэг+тоо холивол илүү найдвартай)'

// ── И-мэйл ──────────────────────────────────────────────────────
export const isEmail = (v) => !v || /^[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}$/.test(String(v).trim())

// ── IPv4 хаяг (камер/хаалтны төхөөрөмж) ─────────────────────────
const OCTET = '(25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)'
export const IPV4_RE = new RegExp(`^${OCTET}\\.${OCTET}\\.${OCTET}\\.${OCTET}$`)
export const normalizeIp = (v) => String(v || '').replace(/[^0-9.]/g, '').slice(0, 15)
export const isIp = (v) => !v || IPV4_RE.test(v)
export const IP_HINT = 'Жишээ: 192.168.1.108'

// ── Латин код (зогсоолын код, түрээслэгчийн код) ────────────────
export const normalizeCode = (v) =>
  String(v || '').toUpperCase().replace(/[^A-Z0-9_-]/g, '').slice(0, 30)

// ── Тоон утга ───────────────────────────────────────────────────
/** Хоосон/буруу утгыг fallback болгож, min–max хооронд шахна.
 *  Хоосныг null-аар үлдээх бол fallback=null өгнө. */
export function clampNum(v, { min = 0, max = Infinity, fallback = 0, int = true } = {}) {
  if (v === '' || v === null || v === undefined) return fallback
  let n = int ? parseInt(v, 10) : parseFloat(v)
  if (!Number.isFinite(n)) return fallback
  return Math.min(max, Math.max(min, n))
}
/** Заавал биш тоон талбар: хоосон бол null, эсвэл шахсан тоо. */
export const clampOrNull = (v, opts) =>
  v === '' || v === null || v === undefined ? null : clampNum(v, { ...opts, fallback: null }) ?? null

// ── Огноо/цаг ───────────────────────────────────────────────────
const pad = (n) => String(n).padStart(2, '0')
/** input[type=date]-д тохирох YYYY-MM-DD (локал цагаар) */
export const toDateInput = (d = new Date()) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
/** input[type=datetime-local]-д тохирох YYYY-MM-DDTHH:MM (локал цагаар) */
export const toDateTimeInput = (d = new Date()) => `${toDateInput(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`
/** Өнөөдөр — огнооны талбарын max (ирээдүйн огноо сонгуулахгүй) */
export const TODAY = toDateInput()

/** Хугацааны мужийн шалгалт: буруу бол алдааны мессеж, зөв бол null. */
export function dateRangeError(from, to, { maxDays = 0 } = {}) {
  if (!from || !to) return null
  if (from > to) return 'Эхлэх огноо дуусах огнооноос хойш байна'
  if (maxDays) {
    const days = (new Date(to) - new Date(from)) / 86400000
    if (days > maxDays) return `Хугацааны муж хэт урт (дээд тал нь ${maxDays} хоног)`
  }
  return null
}

/** "HH:MM" цагийн цонх — хоёулаа бөглөгдсөн эсэх, ижил биш эсэх. */
export function timeWindowError(from, until) {
  if (!from && !until) return null
  if (!from || !until) return 'Цагийн цонх үйлчлэхийн тулд эхлэх, дуусах хоёуланг нь бөглөнө'
  if (from === until) return 'Эхлэх, дуусах цаг ижил байна'
  return null
}

// ── Тарифын шатлал ──────────────────────────────────────────────
const tierList = (t) => (t.tiers || []).map((x) => ({ upto: +x.upto_minutes, price: +x.price }))

/** ЗААВАЛ засах алдаа — эдгээртэй бол хадгалахыг зогсооно.
 *  Буруу дараалалтай шатлал тарифын тооцоог чимээгүй буруу дүн гаргадаг. */
export function tariffErrors(t) {
  const errs = []
  const tiers = tierList(t)
  tiers.forEach((x, i) => {
    if (!Number.isFinite(x.upto) || x.upto < 1) errs.push(`${i + 1}-р шатлалын хугацаа 1 минутаас багагүй байх ёстой`)
    if (!Number.isFinite(x.price) || x.price < 0) errs.push(`${i + 1}-р шатлалын үнэ сөрөг байж болохгүй`)
    if (i > 0 && x.upto <= tiers[i - 1].upto) errs.push(`${i + 1}-р шатлалын хугацаа өмнөхөөсөө их байх ёстой (өсөх дараалал)`)
  })
  if (+t.free_minutes < 0 || +t.grace_minutes < 0) errs.push('Хугацаа сөрөг байж болохгүй')
  return errs
}

/** Хадгалахыг зогсоохгүй, гэхдээ анхаарууштай логик зөрчил. */
export function tariffWarnings(t) {
  const warns = []
  const tiers = tierList(t)
  tiers.forEach((x, i) => {
    if (i > 0 && x.price < tiers[i - 1].price) {
      warns.push(`${i + 1}-р шатлалын үнэ өмнөхөөсөө бага — урт зогссон машин хямд төлнө`)
    }
  })
  const cap = t.daily_cap === '' || t.daily_cap == null ? null : +t.daily_cap
  const maxTier = tiers.length ? Math.max(...tiers.map((x) => x.price)) : 0
  if (cap != null && cap > 0 && cap < maxTier) {
    warns.push(`Хоногийн дээд хязгаар (${cap}₮) шатлалын дээд үнээс (${maxTier}₮) бага — шатлал хэрэгжихгүй`)
  }
  return warns
}

/** Оруулсан талбар зөв эсэхээр input-д улаан хүрээ нэмэх туслах. */
export const errCls = (invalid) => (invalid ? ' input-error' : '')
