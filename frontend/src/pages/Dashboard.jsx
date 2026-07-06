import { Activity, Banknote, Car, CarFront, Clock, LogIn, LogOut as ExitIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, fmt, fmtDate, wsConnect } from '../api'
import { Badge, StatCard } from '../components/ui'

const EVENT_LABELS = {
  ENTRY_EVENT: { label: 'Орлоо', color: 'text-accent', icon: LogIn },
  EXIT_LPR_EVENT: { label: 'Гарах — төлбөр хүлээж буй', color: 'text-amber-400', icon: ExitIcon },
  EXIT_COMPLETED: { label: 'Гарлаа', color: 'text-slate-300', icon: ExitIcon },
  PAYMENT_COMPLETED: { label: 'Төлбөр төлөгдлөө', color: 'text-accent', icon: Banknote },
  BLACKLIST_ALERT: { label: '⚠ Хар жагсаалт!', color: 'text-red-400', icon: Car },
  EXIT_NO_SESSION: { label: 'Бүртгэлгүй гарах оролдлого', color: 'text-red-400', icon: Car },
  BARRIER_MANUAL_OPEN: { label: 'Хаалт гараар нээв', color: 'text-purple-400', icon: Activity },
}

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [events, setEvents] = useState([])
  const load = () => api('/api/reports/dashboard').then(setStats).catch(() => {})

  useEffect(() => {
    load()
    const t = setInterval(load, 30000)
    const close = wsConnect('all', (ev) => {
      setEvents((prev) => [ev, ...prev].slice(0, 30))
      load()
    })
    return () => { clearInterval(t); close() }
  }, [])

  if (!stats) return <div className="text-slate-500">Ачаалж байна…</div>
  const maxRev = Math.max(...stats.week_revenue.map((d) => d.revenue), 1)

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Хяналтын самбар</h1>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={CarFront} label="Одоо зогсож буй" value={stats.open_sessions}
          sub={`Багтаамж: ${stats.total_capacity}`} />
        <StatCard icon={Clock} label="Төлбөр хүлээж буй" value={stats.awaiting_payment} color="text-amber-400" />
        <StatCard icon={LogIn} label="Өнөөдөр орсон" value={stats.today_entries} color="text-blue-400"
          sub={`Гарсан: ${stats.today_exits}`} />
        <StatCard icon={Banknote} label="Өнөөдрийн орлого" value={`${fmt(stats.today_revenue)}₮`} />
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        <div className="card">
          <h2 className="font-semibold mb-4">7 хоногийн орлого</h2>
          <div className="flex items-end gap-2 h-40" role="img"
            aria-label={`Сүүлийн 7 хоногийн орлого: ${stats.week_revenue.map((d) => `${d.date} ${fmt(d.revenue)}₮`).join(', ')}`}>
            {stats.week_revenue.map((d) => (
              <div key={d.date} className="flex-1 flex flex-col items-center gap-1">
                <div className="text-[10px] text-slate-400 font-mono">{d.revenue > 0 ? fmt(d.revenue) : ''}</div>
                <div className="w-full bg-accent/80 rounded-t transition-all hover:bg-accent"
                  style={{ height: `${Math.max(2, (d.revenue / maxRev) * 100)}%` }} title={`${fmt(d.revenue)}₮`} />
                <div className="text-[10px] text-slate-500 font-mono">{d.date}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h2 className="font-semibold mb-4">Зогсоолын ачаалал</h2>
          <div className="space-y-3">
            {stats.sites.map((s) => {
              const pct = s.capacity ? Math.min(100, Math.round((s.occupied / s.capacity) * 100)) : 0
              return (
                <div key={s.id}>
                  <div className="flex justify-between text-sm mb-1">
                    <span>{s.name}</span>
                    <span className="text-slate-400 font-mono text-xs">
                      {s.occupied}/{s.capacity} · Сул: {s.free} · {fmt(s.today_revenue)}₮
                    </span>
                  </div>
                  <div className="h-2 bg-surface-muted rounded-full overflow-hidden">
                    <div className={`h-full rounded-full transition-all ${pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-amber-400' : 'bg-accent'}`}
                      style={{ width: `${pct}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <div className="card">
        <h2 className="font-semibold mb-3 flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-60" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-accent" />
          </span>
          Шууд үйл явдал
        </h2>
        <div className="space-y-1.5 max-h-72 overflow-y-auto" aria-live="polite">
          {events.length === 0 && <div className="text-sm text-slate-500 py-4 text-center">Үйл явдал хүлээж байна…</div>}
          {events.map((ev, i) => {
            const meta = EVENT_LABELS[ev.type] || { label: ev.type, color: 'text-slate-400', icon: Activity }
            const Icon = meta.icon
            return (
              <div key={i} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-surface-muted/40 text-sm">
                <Icon size={15} className={meta.color} aria-hidden />
                <span className="font-mono font-semibold">{ev.data?.plate || ''}</span>
                <span className={meta.color}>{meta.label}</span>
                {ev.data?.total_fee !== undefined && <span className="font-mono">{fmt(ev.data.total_fee)}₮</span>}
                <span className="ml-auto text-xs text-slate-500 font-mono">{fmtDate(ev.ts).split(' ')[1] || ''}</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
