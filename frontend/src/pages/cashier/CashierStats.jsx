// Зогсоолын багтаамжийн тоолуур + нээлттэй ээлжийн орлогын мөр
import { fmt, fmtDate } from '../../api'

export default function CashierStats({ overview, shift }) {
  return (
    <>
      {/* Зогсоолын багтаамжийн тоолуур */}
      {overview && (
        <div className="grid grid-cols-3 gap-4">
          <div className="card py-4 text-center">
            <div className="text-3xl font-bold font-mono">{overview.occupied}<span className="text-lg text-slate-500">/{overview.capacity || '∞'}</span></div>
            <div className="text-xs text-slate-400 mt-1">Зогсож буй / Багтаамж</div>
          </div>
          <div className="card py-4 text-center">
            <div className="text-3xl font-bold font-mono text-accent">{overview.free ?? '—'}</div>
            <div className="text-xs text-slate-400 mt-1">Сул зай</div>
          </div>
          <div className="card py-4 text-center">
            <div className="text-3xl font-bold font-mono text-amber-400">{overview.rows.length}</div>
            <div className="text-xs text-slate-400 mt-1">Өнөөдөр гарсан</div>
          </div>
        </div>
      )}

      {shift?.open && (
        <div className="card py-3 flex flex-wrap gap-6 text-sm">
          <span className="text-slate-400">Ээлж нээсэн: <span className="text-slate-200 font-mono">{fmtDate(shift.shift.opened_at)}</span></span>
          <span className="text-slate-400">Гүйлгээ: <span className="text-slate-200 font-mono">{shift.count}</span></span>
          <span className="text-slate-400">Нийт орлого: <span className="text-accent font-mono font-semibold">{fmt(shift.total)}₮</span></span>
          {Object.entries(shift.by_provider || {}).map(([k, v]) => (
            <span key={k} className="text-slate-500 font-mono">{k}: {fmt(v.amount)}₮</span>
          ))}
        </div>
      )}
    </>
  )
}
