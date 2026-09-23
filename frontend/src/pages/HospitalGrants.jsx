// Эмнэлгийн хөнгөлөлт — эмнэлгээс баталгаажуулж өдрийн үнэгүй минут авсан машинуудын
// жагсаалт. Зогсоол · өдрөөр нэгтгэл + машин бүрийн дэлгэрэнгүй (visit_id, зогсолт,
// ашигласан минут, хөнгөлсөн дүн). Зөвхөн УНШИНА; эрх олгох/хасах нь API-аар л явна.
import { HeartPulse, RefreshCw, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { fmt, fmtHM, fmtMDHM } from '../api'
import { Badge, DateRange, StatCard, Table } from '../components/ui'
import { useFetch } from '../hooks/useFetch'
import { toDateInput } from '../validation'

const STATUS = { OPEN: 'Зогсоолд', AWAITING_PAYMENT: 'Төлбөр хүлээж буй', PAID: 'Төлсөн', CLOSED: 'Хаагдсан' }

function Stay({ s }) {
  const used = s.used_minutes ?? null
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
      <span className="font-mono text-slate-300">{fmtMDHM(s.entry_time)}</span>
      <span className="text-slate-600">→</span>
      <span className="font-mono text-slate-300">{s.exit_time ? fmtHM(s.exit_time) : '…'}</span>
      <Badge value={STATUS[s.status] || s.status} />
      <span className="text-slate-400" title={s.final ? 'Эцсийн ашигласан минут' : 'Нөөцөлсөн / түр тооцоолсон минут'}>
        {used === null ? `${s.allowance_minutes ?? 0} мин нөөц` : `${used} мин${s.final ? '' : ' (түр)'}`}
      </span>
      {s.discount_amount != null && (
        <span className="text-accent" title={s.original_fee != null ? `Бүтэн төлбөр ${fmt(s.original_fee)}₮` : ''}>
          −{fmt(s.discount_amount)}₮
        </span>
      )}
      {s.total_fee != null && <span className="text-slate-400">төлөх {fmt(s.total_fee)}₮</span>}
    </div>
  )
}

export default function HospitalGrants() {
  const today = toDateInput()
  const [from, setFrom] = useState(today)
  const [to, setTo] = useState(today)
  const [siteId, setSiteId] = useState('')
  const [plate, setPlate] = useState('')
  const [applied, setApplied] = useState('')
  const { data: sites } = useFetch('/api/admin/sites', { initial: [], silent: true })
  const params = new URLSearchParams({ date_from: from, date_to: to })
  if (siteId) params.set('site_id', siteId)
  if (applied) params.set('plate', applied)
  const { data, loading, reload } = useFetch(`/api/hospital/grants?${params}`, {
    initial: { rows: [], summary: [] }, enabled: !!from && !!to && from <= to,
  })

  const totals = useMemo(() => ({
    grants: data.rows.length,
    stays: data.rows.reduce((a, r) => a + r.stays.length, 0),
    used: data.rows.reduce((a, r) => a + (r.used_minutes || 0), 0),
    discount: data.rows.reduce((a, r) => a + (r.discount_amount || 0), 0),
  }), [data])

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold flex items-center gap-2"><HeartPulse size={22} className="text-accent" /> Эмнэлгийн хөнгөлөлт</h1>
        <div className="flex flex-wrap items-center gap-2">
          <DateRange from={from} to={to} setFrom={setFrom} setTo={setTo} maxDays={92} />
          <select className="input w-auto" value={siteId} onChange={(e) => setSiteId(e.target.value)} aria-label="Зогсоол шүүх">
            <option value="">Бүх зогсоол</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); setApplied(plate.trim().toUpperCase()) }}>
            <input className="input w-36" placeholder="Дугаар" value={plate} onChange={(e) => setPlate(e.target.value)} aria-label="Дугаараар хайх" />
            <button className="btn-secondary" type="submit" aria-label="Хайх"><Search size={16} /></button>
          </form>
          <button className="btn-secondary" onClick={reload} disabled={loading} aria-label="Шинэчлэх">
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard icon={HeartPulse} label="Эрх авсан машин" value={fmt(totals.grants)} sub="дугаар · өдөр · зогсоол" />
        <StatCard icon={HeartPulse} label="Холбогдсон зогсолт" value={fmt(totals.stays)} sub="эрх хэрэглэсэн орц" />
        <StatCard icon={HeartPulse} label="Ашигласан минут" value={fmt(totals.used)} suffix="мин" />
        <StatCard icon={HeartPulse} label="Хөнгөлсөн дүн" value={fmt(Math.round(totals.discount))} suffix="₮" />
      </div>

      {/* Зогсоол · өдрөөр нэгтгэл */}
      <div className="card space-y-3">
        <div className="font-semibold text-slate-200">Зогсоол · өдрөөр</div>
        <Table headers={['Огноо', 'Зогсоол', 'Эрх авсан машин', 'Зогсолт', 'Ашигласан минут', 'Хөнгөлсөн дүн']} empty={!loading && !data.summary.length}>
          {data.summary.map((s) => (
            <tr key={`${s.benefit_date}-${s.site_id}`}>
              <td className="td font-mono">{s.benefit_date}</td>
              <td className="td">{s.site_name}</td>
              <td className="td text-right font-mono">{fmt(s.grants)}</td>
              <td className="td text-right font-mono">{fmt(s.stays)}</td>
              <td className="td text-right font-mono">{fmt(s.used_minutes)}</td>
              <td className="td text-right font-mono text-accent">{fmt(Math.round(s.discount_amount))}₮</td>
            </tr>
          ))}
        </Table>
      </div>

      {/* Машин бүрээр */}
      <div className="card space-y-3">
        <div className="flex items-center justify-between">
          <div className="font-semibold text-slate-200">Машин бүрээр</div>
          <div className="text-xs text-slate-500">
            Өдөр = Улаанбаатарын цаг, орсон өдрөөр. Нэг дугаарт өдөрт нэг эрх; «visit» тоо нь эмнэлгээс
            ирсэн баталгаажуулалтын тоо (давхардсан нь минут нэмдэггүй).
          </div>
        </div>
        <Table headers={['Огноо', 'Зогсоол', 'Дугаар', 'Эмнэлэг', 'Эрх ирсэн', 'Visit', 'Лимит', 'Ашигласан', 'Үлдэгдэл', 'Хөнгөлөлт', 'Зогсолтууд']}
          empty={!loading && !data.rows.length} maxH="65vh">
          {data.rows.map((r) => (
            <tr key={r.grant_id} className="align-top">
              <td className="td font-mono whitespace-nowrap">{r.benefit_date}</td>
              <td className="td whitespace-nowrap">{r.site_name}</td>
              <td className="td font-mono font-semibold whitespace-nowrap">{r.plate_number}</td>
              <td className="td whitespace-nowrap">{r.integration_name}</td>
              <td className="td font-mono whitespace-nowrap">{fmtHM(r.granted_at)}</td>
              <td className="td text-center">
                <span title={r.visits.map((v) => `${v.visit_id} · ${fmtHM(v.at)}`).join('\n')} className="cursor-help font-mono">
                  {r.visits.length}
                </span>
              </td>
              <td className="td text-right font-mono">{r.daily_minutes}</td>
              <td className="td text-right font-mono">{r.used_minutes}</td>
              <td className="td text-right font-mono">{r.remaining_minutes}</td>
              <td className="td text-right font-mono text-accent whitespace-nowrap">{r.discount_amount ? `${fmt(r.discount_amount)}₮` : '—'}</td>
              <td className="td">
                {r.stays.length
                  ? <div className="space-y-1">{r.stays.map((s) => <Stay key={s.session_id} s={s} />)}</div>
                  : <span className="text-xs text-slate-500">Зогсоолд ороогүй (дараагийн орцод хэрэглэгдэнэ)</span>}
              </td>
            </tr>
          ))}
        </Table>
      </div>
    </div>
  )
}
