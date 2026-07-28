// Гарах гэж буй машинууд (төлбөр хүлээж буй) — real-time жагсаалт
import { RefreshCw } from 'lucide-react'
import { fmt, fmtDate, fmtDur } from '../../api'

export default function ExitQueue({ exits, selected, onSelect, onRefresh }) {
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-semibold">Гарах машинууд (төлбөр хүлээж буй)</h2>
        <button onClick={onRefresh} className="p-1.5 rounded hover:bg-surface-muted cursor-pointer" aria-label="Шинэчлэх">
          <RefreshCw size={15} />
        </button>
      </div>
      <div className="space-y-2 max-h-[26rem] overflow-y-auto">
        {exits.length === 0 && <div className="text-sm text-slate-500 text-center py-6">Одоогоор гарах машин алга</div>}
        {exits.map((s) => (
          <button key={s.id} onClick={() => onSelect(s)}
            className={`w-full text-left px-4 py-3 rounded-lg border transition-colors cursor-pointer
              ${selected?.id === s.id ? 'border-accent bg-accent/5'
                : s.has_debt ? 'border-red-500/60 bg-red-500/5 hover:border-red-400'
                : 'border-surface-border/60 bg-surface-muted/30 hover:border-slate-500'}`}>
            <div className="flex items-center justify-between">
              <span className={`font-mono font-bold text-lg ${s.has_debt ? 'text-red-400' : ''}`}>{s.plate_number}</span>
              <span className="font-mono font-semibold text-amber-400">{fmt(s.fee?.total_fee ?? s.total_fee)}₮</span>
            </div>
            <div className="text-xs text-slate-500 mt-1">
              Орсон: {fmtDate(s.entry_time)} · {fmtDur(s.fee?.duration_minutes ?? s.duration_minutes)}
              {s.has_debt && <span className="text-red-400 font-medium"> · ⚠ Нөхөн төлбөрийн өртэй!</span>}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
