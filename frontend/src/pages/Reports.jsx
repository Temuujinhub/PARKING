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
  const [shifts, setShifts] = useState([])

  const load = () => {
    const qs = `date_from=${from}&date_to=${to}`
    if (tab === 'revenue') api(`/api/reports/revenue?${qs}`).then(setRevenue).catch(() => {})
    else api(`/api/cashier/shifts?${qs}`).then(setShifts).catch(() => {})
  }
  useEffect(load, [tab, from, to])

  const downloadExcel = async () => {
    try {
      const blob = await api(`/api/reports/revenue/excel?date_from=${from}&date_to=${to}`, { blob: true })
      const url = URL.createObjectURL(blob)
      const a = Object.assign(document.createElement('a'), { href: url, download: `tailan_${from}_${to}.xlsx` })
      a.click(); URL.revokeObjectURL(url)
    } catch (e) { toast(e.message, 'error') }
  }

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
        </div>
      </div>

      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {[['revenue', 'Зогсоолын орлого'], ['shifts', 'Касс хаалтын тайлан']].map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>

      {tab === 'revenue' && revenue && (
        <>
          <Table headers={['Зогсоол', 'Орсон', 'Гарсан', 'Нийт хугацаа', 'Төлөгдөөгүй (₮)', 'Төлбөр (₮)']}
            empty={revenue.rows.length === 0}>
            {revenue.rows.map((r) => (
              <tr key={r.site_id}>
                <td className="td font-medium">{r.site_name}</td>
                <td className="td font-mono">{fmt(r.entered)}</td>
                <td className="td font-mono">{fmt(r.exited)}</td>
                <td className="td font-mono">{fmtDur(r.total_minutes)}</td>
                <td className="td font-mono text-amber-400">{fmt(r.unpaid_amount)}</td>
                <td className="td font-mono text-accent font-semibold">{fmt(r.paid_amount)}</td>
              </tr>
            ))}
          </Table>
          <div className="card py-3 flex flex-wrap gap-6 text-sm">
            <span>Нийт орсон: <b className="font-mono">{fmt(revenue.totals.entered)}</b></span>
            <span>Нийт гарсан: <b className="font-mono">{fmt(revenue.totals.exited)}</b></span>
            <span>Нийт хугацаа: <b className="font-mono">{fmtDur(revenue.totals.total_minutes)}</b></span>
            <span>Төлөгдөөгүй: <b className="font-mono text-amber-400">{fmt(revenue.totals.unpaid_amount)}₮</b></span>
            <span>Нийт орлого: <b className="font-mono text-accent">{fmt(revenue.totals.paid_amount)}₮</b></span>
          </div>
        </>
      )}

      {tab === 'shifts' && (
        <Table headers={['Кассчин', 'Төлөв', 'Нээсэн цаг', 'Хаасан цаг', 'Эхэлсэн дүн', 'Гүйлгээ', 'Нийт орлого']}
          empty={shifts.length === 0}>
          {shifts.map((s) => (
            <tr key={s.id}>
              <td className="td font-medium">{s.cashier}</td>
              <td className="td"><Badge value={s.status === 'OPEN' ? 'active' : 'CLOSED'} /></td>
              <td className="td font-mono text-xs">{fmtDate(s.opened_at)}</td>
              <td className="td font-mono text-xs">{fmtDate(s.closed_at)}</td>
              <td className="td font-mono">{fmt(s.opening_amount)}₮</td>
              <td className="td font-mono">{s.count}</td>
              <td className="td font-mono text-accent font-semibold">{fmt(s.total)}₮</td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  )
}
