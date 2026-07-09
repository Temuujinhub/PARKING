import { Activity, Banknote, Building2, Camera, Car, CarFront, Clock, DoorOpen, LogIn, LogOut as ExitIcon, TrendingUp, UserCheck, Wifi } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate, wsConnect } from '../api'
import { StatCard } from '../components/ui'

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
  const hourly = stats.hourly_load || []
  const maxHour = Math.max(...hourly.map((h) => Math.max(h.entries, h.exits)), 1)
  const topSites = [...(stats.sites || [])].sort((a, b) => b.today_revenue - a.today_revenue)
  const maxSiteRev = Math.max(...topSites.map((s) => s.today_revenue), 1)
  // Зогсоол бүрийн байдал — кассчин + камеруудын онлайн статус
  const siteStatus = (stats.sites || []).map((site) => ({
    ...site,
    cashier: (stats.active_shifts || []).find((s) => s.site_name === site.name)?.cashier || null,
    cams: (stats.device_status || []).filter((d) => d.site_name === site.name && d.device_type === 'camera'),
  }))
  const devConnLabel = stats.devices_total
    ? (stats.devices_online === stats.devices_total ? 'Бүгд холбогдсон'
      : stats.devices_online === 0 ? 'Холболт алга' : 'Хэсэгчилсэн')
    : 'Төхөөрөмжгүй'
  const devConnColor = stats.devices_total && stats.devices_online === stats.devices_total
    ? 'text-accent' : stats.devices_online === 0 ? 'text-red-400' : 'text-amber-400'

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Хяналтын самбар</h1>

      {/* Үндсэн үзүүлэлт — нэг эгнээ: 4 KPI + систем/төхөөрөмжийн нэгтгэсэн карт */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <StatCard icon={CarFront} label="Одоо зогсож буй" value={stats.open_sessions}
          sub={`Багтаамж: ${stats.total_capacity}`} />
        <StatCard icon={Clock} label="Төлбөр хүлээж буй" value={stats.awaiting_payment} color="text-amber-400" />
        <StatCard icon={LogIn} label="Өнөөдөр орсон" value={stats.today_entries} color="text-blue-400"
          sub={`Гарсан: ${stats.today_exits}`} />
        <StatCard icon={Banknote} label="Өнөөдрийн орлого" value={`${fmt(stats.today_revenue)}₮`} />
        {/* Систем — зогсоол/камер/холболт (хаалт нь камертай хамт тул тусад нь харуулахгүй) */}
        <div className="card py-3 col-span-2 lg:col-span-1">
          <div className="text-xs text-slate-400 mb-2">Систем / төхөөрөмж</div>
          <div className="space-y-2">
            <div className="flex items-center gap-1.5"><Building2 size={15} className="text-slate-400" />
              <span className="font-mono font-bold">{stats.sites_total ?? 0}</span><span className="text-[11px] text-slate-500">зогсоол</span></div>
            <div className="flex items-center gap-1.5"><Camera size={15} className="text-blue-400" />
              <span className="font-mono font-bold">{stats.cameras_total ?? 0}</span><span className="text-[11px] text-slate-500">камер</span></div>
            <div className="flex items-center gap-1.5"><Wifi size={15} className={devConnColor} />
              <span className={`font-mono font-bold ${devConnColor}`}>{stats.devices_online ?? 0}/{stats.devices_total ?? 0}</span>
              <span className="text-[11px] text-slate-500">холболт</span></div>
          </div>
        </div>
      </div>

      <div className="grid xl:grid-cols-[1fr_20rem] gap-6 items-start">
        <div className="space-y-6 min-w-0">{/* ── Үндсэн багана ── */}

      <div className="grid lg:grid-cols-2 gap-6">
        {/* 7 хоногийн орлого — bar график (max-д нормчилсон) */}
        <div className="card">
          <h2 className="font-semibold mb-4">7 хоногийн орлого</h2>
          <div className="flex items-end gap-2 h-48 border-b border-surface-border/60 pb-0" role="img"
            aria-label={`Сүүлийн 7 хоногийн орлого: ${stats.week_revenue.map((d) => `${d.date} ${fmt(d.revenue)}₮`).join(', ')}`}>
            {stats.week_revenue.map((d) => (
              <div key={d.date} className="flex-1 flex flex-col items-center justify-end gap-1 h-full">
                <div className="text-[10px] text-slate-300 font-mono whitespace-nowrap">
                  {d.revenue > 0 ? fmt(d.revenue) : ''}
                </div>
                <div className="w-full flex items-end justify-center" style={{ height: '100%' }}>
                  <div className="w-full max-w-[46px] bg-gradient-to-t from-accent to-accent/60 rounded-t-md transition-all hover:from-accent hover:to-accent"
                    style={{ height: `${d.revenue > 0 ? Math.max(4, (d.revenue / maxRev) * 100) : 0}%` }}
                    title={`${d.date}: ${fmt(d.revenue)}₮`} />
                </div>
              </div>
            ))}
          </div>
          <div className="flex gap-2 mt-1.5">
            {stats.week_revenue.map((d) => (
              <div key={d.date} className="flex-1 text-center text-[10px] text-slate-500 font-mono">{d.date}</div>
            ))}
          </div>
        </div>

        {/* Цагийн ачаалал — орц/гарц (0–23 цаг) */}
        <div className="card">
          <h2 className="font-semibold mb-4">Өнөөдрийн цагийн ачаалал</h2>
          <div className="flex items-end gap-[3px] h-48 border-b border-surface-border/60" role="img"
            aria-label={`Цагийн ачаалал: ${hourly.filter((h) => h.entries || h.exits).map((h) => `${h.hour}ц орц ${h.entries} гарц ${h.exits}`).join(', ') || 'өнөөдөр хөдөлгөөнгүй'}`}>
            {hourly.map((h) => (
              <div key={h.hour} className="flex-1 flex flex-col justify-end items-center gap-[2px] h-full group relative">
                <div className="w-full flex flex-col justify-end items-center h-full">
                  <div className="w-full bg-accent/80 rounded-t-sm" style={{ height: `${(h.entries / maxHour) * 100}%` }} />
                  <div className="w-full bg-amber-400/70 rounded-b-sm" style={{ height: `${(h.exits / maxHour) * 100}%` }} />
                </div>
                <div className="absolute -top-6 hidden group-hover:block bg-surface-muted text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap z-10 border border-surface-border">
                  {h.hour}ц · орц {h.entries} · гарц {h.exits}
                </div>
              </div>
            ))}
          </div>
          <div className="flex justify-between mt-1.5 text-[10px] text-slate-500 font-mono">
            <span>0ц</span><span>6ц</span><span>12ц</span><span>18ц</span><span>23ц</span>
          </div>
          <div className="flex gap-4 mt-2 text-xs text-slate-400">
            <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-accent/80" /> Орц</span>
            <span className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm bg-amber-400/70" /> Гарц</span>
          </div>
        </div>
      </div>

      <div className="grid lg:grid-cols-2 gap-6">
        {/* Топ орлоготой зогсоол */}
        <div className="card">
          <h2 className="font-semibold mb-4 flex items-center gap-2">
            <TrendingUp size={16} className="text-accent" /> Хамгийн их орлоготой зогсоол (өнөөдөр)
          </h2>
          <div className="space-y-3">
            {topSites.length === 0 && <div className="text-sm text-slate-500 py-2">Зогсоол бүртгэгдээгүй</div>}
            {topSites.map((s, i) => (
              <div key={s.id}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="flex items-center gap-2">
                    <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-bold
                      ${i === 0 ? 'bg-accent text-white' : 'bg-surface-muted text-slate-400'}`}>{i + 1}</span>
                    {s.name}
                  </span>
                  <span className="font-mono font-semibold text-accent">{fmt(s.today_revenue)}₮</span>
                </div>
                <div className="h-2 bg-surface-muted rounded-full overflow-hidden">
                  <div className="h-full rounded-full bg-accent transition-all"
                    style={{ width: `${(s.today_revenue / maxSiteRev) * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Зогсоолын ачаалал (багтаамж) */}
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
                      {s.occupied}/{s.capacity} · Сул: {s.free}
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

      {/* Шууд үйл явдал */}
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
        </div>{/* ── Үндсэн багана төгсгөл ── */}

        {/* ── Баруун талын босоо панель: зогсоол бүрийн байдал ── */}
        <aside className="space-y-3">
          <h2 className="font-semibold flex items-center gap-2">
            <UserCheck size={16} className="text-accent" /> Зогсоол бүрийн байдал
          </h2>
          <div className="space-y-3 xl:max-h-[calc(100vh-9rem)] xl:overflow-y-auto pr-1">
            {siteStatus.length === 0 && <div className="card text-sm text-slate-500 py-4 text-center">Зогсоол бүртгэгдээгүй</div>}
            {siteStatus.map((site) => {
              const camsOn = site.cams.filter((d) => d.online).length
              return (
                <div key={site.id} className="card p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-sm truncate">{site.name}</span>
                    <span className="text-[11px] text-slate-500 font-mono">{site.occupied}/{site.capacity}</span>
                  </div>
                  {/* Кассчин (сүүлд нэвтэрч ажиллаж буй оператор) */}
                  <div className="flex items-center gap-2 text-xs">
                    <span className={`w-2 h-2 rounded-full shrink-0 ${site.cashier ? 'bg-accent' : 'bg-slate-600'}`} />
                    {site.cashier
                      ? <span className="text-slate-200 truncate">{site.cashier}</span>
                      : <span className="text-slate-500">Ажилтан нэвтрээгүй</span>}
                  </div>
                  {/* Камеруудын онлайн/офлайн цэгэн гэрэл */}
                  <div className="flex flex-wrap gap-1.5">
                    {site.cams.length === 0 && <span className="text-[11px] text-slate-600">Камер бүртгэгдээгүй</span>}
                    {site.cams.map((d) => (
                      <span key={d.id} title={`${d.name}: ${d.online ? 'онлайн' : 'офлайн'}`}
                        className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-surface-muted/50 border border-surface-border/50">
                        <span className={`w-2 h-2 rounded-full ${d.online ? 'bg-accent' : 'bg-red-500'}`} />
                        {d.name}
                      </span>
                    ))}
                  </div>
                  {site.cams.length > 0 && (
                    <div className={`text-[10px] ${camsOn === site.cams.length ? 'text-accent' : camsOn === 0 ? 'text-red-400' : 'text-amber-400'}`}>
                      {camsOn}/{site.cams.length} камер онлайн
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </aside>
      </div>
    </div>
  )
}
