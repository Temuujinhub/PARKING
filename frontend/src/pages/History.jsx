// Түүх — бүх session-ийн жагсаалт, шүүлтүүр
import { RotateCcw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate, fmtDur } from '../api'
import { useAuth } from '../auth'
import { useFetch } from '../hooks/useFetch'
import { SnapshotButton } from '../components/Snapshot'
import { Badge, Table, useToast } from '../components/ui'

// Хаагдсан бүртгэлийг буцаан зогсоолд оруулж болох төлвүүд (төлбөргүй хаагдсан)
const REOPENABLE = new Set(['MANUAL_CLOSED', 'FREE', 'CLOSED'])

const STATUSES = [
  ['', 'Бүгд'], ['OPEN', 'Зогсож буй'], ['AWAITING_PAYMENT', 'Төлбөр хүлээж буй'],
  ['PAID', 'Төлсөн'], ['CLOSED', 'Гарсан'], ['FREE', 'Үнэгүй'], ['MANUAL_CLOSED', 'Гараар хаасан'],
]

export default function History() {
  const { user } = useAuth()
  const toast = useToast()
  const isAdmin = ['ADMIN', 'SUPER_ADMIN'].includes(user?.role)
  const [filters, setFilters] = useState({ site_id: '', status: '', plate: '', date_from: '', date_to: '' })
  const [page, setPage] = useState(0)
  const limit = 50

  const { data: sites } = useFetch('/api/admin/sites', { initial: [], silent: true })

  const params = new URLSearchParams({ limit, offset: page * limit })
  Object.entries(filters).forEach(([k, v]) => v && params.set(k, v))
  const { data, reload } = useFetch(`/api/sessions?${params}`, { initial: { total: 0, rows: [] } })

  // Андуурч хаасан бүртгэлийг буцаан зогсоолд оруулах (status→OPEN, цаг үргэлжилнэ)
  const reopen = async (s) => {
    if (!window.confirm(`${s.plate_number}-г буцаан зогсоолд оруулах уу? Орсон цаг хэвээр үлдэж хугацаа үргэлжлэн бодогдоно.`)) return
    try {
      await api(`/api/sessions/${s.id}/reopen`, { method: 'POST' })
      toast(`${s.plate_number} буцаан зогсоолд орлоо`)
      reload()
    } catch (err) { toast(err.message, 'error') }
  }

  // Шүүлтүүр өөрчлөгдвөл эхний хуудас руу (path өөрчлөгдмөгц автоматаар дахин татна)
  useEffect(() => { setPage(0) }, [filters])

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Түүх</h1>
      <div className={`card grid grid-cols-2 gap-3 ${sites.length > 1 ? 'lg:grid-cols-5' : 'lg:grid-cols-4'}`}>
        {sites.length > 1 && (
          <select className="input" value={filters.site_id} onChange={(e) => setFilters({ ...filters, site_id: e.target.value })} aria-label="Зогсоол">
            <option value="">Бүх зогсоол</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
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

      <Table headers={['Дугаар', 'Зогсоол', 'Орсон', 'Гарсан', 'Хугацаа', 'Дүн', 'Хөнгөлөлт', 'Төлөв', 'Зураг',
        ...(isAdmin ? [''] : [])]}
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
            <td className="td"><SnapshotButton session={s} /></td>
            {isAdmin && (
              <td className="td text-right">
                {REOPENABLE.has(s.status) && !s.paid_at && (
                  <button className="btn-secondary py-1 text-xs" title="Буцаан зогсоолд оруулах"
                    onClick={() => reopen(s)}>
                    <RotateCcw size={13} /> Сэргээх
                  </button>
                )}
              </td>
            )}
          </tr>
        ))}
      </Table>

      <div className="flex items-center justify-between text-sm text-slate-400">
        <span>Нийт: {fmt(data.total)} мөр</span>
        <div className="flex gap-2">
          <button className="btn-secondary py-1" disabled={page === 0}
            onClick={() => setPage(page - 1)}>Өмнөх</button>
          <button className="btn-secondary py-1" disabled={(page + 1) * limit >= data.total}
            onClick={() => setPage(page + 1)}>Дараах</button>
        </div>
      </div>
    </div>
  )
}
