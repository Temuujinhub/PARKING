// Тайлан: зогсоолын орлого + кассын ээлжийн тайлан + Excel
import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate, fmtDur } from '../api'
import { Badge, Table, useToast } from '../components/ui'

export default function Reports() {
  const toast = useToast()
  const [tab, setTab] = useState('revenue')
  const today = new Date().toISOString().slice(0, 10)
  const weekAgo = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10)
  const [from, setFrom] = useState(weekAgo)
  const [to, setTo] = useState(today)
  const [revenue, setRevenue] = useState(null)
  const [daily, setDaily] = useState(null)
  const [monthly, setMonthly] = useState(null)
  const [byShift, setByShift] = useState(null)
  const [txns, setTxns] = useState(null)
  const [byPay, setByPay] = useState(null)
  const [sites, setSites] = useState([])
  // Бичилт таб-ын шүүлтүүд
  const [f, setF] = useState({ site_id: '', provider: '', car_type: '', status: '' })

  useEffect(() => { api('/api/admin/sites').then(setSites).catch(() => {}) }, [])

  const txnQs = () => {
    const p = new URLSearchParams({ date_from: from, date_to: to })
    for (const k of ['site_id', 'provider', 'car_type', 'status']) if (f[k]) p.set(k, f[k])
    return p.toString()
  }

  const load = () => {
    const qs = `date_from=${from}&date_to=${to}`
    if (tab === 'revenue') api(`/api/reports/revenue?${qs}`).then(setRevenue).catch(() => {})
    else if (tab === 'daily') api(`/api/reports/daily?${qs}`).then(setDaily).catch(() => {})
    else if (tab === 'monthly') api(`/api/reports/monthly?${qs}`).then(setMonthly).catch(() => {})
    else if (tab === 'shifts') api(`/api/reports/by-shift?${qs}`).then(setByShift).catch(() => {})
    else if (tab === 'bypayment') api(`/api/reports/by-payment?${qs}${f.site_id ? `&site_id=${f.site_id}` : ''}`).then(setByPay).catch(() => {})
    else if (tab === 'transactions') api(`/api/reports/transactions?${txnQs()}`).then(setTxns).catch(() => {})
  }
  useEffect(load, [tab, from, to, f])

  const downloadBlob = async (path, filename) => {
    try {
      const blob = await api(path, { blob: true })
      const url = URL.createObjectURL(blob)
      const a = Object.assign(document.createElement('a'), { href: url, download: filename })
      a.click(); URL.revokeObjectURL(url)
    } catch (e) { toast(e.message, 'error') }
  }
  const downloadExcel = () =>
    downloadBlob(`/api/reports/revenue/excel?date_from=${from}&date_to=${to}`, `tailan_${from}_${to}.xlsx`)

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Тайлан</h1>
        <div className="flex items-center gap-2">
          <input type="date" className="input w-40" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Эхлэх огноо" />
          <span className="text-slate-500">—</span>
          <input type="date" className="input w-40" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Дуусах огноо" />
          {tab === 'revenue' && (
            <button className="btn-primary" onClick={downloadExcel}><Download size={16} /> Excel</button>
          )}
          {tab === 'daily' && (
            <button className="btn-primary"
              onClick={() => downloadBlob(`/api/reports/daily/excel?date_from=${from}&date_to=${to}`, `odriin_tailan_${from}_${to}.xlsx`)}>
              <Download size={16} /> Excel
            </button>
          )}
          {tab === 'monthly' && (
            <button className="btn-primary"
              onClick={() => downloadBlob(`/api/reports/monthly/excel?date_from=${from}&date_to=${to}`, `saraar_${from}_${to}.xlsx`)}>
              <Download size={16} /> Excel
            </button>
          )}
          {tab === 'shifts' && (
            <button className="btn-primary"
              onClick={() => downloadBlob(`/api/reports/by-shift/excel?date_from=${from}&date_to=${to}`, `eeljeer_${from}_${to}.xlsx`)}>
              <Download size={16} /> Excel
            </button>
          )}
          {tab === 'bypayment' && (
            <button className="btn-primary"
              onClick={() => downloadBlob(`/api/reports/by-payment/excel?date_from=${from}&date_to=${to}${f.site_id ? `&site_id=${f.site_id}` : ''}`, `tolboriin_torol_${from}_${to}.xlsx`)}>
              <Download size={16} /> Excel
            </button>
          )}
          {tab === 'transactions' && (
            <button className="btn-primary"
              onClick={() => downloadBlob(`/api/reports/transactions/excel?${txnQs()}`, `bichilt_${from}_${to}.xlsx`)}>
              <Download size={16} /> Excel (шүүлтээр)
            </button>
          )}
        </div>
      </div>

      <div className="flex gap-1 border-b border-surface-border/60 overflow-x-auto" role="tablist">
        {[['revenue', 'Зогсоолоор'], ['monthly', 'Сараар'], ['daily', 'Өдрөөр'], ['shifts', 'Ээлжээр'],
          ['bypayment', 'Төлбөрийн төрлөөр'], ['transactions', 'Бичилт']].map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>

      {tab === 'revenue' && revenue && (
        <>
          <Table headers={['Зогсоол', 'Орсон', 'Гарсан', 'Хугацаа', 'Бэлэн (₮)', 'QPay (₮)', 'Карт (₮)', 'Төлөгдөөгүй (₮)', 'Нийт (₮)', 'Үйлдэл']}
            empty={revenue.rows.length === 0}>
            {revenue.rows.map((r) => (
              <tr key={r.site_id}>
                <td className="td font-medium">{r.site_name}</td>
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
                    onClick={() => downloadBlob(
                      `/api/reports/site-sessions/excel?site_id=${r.site_id}&date_from=${from}&date_to=${to}`,
                      `${r.site_name}_${from}_${to}.xlsx`)}>
                    <Download size={13} /> Татах
                  </button>
                </td>
              </tr>
            ))}
          </Table>
          <div className="card py-3 flex flex-wrap gap-5 text-sm">
            <span>Орсон: <b className="font-mono">{fmt(revenue.totals.entered)}</b></span>
            <span>Гарсан: <b className="font-mono">{fmt(revenue.totals.exited)}</b></span>
            <span>Бэлэн: <b className="font-mono">{fmt(revenue.totals.cash_amount)}₮</b></span>
            <span>QPay: <b className="font-mono">{fmt(revenue.totals.qpay_amount)}₮</b></span>
            <span>Карт: <b className="font-mono">{fmt(revenue.totals.pos_amount)}₮</b></span>
            <span>Төлөгдөөгүй: <b className="font-mono text-amber-400">{fmt(revenue.totals.unpaid_amount)}₮</b></span>
            <span>Нийт орлого: <b className="font-mono text-accent">{fmt(revenue.totals.paid_amount)}₮</b></span>
          </div>
        </>
      )}

      {tab === 'daily' && daily && (
        <>
          <Table headers={['Огноо', 'Орсон', 'Гарсан', 'Бэлэн (₮)', 'QPay (₮)', 'Карт (₮)', 'Нийт орлого (₮)']}
            empty={daily.rows.length === 0}>
            {daily.rows.map((r) => (
              <tr key={r.date}>
                <td className="td font-mono font-medium">{r.date}</td>
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
            <span>Орсон: <b className="font-mono">{fmt(daily.totals.entered)}</b></span>
            <span>Гарсан: <b className="font-mono">{fmt(daily.totals.exited)}</b></span>
            <span>Нийт орлого: <b className="font-mono text-accent">{fmt(daily.totals.paid_amount)}₮</b></span>
          </div>
        </>
      )}

      {tab === 'shifts' && byShift && (
        <>
          <div className="text-xs text-slate-400">
            Ээлжийн өдрийг <b className="text-slate-200">{String(byShift.shift_hour).padStart(2, '0')}:00</b> цагаар тасалж бүлэглэв
            (шөнө дунд биш). Тохиргоо: <span className="font-mono">PARKING_SHIFT_CHANGE_HOUR</span>.
          </div>
          <Table headers={['Ээлжийн өдөр', 'Зааг', 'Орсон', 'Гарсан', 'Бэлэн (₮)', 'QPay (₮)', 'Карт (₮)', 'Нийт орлого (₮)']}
            empty={byShift.rows.length === 0}>
            {byShift.rows.map((r) => (
              <tr key={r.date}>
                <td className="td font-mono font-medium">{r.date}</td>
                <td className="td font-mono text-xs text-slate-500">{r.window}</td>
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
            <span>Орсон: <b className="font-mono">{fmt(byShift.totals.entered)}</b></span>
            <span>Гарсан: <b className="font-mono">{fmt(byShift.totals.exited)}</b></span>
            <span>Нийт орлого: <b className="font-mono text-accent">{fmt(byShift.totals.paid_amount)}₮</b></span>
          </div>
        </>
      )}

      {tab === 'monthly' && monthly && (
        <>
          <Table headers={['Сар', 'Гүйлгээ', 'Бэлэн (₮)', 'QPay (₮)', 'Карт (₮)', 'Нийт орлого (₮)']}
            empty={monthly.rows.length === 0}>
            {monthly.rows.map((r) => (
              <tr key={r.month}>
                <td className="td font-mono font-medium">{r.month}</td>
                <td className="td font-mono">{r.count}</td>
                <td className="td font-mono">{fmt(r.cash)}</td>
                <td className="td font-mono">{fmt(r.qpay)}</td>
                <td className="td font-mono">{fmt(r.pos)}</td>
                <td className="td font-mono text-accent font-semibold">{fmt(r.total)}</td>
              </tr>
            ))}
          </Table>
          <div className="card py-3 flex flex-wrap gap-5 text-sm">
            <span>Бэлэн: <b className="font-mono">{fmt(monthly.totals.cash)}₮</b></span>
            <span>QPay: <b className="font-mono">{fmt(monthly.totals.qpay)}₮</b></span>
            <span>Карт: <b className="font-mono">{fmt(monthly.totals.pos)}₮</b></span>
            <span>Нийт: <b className="font-mono text-accent">{fmt(monthly.totals.total)}₮</b></span>
          </div>
        </>
      )}

      {/* Төлбөрийн төрлөөр — хэрэгсэл ба машины төрлөөр */}
      {tab === 'bypayment' && byPay && (
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
            <h2 className="font-semibold mb-3">Машины төрлөөр (гэрээт / хөнгөлөлт / энгийн / үнэгүй)</h2>
            <Table headers={['Төрөл', 'Тоо', 'Дүн (₮)']} empty={byPay.by_car.length === 0}>
              {byPay.by_car.map((r) => (
                <tr key={r.key}>
                  <td className={`td font-medium ${r.key === 'Гэрээт' ? 'text-cyan-400' : r.key === 'Хөнгөлөлттэй' ? 'text-amber-400' : r.key === 'Үнэгүй' ? 'text-slate-400' : ''}`}>{r.key}</td>
                  <td className="td font-mono">{r.count}</td>
                  <td className="td font-mono">{fmt(r.amount)}₮</td>
                </tr>
              ))}
            </Table>
          </div>
        </div>
      )}

      {/* Бичилт — дэлгэрэнгүй, олон шүүлттэй, шүүлтээр Excel татна */}
      {tab === 'transactions' && (
        <>
          <div className="flex flex-wrap gap-2 items-center">
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
      )}
    </div>
  )
}
