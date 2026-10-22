// «Төлбөр хүлээж эхэлсэн» (system test №56, 2026-10-22) — машин гарах камерт
// анх уншигдаж AWAITING_PAYMENT болсон цаг (`payment_wait_started_at`, 02ba3c1-ээс
// хадгалагддаг ч хаана ч харагддаггүй байв). Оператор хэр удаан хүлээлгэж байгааг,
// мөн `auto_close_awaiting_hours`-ийн дараа автоматаар өр болох машиныг урьдчилан харна.
import { fmtDate, fmtDur, fmtHM } from '../api'

export function minutesSince(s, now = Date.now()) {
  if (!s) return null
  const t = new Date(s + (s.endsWith('Z') ? '' : 'Z')).getTime()
  return Number.isNaN(t) ? null : Math.max(0, Math.floor((now - t) / 60000))
}

export function PaymentWaitSince({ session, className = '' }) {
  const t = session?.payment_wait_started_at
  if (!t || session.status !== 'AWAITING_PAYMENT') return null
  const m = minutesSince(t)
  return (
    <div className={`text-[10px] text-amber-300 whitespace-nowrap ${className}`}
      title={`Гарах камерт уншигдаж төлбөр хүлээж эхэлсэн: ${fmtDate(t)}`}>
      хүлээж эхэлсэн {fmtHM(t)}{m != null && <> · {fmtDur(m)}</>}
    </div>
  )
}
