// Түүх — бүх session-ийн жагсаалт, шүүлтүүр
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate, fmtDur } from '../api'
import { Badge, Table } from '../components/ui'

const STATUSES = [
  ['', 'Бүгд'], ['OPEN', 'Зогсож буй'], ['AWAITING_PAYMENT', 'Төлбөр хүлээж буй'],
  ['PAID', 'Төлсөн'], ['CLOSED', 'Гарсан'], ['FREE', 'Үнэгүй'], ['MANUAL_CLOSED', 'Гараар хаасан'],
]

export default function History() {
  const [sites, setSites] = useState([])
  const [filters, setFilters] = useState({ site_id: '', status: '', plate: '', date_from: '', date_to: '' })
  const [data, setData] = useState({ total: 0, rows: [] })
  const [page, setPage] = useState(0)
  const limit = 50

  useEffect(() => { api('/api/admin/sites').then(setSites) }, [])

  const load = (p = page) => {
    const params = new URLSearchParams({ limit, offset: p * limit })
    Object.entries(filters).forEach(([k, v]) => v && params.set(k, v))
    api(`/api/sessions?${params}`).then(setData).catch(() => {})
  }
  useEffect(() => { load(0); setPage(0) }, [filters])

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Түүх</h1>
      <div className="card grid grid-cols-2 lg:grid-cols-5 gap-3">
        <select className="input" value={filters.site_id} onChange={(e) => setFilters({ ...filters, site_id: e.target.value })} aria-label="Зогсоол">
          <option value="">Бүх зогсоол</option>
          {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select className="input" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} aria-label="Төлөв">
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <input className="input font-mono" placeholder="Дугаар…" value={filters.plate}
          onChange={(e) => setFilters({ ...filters, plate: e.target.value.toUpperCase() })} aria-label="Дугаар" />
        <input type="date" className="input" value={filters.date_from}
          onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} aria-label="Эхлэх огноо" />
        <input type="date" className="input" value={filters.date_to}
          onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} aria-label="Дуусах огноо" />
      </div>

      <Table headers={['Дугаар', 'Зогсоол', 'Орсон', 'Гарсан', 'Хугацаа', 'Дүн', 'Хөнгөлөлт', 'Төлөв']}
        empty={data.rows.length === 0}>
        {data.rows.map((s) => (
          <tr key={s.id} className="hover:bg-surface-muted/30">
            <td className="td font-mono font-bold">{s.plate_number}</td>
            <td className="td">{s.site_name}</td>
            <td className="td font-mono text-xs">{fmtDate(s.entry_time)}</td>
            <td className="td font-mono text-xs">{fmtDate(s.exit_time)}</td>
            <td className="td font-mono">{fmtDur(s.duration_minutes)}</td>
            <td className="td font-mono font-semibold">{s.total_fee !== null ? `${fmt(s.total_fee)}₮` : '-'}</td>
            <td className="td text-xs">{s.discount_name || '-'}</td>
            <td className="td"><Badge value={s.status} /></td>
          </tr>
        ))}
      </Table>

      <div className="flex items-center justify-between text-sm text-slate-400">
        <span>Нийт: {fmt(data.total)} мөр</span>
        <div className="flex gap-2">
          <button className="btn-secondary py-1" disabled={page === 0}
            onClick={() => { setPage(page - 1); load(page - 1) }}>Өмнөх</button>
          <button className="btn-secondary py-1" disabled={(page + 1) * limit >= data.total}
            onClick={() => { setPage(page + 1); load(page + 1) }}>Дараах</button>
        </div>
      </div>
    </div>
  )
}
