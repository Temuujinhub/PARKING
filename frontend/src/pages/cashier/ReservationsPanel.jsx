// Урьдчилсан захиалга (wallet, 2026-10-22) — харуулд: ойрын 24 цагт эхлэх болон одоо идэвхтэй
// захиалгууд. Шинэ захиалга баталгаажих/машин ирэх бүрд WebSocket-оор шинэчлэгдэнэ (refreshKey).
// Захиалгагүй үед огт харагдахгүй (касс дэлгэцийг ачаалахгүй).
import { CalendarClock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtHM, fmtMDHM } from '../../api'

const STATUS = {
  CONFIRMED: ['Хүлээж байна', 'bg-blue-500/15 text-blue-300 border-blue-500/30'],
  CHECKED_IN: ['Ирсэн', 'bg-accent/15 text-accent border-accent/30'],
}

export default function ReservationsPanel({ siteId, refreshKey }) {
  const [rows, setRows] = useState([])
  useEffect(() => {
    if (!siteId) { setRows([]); return }
    let alive = true
    const load = () => api(`/api/reservations?site_id=${siteId}&scope=upcoming`)
      .then((d) => { if (alive) setRows(d.reservations || []) }).catch(() => {})
    load()
    const t = setInterval(load, 60000)   // эхлэх цаг ойртох/дуусахыг WS-гүйгээр ч шинэчилнэ
    return () => { alive = false; clearInterval(t) }
  }, [siteId, refreshKey])

  if (!rows.length) return null
  const today = new Date().toDateString()
  return (
    <div className="card">
      <h2 className="font-semibold flex items-center gap-2 mb-3">
        <CalendarClock size={17} className="text-blue-300" aria-hidden />
        Урьдчилсан захиалга <span className="text-xs text-slate-500 font-normal">· байрыг бэлдэж үлдээнэ үү</span>
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="py-1.5 pr-3 font-medium">Цаг</th>
              <th className="py-1.5 pr-3 font-medium">Дугаар</th>
              <th className="py-1.5 pr-3 font-medium">Код</th>
              <th className="py-1.5 pr-3 font-medium">Хэтэвч</th>
              <th className="py-1.5 pr-3 font-medium text-right">Урьдчилж төлсөн</th>
              <th className="py-1.5 font-medium">Төлөв</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const [label, cls] = STATUS[r.status] || [r.status, 'border-surface-border text-slate-400']
              const sameDay = new Date(r.start_time).toDateString() === today
              return (
                <tr key={r.reservation_id} className="border-t border-surface-border/40">
                  <td className="py-2 pr-3 font-mono whitespace-nowrap">
                    {sameDay ? fmtHM(r.start_time) : fmtMDHM(r.start_time)}–{fmtHM(r.end_time)}
                  </td>
                  <td className="py-2 pr-3 font-mono font-bold tracking-wider">{r.plate_number}</td>
                  <td className="py-2 pr-3 font-mono text-slate-300">{r.reservation_no}</td>
                  <td className="py-2 pr-3 text-slate-300">{r.provider}</td>
                  <td className="py-2 pr-3 font-mono text-right">{fmt(r.amount)}₮</td>
                  <td className="py-2">
                    <span className={`text-[11px] px-1.5 py-0.5 rounded border ${cls}`}>{label}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
