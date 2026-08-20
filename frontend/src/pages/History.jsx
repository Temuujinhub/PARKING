// Түүх — бүх session-ийн жагсаалт, шүүлтүүр
import { RotateCcw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate, fmtDur } from '../api'
import { useAuth } from '../auth'
import { useFetch } from '../hooks/useFetch'
import { SnapshotButton } from '../components/Snapshot'
import { Badge, DateRange, Table, useToast } from '../components/ui'
import { normalizePlate } from '../validation'

// Хаагдсан бүртгэлийг буцаан зогсоолд оруулж болох төлвүүд (төлбөргүй хаагдсан)
const REOPENABLE = new Set(['MANUAL_CLOSED', 'FREE', 'CLOSED'])

// Төлбөрийн хэрэгслийн богино шошго — «Гарсан» гэдэг төлөв ЮУГААР төлөгдснийг
// хэлдэггүй тул түүхэн дээр тусад нь харуулна (provider → payment_method).
const PAY_LABEL = {
  QPAY: 'QPay QR', POS: 'Карт (ПОС)', CASH: 'Бэлэн', TRANSFER: 'Дансаар',
}

function PaymentCell({ s }) {
  const pays = s.payments || []
  if (pays.length) {
    return (
      <div className="space-y-0.5">
        {pays.map((p, i) => (
          <div key={i} className="text-[11px] whitespace-nowrap">
            <span className="text-emerald-400">{PAY_LABEL[p.provider] || p.provider}</span>
            {p.cashier && <span className="text-slate-500"> · {p.cashier}</span>}
            {pays.length > 1 && <span className="text-slate-500"> {fmt(p.amount)}₮</span>}
          </div>
        ))}
      </div>
    )
  }
  // Төлбөргүй хаагдсан — яагаад гэдгийг төлөв нь аль хэдийн хэлнэ (Үнэгүй/Гарах уншилтгүй)
  if (s.status === 'FREE') return <span className="text-cyan-400 text-[11px]">Үнэгүй</span>
  return <span className="text-slate-600">-</span>
}

function ClosedByCell({ s }) {
  const c = s.closed_by
  if (!c) return <span className="text-slate-600">-</span>
  return (
    <span className={`text-[11px] whitespace-nowrap cursor-help ${c.auto ? 'text-slate-400' : 'text-amber-300'}`}
      title={`${c.label}${c.auto ? '' : ` — ${c.by}`}${c.at ? `\n${fmtDate(c.at)}` : ''}`}>
      {c.auto ? 'Систем' : c.by}
    </span>
  )
}

const STATUSES = [
  ['', 'Бүгд'], ['OPEN', 'Зогсож буй'], ['AWAITING_PAYMENT', 'Төлбөр хүлээж буй'],
  ['PAID', 'Төлсөн'], ['CLOSED', 'Гарсан'], ['FREE', 'Үнэгүй'], ['MANUAL_CLOSED', 'Гарах уншилтгүй'],
  // Nested зогсоолтой газарт (Рашбулаг ЭТТ): доторх зогсоолд орж, төлбөрийн
  // тоолуур нь зогссон машинууд. Төлбөр яагаад бага байсныг тайлбарлана.
  ['INNER', 'Дотор зогссон'],
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
  Object.entries(filters).forEach(([k, v]) => {
    if (!v) return
    // «Дотор зогссон» нь төлөв биш — тусдаа шүүлтүүр (аль хэдийн гарсан ч харагдана)
    if (k === 'status' && v === 'INNER') params.set('inner', 'ever')
    else params.set(k, v)
  })
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
        <input className="input font-mono uppercase" placeholder="Дугаар…" value={filters.plate} maxLength={7}
          onChange={(e) => setFilters({ ...filters, plate: normalizePlate(e.target.value) })} aria-label="Дугаар" />
        <DateRange className="" from={filters.date_from} to={filters.date_to}
          setFrom={(v) => setFilters({ ...filters, date_from: v })}
          setTo={(v) => setFilters({ ...filters, date_to: v })} />
      </div>

      <Table headers={['Дугаар', 'Зогсоол', 'Орсон', 'Гарсан', 'Хугацаа', 'Дүн', 'Төлбөр', 'Хөнгөлөлт',
        'Төлөв', 'Хаасан', 'Зураг', ...(isAdmin ? [''] : [])]}
        empty={data.rows.length === 0}>
        {data.rows.map((s) => (
          <tr key={s.id} className="hover:bg-surface-muted/30">
            <td className="td font-mono font-bold">{s.plate_number}</td>
            <td className="td">{s.site_name}</td>
            <td className="td font-mono text-xs">{fmtDate(s.entry_time)}</td>
            <td className="td font-mono text-xs">{fmtDate(s.exit_time)}</td>
            <td className="td font-mono">
              {fmtDur(s.duration_minutes)}
              {/* Доторх (nested) зогсоолд өнгөрүүлсэн хугацаа төлбөрөөс хасагдсан —
                  «яагаад ийм бага дүн гарав» гэдгийг мөрөн дээр нь шууд харуулна */}
              {(s.paused_minutes > 0 || s.paused_since) && (
                <div className="text-[10px] text-sky-300"
                  title={s.paused_since
                    ? 'ОДОО доторх зогсоолд байна — тоолуур зогссон'
                    : 'Доторх зогсоолд өнгөрүүлсэн — энэ хугацаа төлбөрөөс хасагдсан'}>
                  {s.paused_since ? '⏸ дотор' : `дотор ${fmtDur(s.paused_minutes)}`}
                </div>
              )}
            </td>
            <td className="td font-mono font-semibold">{s.total_fee !== null ? `${fmt(s.total_fee)}₮` : '-'}</td>
            <td className="td"><PaymentCell s={s} /></td>
            <td className="td text-xs">{s.discount_name || '-'}</td>
            <td className="td"><Badge value={s.status} /></td>
            <td className="td"><ClosedByCell s={s} /></td>
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
