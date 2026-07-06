// Нөхөн төлбөр — төлбөргүй гарсан машины нэхэмжлэл (JGA спек)
// 3+ төлөгдөөгүй нэхэмжлэлтэй дугаар автоматаар хар жагсаалтад орно.
import { Banknote, MoonStar, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate } from '../api'
import { useAuth } from '../auth'
import { Badge, Table, useToast } from '../components/ui'

const REASONS = { unpaid_exit: 'Төлбөргүй гаргасан', night_close: 'Шөнийн хаалт', manual: 'Гараар' }

export default function Compensations() {
  const toast = useToast()
  const { can, user } = useAuth()
  const [data, setData] = useState({ rows: [], total_pending: 0 })
  const [status, setStatus] = useState('PENDING')
  const [plate, setPlate] = useState('')

  const load = () => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (plate.trim()) params.set('plate', plate.trim())
    api(`/api/compensations?${params}`).then(setData).catch(() => {})
  }
  useEffect(load, [status])

  const pay = async (c) => {
    if (!confirm(`${c.plate_number} — ${fmt(c.amount)}₮ нөхөн төлбөрийг бэлнээр төлүүлж хаах уу?`)) return
    try { await api(`/api/compensations/${c.id}/pay`, { method: 'POST' }); toast('Төлөгдлөө'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const cancel = async (c) => {
    const reason = prompt(`${c.plate_number} нэхэмжлэлийг цуцлах шалтгаан:`)
    if (!reason) return
    try { await api(`/api/compensations/${c.id}/cancel`, { method: 'POST', body: { reason } }); toast('Цуцлагдлаа'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  const nightClose = async () => {
    if (!confirm('ШӨНИЙН ХААЛТ: Зогсоолд байгаа БҮХ машиныг гаргаж, төлбөртэй нь нөхөн төлбөрийн нэхэмжлэлтэй болно.\n\nЭнэ үйлдлийг БУЦААХ БОЛОМЖГҮЙ. Үргэлжлүүлэх үү?')) return
    try {
      const r = await api('/api/compensations/night-close', { method: 'POST', body: {} })
      toast(`${r.closed_sessions} машин гаргаж, ${r.compensations_created} нэхэмжлэл үүслээ`)
      load()
    } catch (e) { toast(e.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Нөхөн төлбөр</h1>
        {can('settings') && (
          <button className="btn-danger" onClick={nightClose}
            title="00 цагийн хаалт — бүх машиныг гаргаж нэхэмжлэл үүсгэнэ">
            <MoonStar size={15} /> Шөнийн хаалт
          </button>
        )}
      </div>

      <div className="card py-3 text-sm text-slate-400">
        Төлбөргүй гарсан машинд нэхэмжлэл үүсдэг (Касс → Гараар гаргах → "нөхөн төлбөр үүсгэх").
        Дараагийн ирэлтэд нь касс дээр <b className="text-red-400">улаанаар</b> тэмдэглэгдэж,
        <b className="text-slate-200"> 3+ төлөгдөөгүй</b> нэхэмжлэлтэй дугаар автоматаар хар жагсаалтад орно.
      </div>

      <div className="card grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Төлөв">
          <option value="PENDING">Төлөгдөөгүй</option>
          <option value="PAID">Төлөгдсөн</option>
          <option value="CANCELLED">Цуцлагдсан</option>
          <option value="">Бүгд</option>
        </select>
        <div className="flex gap-2 col-span-2">
          <input className="input font-mono" placeholder="Дугаараар хайх…" value={plate}
            onChange={(e) => setPlate(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === 'Enter' && load()} />
          <button className="btn-secondary" onClick={load} aria-label="Хайх"><Search size={15} /></button>
        </div>
        <div className="text-sm text-right">
          Нийт авлага: <b className="font-mono text-red-400">{fmt(data.total_pending)}₮</b>
        </div>
      </div>

      <Table headers={['Дугаар', 'Зогсоол', 'Дүн', 'Шалтгаан', 'Үүсгэсэн', 'Огноо', 'Төлөв', '']}
        empty={data.rows.length === 0}>
        {data.rows.map((c) => (
          <tr key={c.id} className={c.status === 'PENDING' ? 'bg-red-500/5' : ''}>
            <td className="td font-mono font-bold text-red-400">{c.plate_number}</td>
            <td className="td">{c.site_name}</td>
            <td className="td font-mono font-semibold">{fmt(c.amount)}₮</td>
            <td className="td text-xs">{REASONS[c.reason] || c.reason}</td>
            <td className="td text-xs">{c.created_by}</td>
            <td className="td font-mono text-xs">{fmtDate(c.created_at)}</td>
            <td className="td"><Badge value={c.status === 'PENDING' ? 'FAILED' : c.status === 'PAID' ? 'PAID' : 'CLOSED'} /></td>
            <td className="td text-right whitespace-nowrap">
              {c.status === 'PENDING' && (
                <>
                  <button className="btn-primary py-1 text-xs mr-1" onClick={() => pay(c)}>
                    <Banknote size={13} /> Төлүүлэх
                  </button>
                  {can('settings') && (
                    <button className="btn-secondary py-1 text-xs" onClick={() => cancel(c)}>Цуцлах</button>
                  )}
                </>
              )}
            </td>
          </tr>
        ))}
      </Table>
    </div>
  )
}
