import { Link } from 'react-router-dom'
import { fmt, fmtDate } from '../api'

const PAY_LABEL = { QPAY: 'QPay QR', POS: 'Карт (ПОС)', CASH: 'Бэлэн', TRANSFER: 'Дансаар',
  WALLET: 'Хэтэвч', SITE_WALLET: 'Зогсоолын хэтэвч', EP_WALLET: 'Easy Parking хэтэвч' }
const DEBT_LABEL = { PENDING: 'Төлөгдөөгүй', PAID: 'Төлөгдсөн', CANCELLED: 'Цуцалсан' }
const linkStyle = 'inline-block rounded py-2 underline underline-offset-4 text-slate-200 hover:text-slate-100 hover:bg-surface-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-current'

export function ExitReadCell({ session }) {
  const state = session.exit_read_status || (session.exit_device_id ? 'CAMERA_LINKED'
    : session.exit_confirmed ? 'RECORDED_EXIT' : 'NO_CAMERA_READ')
  const label = { CAMERA_READ: 'Гарах камерт уншигдсан', RECORDED_EXIT: 'Гарах цаг бүртгэлтэй',
    CAMERA_LINKED: 'Гарах камер холбогдсон · уншилт баталгаажаагүй',
    NO_CAMERA_READ: 'Гарах камерын уншилт бүртгэгдээгүй' }[state] || 'Уншилт тодорхойгүй'
  return <div className="text-xs font-sans text-slate-300 mt-1">{label}</div>
}

export function HistoryPaymentCell({ session, selectedPaymentId }) {
  const pays = session.payments || []
  if (!pays.length) return <span className="text-xs text-slate-300">
    {session.status === 'FREE' ? 'Үнэгүй' : 'Энэ зогсолтод шууд төлбөр бүртгэгдээгүй'}
  </span>
  return <div className="space-y-3 min-w-[12rem]">
    {pays.map((p) => <details key={p.id} id={`payment-${p.id}`} open={selectedPaymentId === p.id || undefined}
      className="rounded border border-surface-border p-2 text-xs text-slate-200">
      <summary className="cursor-pointer rounded py-1 hover:bg-surface-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-current">
        {PAY_LABEL[p.provider] || p.provider} · {fmt(p.amount)}₮ · Төлсөн
        {p.allocation?.known && p.allocation.previous_debt_amount > 0 && <span className="block mt-2 text-slate-300">
          Зогсолт {fmt(p.allocation.parking_amount)}₮ + өмнөх өр {fmt(p.allocation.previous_debt_amount)}₮
        </span>}
      </summary>
      <div className="mt-2 space-y-2">
        <div>{fmtDate(p.paid_at)}</div>
        {p.cashier && <div>Хүлээн авсан: {p.cashier}</div>}
        {p.allocation?.known ? <dl className="space-y-1" aria-label="Нэхэмжлэлийн задаргаа">
          <div className="flex justify-between gap-4"><dt>Энэ зогсолт</dt><dd>{fmt(p.allocation.parking_amount)}₮</dd></div>
          <div className="flex justify-between gap-4"><dt>Өмнөх өр</dt><dd>{fmt(p.allocation.previous_debt_amount)}₮</dd></div>
          <div className="flex justify-between gap-4 border-t border-surface-border pt-1 font-semibold"><dt>Нийт гүйлгээ</dt><dd>{fmt(p.amount)}₮</dd></div>
        </dl> : <p>Баталгаатай задаргаа хадгалагдаагүй. Нийт төлсөн дүнг харуулав.</p>}
      </div>
    </details>)}
  </div>
}

export function SessionDebtCell({ session }) {
  const debts = session.session_debts || []
  if (!debts.length) return <span className="text-xs text-slate-300">Өр бүртгэгдээгүй</span>
  return <div className="space-y-2 text-xs text-slate-200 min-w-[12rem]">
    {debts.map((d) => {
      const p = d.status === 'PAID' ? d.payment : null
      const paidText = `${fmt(d.amount)}₮ өрийг ${fmtDate(d.paid_at)}${p ? ` · ${PAY_LABEL[p.provider] || p.provider}` : ''} төлсөн`
      return <div key={d.id}>
        {d.status === 'PAID' ? (p?.session_id ?
          <Link className={linkStyle} to={`/history?session_id=${encodeURIComponent(p.session_id)}&payment_id=${encodeURIComponent(p.id)}`}
            aria-label={`${paidText}. Холбогдох төлбөрийн задаргаа харах`}>{paidText}</Link>
          : <span>{paidText}</span>)
          : <span>{fmt(d.amount)}₮ · {DEBT_LABEL[d.status] || d.status}</span>}
      </div>
    })}
  </div>
}
