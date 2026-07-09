// Шалгах — зогсоолд ОДОО байгаа машинуудын хяналтын жагсаалт (эргүүл/хяналтын дэлгэц).
// Дугаарын эхний тэмдэгтээр live шүүнэ, төлөв/зогсоолоор шүүнэ, real-time шинэчлэгдэнэ.
import { RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, fmt, fmtDate, fmtDur, wsConnect } from '../api'
import { Badge, Table } from '../components/ui'

const STATUSES = [
  ['', 'Бүгд (зогсоолд байгаа)'],
  ['OPEN', 'Зогсож байна'],
  ['AWAITING_PAYMENT', 'Төлбөр хүлээж буй'],
  ['PAID', 'Төлсөн (гараагүй)'],
]

export default function Check() {
  const [sites, setSites] = useState([])
  const [siteId, setSiteId] = useState('')
  const [status, setStatus] = useState('')
  const [plate, setPlate] = useState('')
  const [data, setData] = useState({ total: 0, rows: [] })
  const debounceRef = useRef(null)

  const load = () => {
    const params = new URLSearchParams({
      status: status || 'OPEN,AWAITING_PAYMENT,PAID',
      with_fee: '1', limit: 200,
    })
    if (siteId) params.set('site_id', siteId)
    if (plate.trim()) params.set('plate', plate.trim())
    api(`/api/sessions?${params}`).then(setData).catch(() => {})
  }

  useEffect(() => { api('/api/admin/sites').then(setSites) }, [])
  useEffect(load, [siteId, status])
  useEffect(() => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(load, 350)
    return () => clearTimeout(debounceRef.current)
  }, [plate])
  useEffect(() => wsConnect('all', load), [siteId, status, plate])

  const unpaidTotal = data.rows.reduce((sum, s) => sum + (s.fee?.total_fee ?? Number(s.total_fee) ?? 0), 0)

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Шалгах</h1>
        <button className="btn-secondary" onClick={load}><RefreshCw size={15} /> Шинэчлэх</button>
      </div>

      <div className={`card grid grid-cols-1 gap-3 ${sites.length > 1 ? 'md:grid-cols-3' : 'md:grid-cols-2'}`}>
        <input className="input font-mono text-lg" placeholder="Дугаараар шүүх… (эхний тоо хангалттай)"
          value={plate} onChange={(e) => setPlate(e.target.value.toUpperCase())} autoFocus
          aria-label="Улсын дугаараар шүүх" />
        {sites.length > 1 && (
          <select className="input" value={siteId} onChange={(e) => setSiteId(e.target.value)} aria-label="Зогсоол">
            <option value="">Бүх зогсоол</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Төлөв">
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>

      <Table headers={['Дугаар', 'Зогсоол', 'Орсон', 'Хугацаа', 'Дүн', 'Өр', 'Гэрээт', 'Төлөв']}
        empty={data.rows.length === 0}>
        {data.rows.map((s) => (
          <tr key={s.id} className={s.debt ? 'bg-red-500/10' : 'hover:bg-surface-muted/30'}>
            <td className="td font-mono font-bold text-base">{s.plate_number}</td>
            <td className="td">{s.site_name}</td>
            <td className="td font-mono text-xs">{fmtDate(s.entry_time)}</td>
            <td className="td font-mono">{fmtDur(s.fee?.duration_minutes ?? s.duration_minutes)}</td>
            <td className="td font-mono font-semibold">
              {s.fee?.is_free ? <span className="text-cyan-400">Үнэгүй</span> : `${fmt(s.fee?.total_fee ?? s.total_fee)}₮`}
            </td>
            <td className="td">
              {s.debt ? (
                <span className="text-red-400 font-mono font-bold" title={`${s.debt.count} төлөгдөөгүй нэхэмжлэл (бүх зогсоол)`}>
                  {fmt(s.debt.amount)}₮{s.debt.count >= 3 && <span className="ml-1 text-[10px] bg-red-500/20 px-1 rounded">хориг</span>}
                </span>
              ) : <span className="text-slate-600">-</span>}
            </td>
            <td className="td text-xs">{s.is_registered ? <span className="text-accent">Тийм</span> : '-'}</td>
            <td className="td"><Badge value={s.status} /></td>
          </tr>
        ))}
      </Table>

      <div className="card py-3 flex flex-wrap gap-6 text-sm">
        <span>Зогсоолд байгаа: <b className="font-mono">{fmt(data.total)}</b> машин</span>
        <span>Тооцоолсон нийт дүн: <b className="font-mono text-amber-400">{fmt(unpaidTotal)}₮</b></span>
      </div>
    </div>
  )
}
