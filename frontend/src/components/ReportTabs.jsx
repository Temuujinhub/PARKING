// Тайлангийн таб компонентууд — таб бүр өөрийн өгөгдлийг useFetch-ээр татаж, drilldown-оо
// өөрөө удирдана. Reports.jsx-ийг нимгэн orchestrator болгож, 450 мөрийн god-component-ийг
// задлав. Алдаа toast-оор харагдана (өмнө нь catch(()=>{}) чимээгүй залгидаг байсан).
import { Download } from 'lucide-react'
import { useState } from 'react'
import { api, fmt, fmtDate, fmtDur } from '../api'
import { useFetch } from '../hooks/useFetch'
import { useDownload } from '../hooks/useDownload'
import { Badge, Table, useToast } from './ui'

export const siteQ = (sid) => sid ? `&site_id=${sid}` : ''
const lastDayOf = (month) => {
  const [y, m] = month.split('-').map(Number)
  return `${month}-${String(new Date(y, m, 0).getDate()).padStart(2, '0')}`
}
const hm = (iso) => iso ? iso.replace('T', ' ').slice(11, 16) : '—'

// Тухайн өдрийн бүх гүйлгээ (цаг:минутаар) — Өдрөөр/Сараар/Зогсоолоор drilldown-оос нээгдэнэ
function DayTxns({ date, siteId, onClose }) {
  const dl = useDownload()
  const { data, loading } = useFetch(
    `/api/reports/transactions?date_from=${date}&date_to=${date}&date_field=paid&limit=2000${siteQ(siteId)}`,
    { initial: { rows: [] } })
  const rows = data?.rows || []
  return (
    <div className="card border-accent/30">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-accent">{date} — өдрийн бүх гүйлгээ</h3>
        <div className="flex items-center gap-3">
          <button className="btn-secondary py-1 text-xs"
            onClick={() => dl(`/api/reports/transactions/excel?date_from=${date}&date_to=${date}&date_field=paid${siteQ(siteId)}`, `guilgee_${date}.xlsx`)}>
            <Download size={13} /> Татах
          </button>
          <button className="text-xs text-slate-400 hover:text-slate-200 underline" onClick={onClose}>Хаах</button>
        </div>
      </div>
      {loading
        ? <div className="text-sm text-slate-500 py-4 text-center">Ачаалж байна…</div>
        : (
          <Table headers={['Дугаар', 'Орсон', 'Гарсан', 'Төлсөн', 'Хугацаа', 'Төрөл', 'Нийт (₮)', 'Хэрэгсэл', 'Төлөв', 'Кассчин']}
            empty={rows.length === 0}>
            {rows.map((r) => (
              <tr key={r.session_id}>
                <td className="td font-mono font-bold">{r.plate_number}</td>
                <td className="td font-mono text-xs">{hm(r.entry_time)}</td>
                <td className="td font-mono text-xs">{hm(r.exit_time)}</td>
                <td className="td font-mono text-xs text-accent">{hm(r.paid_at)}</td>
                <td className="td font-mono text-xs">{fmtDur(r.duration_minutes)}</td>
                <td className={`td text-xs font-medium ${r.car_type === 'Гэрээт' ? 'text-cyan-400' : r.car_type === 'Хөнгөлөлттэй' ? 'text-amber-400' : ''}`}>{r.car_type}</td>
                <td className="td font-mono">{fmt(r.total_fee)}</td>
                <td className="td text-xs">{r.provider || '—'}</td>
                <td className="td text-xs">{r.status}</td>
                <td className="td text-xs">{r.cashier || '—'}</td>
              </tr>
            ))}
          </Table>
        )}
    </div>
  )
}

// Сарын доторх өдрүүд (drilldown) — Сараар таб ба Зогсоолоор→сар гинжнээс
function MonthDays({ month, siteId, onClose }) {
  const dl = useDownload()
  const [day, setDay] = useState(null)
  const { data } = useFetch(
    `/api/reports/daily?date_from=${month}-01&date_to=${lastDayOf(month)}${siteQ(siteId)}`, { initial: { rows: [] } })
  const rows = data?.rows || []
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-accent">{month} сарын өдрүүд</h3>
        <div className="flex items-center gap-3">
          <button className="btn-secondary py-1 text-xs"
            onClick={() => dl(`/api/reports/daily/excel?date_from=${month}-01&date_to=${lastDayOf(month)}${siteQ(siteId)}`, `odriin_${month}.xlsx`)}>
            <Download size={13} /> Сарын бүх өдрүүд татах
          </button>
          <button className="text-xs text-slate-400 hover:text-slate-200 underline" onClick={onClose}>Хаах</button>
        </div>
      </div>
      <Table headers={['Огноо', 'Орсон', 'Гарсан', 'Бэлэн (₮)', 'QPay (₮)', 'Карт (₮)', 'Нийт (₮)']} empty={rows.length === 0}>
        {rows.map((r) => (
          <tr key={r.date} onClick={() => setDay(r.date)} className="cursor-pointer hover:bg-surface-muted/40">
            <td className="td font-mono text-accent underline decoration-dotted">{r.date}</td>
            <td className="td font-mono">{fmt(r.entered)}</td>
            <td className="td font-mono">{fmt(r.exited)}</td>
            <td className="td font-mono">{fmt(r.cash_amount)}</td>
            <td className="td font-mono">{fmt(r.qpay_amount)}</td>
            <td className="td font-mono">{fmt(r.pos_amount)}</td>
            <td className="td font-mono text-accent font-semibold">{fmt(r.paid_amount)}</td>
          </tr>
        ))}
      </Table>
      {day && <div className="mt-3"><DayTxns date={day} siteId={siteId} onClose={() => setDay(null)} /></div>}
    </div>
  )
}

// ── Зогсоолоор ──
export function RevenueTab({ from, to }) {
  const dl = useDownload()
  const [siteMonths, setSiteMonths] = useState(null) // {site_id, name}
  const [month, setMonth] = useState(null)
  const { data } = useFetch(`/api/reports/revenue?date_from=${from}&date_to=${to}`, { initial: { rows: [], totals: {} } })
  const toast = useToast()
  if (!data) return null
  const openSiteMonths = (site_id, name) => {
    api(`/api/reports/monthly?date_from=${from}&date_to=${to}${siteQ(site_id)}`)
      .then((d) => { setSiteMonths({ site_id, name, rows: d.rows }); setMonth(null) })
      .catch((e) => toast(e.message, 'error'))
  }
  const t = data.totals
  return (
    <>
      <div className="text-xs text-slate-400">Зогсоол дээр дарж тухайн зогсоолын сар бүрийн дүнг харна.</div>
      <Table headers={['Зогсоол', 'Орсон', 'Гарсан', 'Хугацаа', 'Бэлэн (₮)', 'QPay (₮)', 'Карт (₮)', 'Төлөгдөөгүй (₮)', 'Нийт (₮)', 'Үйлдэл']}
        empty={data.rows.length === 0}>
        {data.rows.map((r) => (
          <tr key={r.site_id}>
            <td className="td font-medium text-accent underline decoration-dotted cursor-pointer"
              onClick={() => openSiteMonths(r.site_id, r.site_name)}>{r.site_name}</td>
            <td className="td font-mono">{fmt(r.entered)}</td>
            <td className="td font-mono">{fmt(r.exited)}</td>
            <td className="td font-mono">{fmtDur(r.total_minutes)}</td>
            <td className="td font-mono">{fmt(r.cash_amount)}</td>
            <td className="td font-mono">{fmt(r.qpay_amount)}</td>
            <td className="td font-mono">{fmt(r.pos_amount)}</td>
            <td className="td font-mono text-amber-400">{fmt(r.unpaid_amount)}</td>
            <td className="td font-mono text-accent font-semibold">{fmt(r.paid_amount)}</td>
            <td className="td">
              <button className="btn-secondary py-1 px-2 text-xs" aria-label={`${r.site_name} дэлгэрэнгүй татах`}
                onClick={() => dl(`/api/reports/site-sessions/excel?site_id=${r.site_id}&date_from=${from}&date_to=${to}`, `${r.site_name}_${from}_${to}.xlsx`)}>
                <Download size={13} /> Татах
              </button>
            </td>
          </tr>
        ))}
      </Table>
      <div className="card py-3 flex flex-wrap gap-5 text-sm">
        <span>Орсон: <b className="font-mono">{fmt(t.entered)}</b></span>
        <span>Гарсан: <b className="font-mono">{fmt(t.exited)}</b></span>
        <span>Бэлэн: <b className="font-mono">{fmt(t.cash_amount)}₮</b></span>
        <span>QPay: <b className="font-mono">{fmt(t.qpay_amount)}₮</b></span>
        <span>Карт: <b className="font-mono">{fmt(t.pos_amount)}₮</b></span>
        <span>Төлөгдөөгүй: <b className="font-mono text-amber-400">{fmt(t.unpaid_amount)}₮</b></span>
        <span>Нийт орлого: <b className="font-mono text-accent">{fmt(t.paid_amount)}₮</b></span>
      </div>
      {siteMonths && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-accent">{siteMonths.name} — сар бүрийн дүн</h3>
            <button className="text-xs text-slate-400 hover:text-slate-200 underline" onClick={() => setSiteMonths(null)}>Хаах</button>
          </div>
          <Table headers={['Сар', 'Гүйлгээ', 'Бэлэн (₮)', 'QPay (₮)', 'Карт (₮)', 'Нийт (₮)', '']} empty={siteMonths.rows.length === 0}>
            {siteMonths.rows.map((r) => (
              <tr key={r.month}>
                <td className="td font-mono text-accent underline decoration-dotted cursor-pointer" onClick={() => setMonth(r.month)}>{r.month}</td>
                <td className="td font-mono">{r.count}</td>
                <td className="td font-mono">{fmt(r.cash)}</td>
                <td className="td font-mono">{fmt(r.qpay)}</td>
                <td className="td font-mono">{fmt(r.pos)}</td>
                <td className="td font-mono text-accent font-semibold">{fmt(r.total)}</td>
                <td className="td text-right">
                  <button className="btn-secondary py-1 px-2 text-xs"
                    onClick={() => dl(`/api/reports/monthly/excel?date_from=${r.month}-01&date_to=${lastDayOf(r.month)}&site_id=${siteMonths.site_id}`, `${siteMonths.name}_${r.month}.xlsx`)}>
                    <Download size={13} /> Татах
                  </button>
                </td>
              </tr>
            ))}
          </Table>
          {month && <div className="mt-3"><MonthDays month={month} siteId={siteMonths.site_id} onClose={() => setMonth(null)} /></div>}
        </div>
      )}
    </>
  )
}

// ── Өдрөөр ──
export function DailyTab({ from, to, siteId }) {
  const [day, setDay] = useState(null)
  const { data } = useFetch(`/api/reports/daily?date_from=${from}&date_to=${to}${siteQ(siteId)}`, { initial: { rows: [], totals: {} } })
  if (!data) return null
  return (
    <>
      <div className="text-xs text-slate-400">Өдөр дээр дарж тухайн өдрийн бүх гүйлгээг цаг:минутаар харна.</div>
      <Table headers={['Огноо', 'Орсон', 'Гарсан', 'Бэлэн (₮)', 'QPay (₮)', 'Карт (₮)', 'Нийт орлого (₮)']} empty={data.rows.length === 0}>
        {data.rows.map((r) => (
          <tr key={r.date} onClick={() => setDay(r.date)} className="cursor-pointer hover:bg-surface-muted/40">
            <td className="td font-mono font-medium text-accent underline decoration-dotted">{r.date}</td>
            <td className="td font-mono">{fmt(r.entered)}</td>
            <td className="td font-mono">{fmt(r.exited)}</td>
            <td className="td font-mono">{fmt(r.cash_amount)}</td>
            <td className="td font-mono">{fmt(r.qpay_amount)}</td>
            <td className="td font-mono">{fmt(r.pos_amount)}</td>
            <td className="td font-mono text-accent font-semibold">{fmt(r.paid_amount)}</td>
          </tr>
        ))}
      </Table>
      <div className="card py-3 flex flex-wrap gap-5 text-sm">
        <span>Орсон: <b className="font-mono">{fmt(data.totals.entered)}</b></span>
        <span>Гарсан: <b className="font-mono">{fmt(data.totals.exited)}</b></span>
        <span>Нийт орлого: <b className="font-mono text-accent">{fmt(data.totals.paid_amount)}₮</b></span>
      </div>
      {day && <DayTxns date={day} siteId={siteId} onClose={() => setDay(null)} />}
    </>
  )
}

// ── Сараар ──
export function MonthlyTab({ from, to, siteId }) {
  const [month, setMonth] = useState(null)
  const { data } = useFetch(`/api/reports/monthly?date_from=${from}&date_to=${to}${siteQ(siteId)}`, { initial: { rows: [], totals: {} } })
  if (!data) return null
  return (
    <>
      <div className="text-xs text-slate-400">Сар дээр дарж тухайн сарын өдрүүдийг, дараа нь өдөр дээр дарж гүйлгээг харна.</div>
      <Table headers={['Сар', 'Гүйлгээ', 'Бэлэн (₮)', 'QPay (₮)', 'Карт (₮)', 'Нийт орлого (₮)']} empty={data.rows.length === 0}>
        {data.rows.map((r) => (
          <tr key={r.month} onClick={() => setMonth(r.month)} className="cursor-pointer hover:bg-surface-muted/40">
            <td className="td font-mono font-medium text-accent underline decoration-dotted">{r.month}</td>
            <td className="td font-mono">{r.count}</td>
            <td className="td font-mono">{fmt(r.cash)}</td>
            <td className="td font-mono">{fmt(r.qpay)}</td>
            <td className="td font-mono">{fmt(r.pos)}</td>
            <td className="td font-mono text-accent font-semibold">{fmt(r.total)}</td>
          </tr>
        ))}
      </Table>
      <div className="card py-3 flex flex-wrap gap-5 text-sm">
        <span>Бэлэн: <b className="font-mono">{fmt(data.totals.cash)}₮</b></span>
        <span>QPay: <b className="font-mono">{fmt(data.totals.qpay)}₮</b></span>
        <span>Карт: <b className="font-mono">{fmt(data.totals.pos)}₮</b></span>
        <span>Нийт: <b className="font-mono text-accent">{fmt(data.totals.total)}₮</b></span>
      </div>
      {month && <MonthDays month={month} siteId={siteId} onClose={() => setMonth(null)} />}
    </>
  )
}

// ── Ээлжээр ──
export function ShiftsTab({ from, to, siteId }) {
  const { data: shifts } = useFetch(`/api/cashier/shifts?date_from=${from}&date_to=${to}${siteQ(siteId)}`, { initial: [] })
  return (
    <>
      <div className="text-xs text-slate-400">
        Жинхэнэ ажилласан ээлж бүр (кассчин POS/системд нэвтэрснээр эхэлж, дараагийн хүн нэвтрэхэд/хаахад дуусна).
        Дүн нь тухайн ээлжид тэр кассчны авсан бэлэн/картын гүйлгээ.
      </div>
      <Table headers={['Кассчин', 'Зогсоол', 'Эхэлсэн', 'Дууссан', 'Үргэлжилсэн', 'Гүйлгээ', 'Бэлэн (₮)', 'Карт (₮)', 'Нийт (₮)', 'Төлөв']}
        empty={shifts.length === 0}>
        {shifts.map((s) => (
          <tr key={s.id}>
            <td className="td font-medium">{s.cashier}</td>
            <td className="td text-xs">{s.site_name}</td>
            <td className="td font-mono text-xs">{fmtDate(s.opened_at)}</td>
            <td className="td font-mono text-xs">{s.closed_at ? fmtDate(s.closed_at) : '—'}</td>
            <td className="td font-mono text-xs">{fmtDur(s.duration_minutes)}</td>
            <td className="td font-mono">{s.count}</td>
            <td className="td font-mono">{fmt(s.by_provider?.CASH?.amount || 0)}</td>
            <td className="td font-mono">{fmt(s.by_provider?.POS?.amount || 0)}</td>
            <td className="td font-mono text-accent font-semibold">{fmt(s.total)}</td>
            <td className="td"><Badge value={s.status === 'OPEN' ? 'active' : 'CLOSED'} /></td>
          </tr>
        ))}
      </Table>
    </>
  )
}

// ── Төлбөрийн төрлөөр ──
export function ByPaymentTab({ from, to, siteId }) {
  const { data: byPay } = useFetch(`/api/reports/by-payment?date_from=${from}&date_to=${to}${siteQ(siteId)}`, { initial: null })
  if (!byPay) return null
  return (
    <>
      <div className="card py-3 flex flex-wrap gap-6 text-sm">
        <span className="text-slate-400">Нийт орлого (төлсөн): <b className="font-mono text-accent">{fmt(byPay.total)}₮</b></span>
        <span className="text-slate-500 text-xs">Хэрэгслээр ба машины төрлөөр 2 задаргаа энэ дүнд тэнцвэржинэ (бүгд ТӨЛСӨН гүйлгээгээр).</span>
        <span className="text-slate-400 ml-auto">Үнэгүй гарсан: <b className="font-mono">{byPay.free_count}</b> <span className="text-xs text-slate-500">(орлогогүй)</span></span>
      </div>
      <div className="grid lg:grid-cols-2 gap-6">
        <div className="card">
          <h2 className="font-semibold mb-3">Төлбөрийн хэрэгслээр</h2>
          <Table headers={['Хэрэгсэл', 'Гүйлгээ', 'Дүн (₮)']} empty={byPay.by_method.length === 0}>
            {byPay.by_method.map((r) => (
              <tr key={r.key}>
                <td className="td font-medium">{r.key}</td>
                <td className="td font-mono">{r.count}</td>
                <td className="td font-mono text-accent font-semibold">{fmt(r.amount)}₮</td>
              </tr>
            ))}
          </Table>
        </div>
        <div className="card">
          <h2 className="font-semibold mb-3">Машины төрлөөр (төлсөн гүйлгээ)</h2>
          <Table headers={['Төрөл', 'Гүйлгээ', 'Дүн (₮)']} empty={byPay.by_car.length === 0}>
            {byPay.by_car.map((r) => (
              <tr key={r.key}>
                <td className={`td font-medium ${r.key === 'Гэрээт' ? 'text-cyan-400' : r.key === 'Хөнгөлөлттэй' ? 'text-amber-400' : ''}`}>{r.key}</td>
                <td className="td font-mono">{r.count}</td>
                <td className="td font-mono">{fmt(r.amount)}₮</td>
              </tr>
            ))}
          </Table>
        </div>
      </div>
    </>
  )
}

// ── Бичилт (олон шүүлттэй) ──
export function TransactionsTab({ from, to, sites }) {
  const dl = useDownload()
  const [f, setF] = useState({ site_id: '', provider: '', car_type: '', status: '', date_field: 'entry' })
  const qs = () => {
    const p = new URLSearchParams({ date_from: from, date_to: to })
    for (const k of ['site_id', 'provider', 'car_type', 'status', 'date_field']) if (f[k]) p.set(k, f[k])
    return p.toString()
  }
  const { data: txns } = useFetch(`/api/reports/transactions?${qs()}`, { initial: null })
  return (
    <>
      <div className="flex flex-wrap gap-2 items-center">
        <button className="btn-primary" onClick={() => dl(`/api/reports/transactions/excel?${qs()}`, `bichilt_${from}_${to}.xlsx`)}>
          <Download size={16} /> Excel (шүүлтээр)
        </button>
        <select className="input w-auto" value={f.date_field} onChange={(e) => setF({ ...f, date_field: e.target.value })} aria-label="Огнооны төрөл" title="Огноог аль үйл явдлаар шүүх">
          <option value="entry">Орсон огноогоор</option>
          <option value="paid">Төлсөн огноогоор (орлого)</option>
        </select>
        <select className="input w-auto" value={f.site_id} onChange={(e) => setF({ ...f, site_id: e.target.value })} aria-label="Зогсоол">
          <option value="">Бүх зогсоол</option>
          {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select className="input w-auto" value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })} aria-label="Төлбөрийн хэрэгсэл">
          <option value="">Бүх хэрэгсэл</option>
          <option value="CASH">Бэлэн</option>
          <option value="QPAY">QPay</option>
          <option value="POS">Банкны карт</option>
        </select>
        <select className="input w-auto" value={f.car_type} onChange={(e) => setF({ ...f, car_type: e.target.value })} aria-label="Машины төрөл">
          <option value="">Бүх төрөл</option>
          <option value="normal">Энгийн</option>
          <option value="contract">Гэрээт</option>
          <option value="discount">Хөнгөлөлттэй</option>
        </select>
        <select className="input w-auto" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })} aria-label="Төлөв">
          <option value="">Бүх төлөв</option>
          <option value="PAID">Төлсөн</option>
          <option value="FREE">Үнэгүй</option>
          <option value="AWAITING_PAYMENT">Төлбөр хүлээж буй</option>
        </select>
        {txns && <span className="text-sm text-slate-400 ml-auto">Нийт <b className="text-slate-200">{txns.total}</b> бичилт · <b className="font-mono text-accent">{fmt(txns.totals.total_fee)}₮</b></span>}
      </div>
      <Table headers={['Дугаар', 'Зогсоол', 'Орсон', 'Гарсан', 'Хугацаа', 'Төрөл', 'Нийт (₮)', 'Хэрэгсэл', 'Төлөв', 'Кассчин', 'ДДТД']}
        empty={!txns || txns.rows.length === 0}>
        {txns?.rows.map((r) => (
          <tr key={r.session_id}>
            <td className="td font-mono font-semibold">{r.plate_number}</td>
            <td className="td text-xs">{r.site_name}</td>
            <td className="td font-mono text-xs">{(r.entry_time || '').replace('T', ' ').slice(5, 16)}</td>
            <td className="td font-mono text-xs">{(r.exit_time || '').replace('T', ' ').slice(5, 16)}</td>
            <td className="td font-mono text-xs">{fmtDur(r.duration_minutes)}</td>
            <td className={`td text-xs font-medium ${r.car_type === 'Гэрээт' ? 'text-cyan-400' : r.car_type === 'Хөнгөлөлттэй' ? 'text-amber-400' : ''}`}>
              {r.car_type}{r.discount_name ? ` (${r.discount_name})` : ''}
            </td>
            <td className="td font-mono">{fmt(r.total_fee)}</td>
            <td className="td text-xs">{r.provider || '-'}</td>
            <td className="td text-xs">{r.status}</td>
            <td className="td text-xs">{r.cashier || '-'}</td>
            <td className="td font-mono text-[10px] max-w-[10rem] truncate" title={r.ebarimt_id || ''}>{r.ebarimt_id || '-'}</td>
          </tr>
        ))}
      </Table>
    </>
  )
}
