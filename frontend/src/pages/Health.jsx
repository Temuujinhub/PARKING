// Системийн эрүүл мэнд — сервер metrics, сервисүүд, DB, харилцан холболт (5 сек auto-refresh)
import {
  Activity, AlertTriangle, Camera, Cpu, Database, DoorClosed, HardDrive,
  MemoryStick, Network, PieChart, RefreshCw, Server, ShieldCheck, Thermometer, Wifi,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Table } from '../components/ui'

const fmtBytes = (n) => {
  if (n == null) return '—'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0; let v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`
}
const fmtDur = (s) => {
  if (s == null) return '—'
  const d = Math.floor(s / 86400); const h = Math.floor((s % 86400) / 3600); const m = Math.floor((s % 3600) / 60)
  return d ? `${d}ө ${h}ц` : h ? `${h}ц ${m}м` : `${m}м`
}
const ageLabel = (sec) => sec == null ? 'хэзээ ч' : sec < 90 ? `${sec}с` : sec < 5400 ? `${Math.round(sec / 60)}м` : `${Math.round(sec / 3600)}ц`

// Хувь → өнгө (ачаалал их бол улаан)
const pctColor = (p) => p >= 90 ? 'bg-red-500' : p >= 75 ? 'bg-amber-500' : 'bg-accent'
const pctText = (p) => p >= 90 ? 'text-red-400' : p >= 75 ? 'text-amber-400' : 'text-accent'
const sslColor = (days) => days == null ? 'text-slate-400' : days <= 7 ? 'text-red-400' : days <= 20 ? 'text-amber-400' : 'text-accent'

function Bar({ percent, color, hex }) {
  return (
    <div className="h-2 rounded-full bg-surface-muted overflow-hidden">
      <div className={`h-full ${hex ? '' : (color || pctColor(percent))}`}
        style={{ width: `${Math.min(100, percent || 0)}%`, ...(hex ? { background: hex } : {}) }} />
    </div>
  )
}

// Дата ангиллын өнгө (донат + бар) — гэрэл/бараан хоёуланд тод харагдана
const CAT_COLORS = {
  'Мөнгөн урсгал': '#34d399', 'Лог/түүх': '#fbbf24', 'Тохиргоо': '#60a5fa', 'Бусад': '#94a3b8',
}

function Donut({ categories }) {
  const r = 42; const c = 2 * Math.PI * r
  let offset = 0
  return (
    <svg viewBox="0 0 100 100" className="w-40 h-40 -rotate-90">
      <circle cx="50" cy="50" r={r} fill="none" strokeWidth="13" className="stroke-surface-muted" />
      {categories.map((cat) => {
        const len = (cat.percent / 100) * c
        const el = (
          <circle key={cat.name} cx="50" cy="50" r={r} fill="none" strokeWidth="13"
            stroke={CAT_COLORS[cat.name] || '#94a3b8'}
            strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} />
        )
        offset += len
        return el
      })}
    </svg>
  )
}

function Dot({ ok }) {
  const c = ok === true ? 'bg-accent' : ok === false ? 'bg-red-500' : 'bg-slate-500'
  return <span className={`inline-block w-2.5 h-2.5 rounded-full ${c} ${ok === true ? 'animate-pulse' : ''}`} />
}

export default function Health() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [ivl, setIvl] = useState(30000) // шинэчлэх давтамж (мс), 0 = гараар
  const netRef = useRef(null) // сүлжээний хурд тооцох өмнөх дээж
  const [netRate, setNetRate] = useState(null)

  const load = () => api('/api/health/system').then((r) => {
    setErr(null)
    // Сүлжээний хурд = өмнөх дээжтэй зөрүү / хугацаа
    const net = r.system?.network
    if (net && netRef.current) {
      const dt = (r.generated_at - netRef.current.t) || 1
      setNetRate({
        rx: Math.max(0, (net.bytes_recv - netRef.current.rx) / dt),
        tx: Math.max(0, (net.bytes_sent - netRef.current.tx) / dt),
      })
    }
    if (net) netRef.current = { rx: net.bytes_recv, tx: net.bytes_sent, t: r.generated_at }
    setD(r)
  }).catch((e) => setErr(e.message))

  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!ivl) return
    const id = setInterval(load, ivl)
    return () => clearInterval(id)
  }, [ivl])

  if (err) return (
    <div className="card text-red-400 flex items-center gap-2"><AlertTriangle size={18} /> {err}</div>
  )
  if (!d) return <div className="text-slate-500">Ачаалж байна…</div>

  const sys = d.system || {}
  const mem = sys.memory || {}
  const swap = sys.swap || {}
  const mock = d.app?.mock || {}

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">Системийн эрүүл мэнд</h1>
          <p className="text-sm text-slate-400">
            {d.app?.name} · хувилбар <span className="font-mono">{d.app?.version || '—'}</span> ·
            API ажилласан {fmtDur(d.app?.uptime_seconds)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select className="input w-auto py-1.5 text-xs" value={ivl}
            onChange={(e) => setIvl(+e.target.value)} aria-label="Шинэчлэх давтамж">
            <option value={0}>Гараар</option>
            <option value={10000}>10 сек</option>
            <option value={30000}>30 сек</option>
            <option value={60000}>1 мин</option>
          </select>
          <button className="btn-secondary" onClick={load}><RefreshCw size={15} /> Шинэчлэх</button>
        </div>
      </div>

      {/* Анхааруулгууд */}
      {d.reboot_required && (
        <div className="card bg-amber-500/10 border border-amber-500/30 text-amber-300 flex items-center gap-2 py-3">
          <AlertTriangle size={18} /> Сервер дахин ачаалах шаардлагатай (kernel/багц шинэчлэгдсэн).
        </div>
      )}
      {mock.simulate && (
        <div className="card bg-amber-500/10 border border-amber-500/30 text-amber-300 flex items-center gap-2 py-3">
          <AlertTriangle size={18} /> Тест горим (simulate) идэвхтэй — production-д унтраана уу.
        </div>
      )}

      {/* Сервер metrics */}
      {sys.available === false ? (
        <div className="card text-slate-400 text-sm">Серверийн metrics байхгүй (psutil суулгаагүй).</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          <div className="card space-y-2">
            <div className="flex items-center gap-2 text-slate-400 text-sm"><Cpu size={16} /> CPU</div>
            <div className={`text-3xl font-bold font-mono ${pctText(sys.cpu_percent)}`}>{Math.round(sys.cpu_percent)}%</div>
            <Bar percent={sys.cpu_percent} />
            <div className="text-xs text-slate-500">{sys.cpu_count} цөм · load {sys.load_avg?.join(' / ')}{sys.processes ? ` · ${sys.processes} процесс` : ''}</div>
          </div>
          <div className="card space-y-2">
            <div className="flex items-center gap-2 text-slate-400 text-sm"><MemoryStick size={16} /> Санах ой (RAM)</div>
            <div className={`text-3xl font-bold font-mono ${pctText(mem.percent)}`}>{Math.round(mem.percent)}%</div>
            <Bar percent={mem.percent} />
            <div className="text-xs text-slate-500">{fmtBytes(mem.used)} / {fmtBytes(mem.total)} · swap {Math.round(swap.percent || 0)}%{sys.backend_rss ? ` · API ${fmtBytes(sys.backend_rss)}` : ''}</div>
          </div>
          <div className="card space-y-2">
            <div className="flex items-center gap-2 text-slate-400 text-sm"><Thermometer size={16} /> Температур</div>
            <div className={`text-3xl font-bold font-mono ${sys.temperature_c == null ? 'text-slate-500' : sys.temperature_c >= 75 ? 'text-red-400' : sys.temperature_c >= 60 ? 'text-amber-400' : 'text-accent'}`}>
              {sys.temperature_c == null ? '—' : `${sys.temperature_c}°C`}
            </div>
            <div className="text-xs text-slate-500">{sys.temperature_c == null ? 'Мэдрэгч байхгүй (cloud VM)' : 'CPU дулаан'}</div>
          </div>
          <div className="card space-y-2">
            <div className="flex items-center gap-2 text-slate-400 text-sm"><Network size={16} /> Сүлжээ</div>
            <div className="text-lg font-bold font-mono text-accent">↓ {fmtBytes(netRate?.rx)}/s</div>
            <div className="text-lg font-bold font-mono text-blue-400">↑ {fmtBytes(netRate?.tx)}/s</div>
            <div className="text-xs text-slate-500">Нийт: {fmtBytes(sys.network?.bytes_recv)} авсан</div>
          </div>
        </div>
      )}

      {/* Диск */}
      {sys.disks?.length > 0 && (
        <div className="card">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-3"><HardDrive size={16} /> Дискний багтаамж</div>
          <div className="space-y-3">
            {sys.disks.map((dk) => (
              <div key={dk.mount}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="font-mono">{dk.mount}</span>
                  <span className={pctText(dk.percent)}>{fmtBytes(dk.used)} / {fmtBytes(dk.total)} ({Math.round(dk.percent)}%)</span>
                </div>
                <Bar percent={dk.percent} />
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Сервисүүд */}
        <div className="card">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-3"><Server size={16} /> Сервисүүд</div>
          <div className="space-y-2">
            {d.services?.map((s) => (
              <div key={s.name} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2"><Dot ok={s.status === 'active' ? true : s.status === 'unknown' ? null : false} /> {s.name}</span>
                <span className={`font-mono text-xs ${s.status === 'active' ? 'text-accent' : s.status === 'unknown' ? 'text-slate-500' : 'text-red-400'}`}>{s.status}</span>
              </div>
            ))}
            <div className="flex items-center justify-between text-sm pt-2 border-t border-surface-border/50">
              <span className="text-slate-400">Kernel</span>
              <span className="font-mono text-xs text-slate-300">{d.kernel}</span>
            </div>
          </div>
        </div>

        {/* Database тойм */}
        <div className="card">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-3"><Database size={16} /> Өгөгдлийн сан</div>
          {d.database?.ok ? (
            <div className="grid grid-cols-2 gap-3 text-sm">
              <Stat label="Холболт" value={<span className="text-accent flex items-center gap-1"><Dot ok /> Хэвийн</span>} />
              <Stat label="Нийт хэмжээ" value={fmtBytes(d.database.size_bytes)} />
              <Stat label="Идэвхтэй холболт" value={`${d.database.active_connections}${d.database.max_connections ? ' / ' + d.database.max_connections : ''}`} />
              <Stat label="Дата ангилал" value={`${d.database.storage?.categories?.length || 0}`} />
            </div>
          ) : (
            <div className="text-red-400 text-sm flex items-center gap-2"><Dot ok={false} /> {d.database?.error || 'Холбогдсонгүй'}</div>
          )}
        </div>
      </div>

      {/* Өгөгдлийн сангийн эзэлхүүн — ямар төрлийн датагаар хэдэн хувь дүүрсэн (донат) */}
      {d.database?.storage?.total_bytes > 0 && (
        <div className="card">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-4">
            <PieChart size={16} /> Өгөгдлийн сангийн эзэлхүүн — датагаар
          </div>
          <div className="flex flex-col md:flex-row gap-6 items-center">
            <div className="relative shrink-0">
              <Donut categories={d.database.storage.categories} />
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <div className="text-lg font-bold font-mono">{fmtBytes(d.database.storage.total_bytes)}</div>
                <div className="text-[11px] text-slate-500">нийт</div>
              </div>
            </div>
            <div className="flex-1 w-full space-y-2.5">
              {d.database.storage.categories.map((cat) => (
                <div key={cat.name}>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="flex items-center gap-2">
                      <span className="w-3 h-3 rounded-sm" style={{ background: CAT_COLORS[cat.name] || '#94a3b8' }} />
                      {cat.name}
                    </span>
                    <span className="font-mono text-slate-300">{cat.percent}% · {fmtBytes(cat.bytes)}</span>
                  </div>
                  <Bar percent={cat.percent} hex={CAT_COLORS[cat.name] || '#94a3b8'} />
                </div>
              ))}
              {d.database.storage.top_tables?.length > 0 && (
                <div className="pt-2 mt-2 border-t border-surface-border/50 text-xs text-slate-500">
                  Хамгийн том хүснэгт: {d.database.storage.top_tables.slice(0, 4).map((t) => `${t.table} (${t.percent}%)`).join(' · ')}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Интеграци: QPay + WebSocket */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="card">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-2"><Activity size={16} /> QPay холболт</div>
          {d.integrations?.qpay?.ok === null ? (
            <div className="text-slate-500 text-sm">Mock горим (бодит холболт шалгахгүй)</div>
          ) : d.integrations?.qpay?.ok ? (
            <div className="text-accent flex items-center gap-2"><Dot ok /> Хэвийн <span className="text-xs text-slate-500">{d.integrations.qpay.ms}ms</span></div>
          ) : (
            <div className="text-red-400 text-sm flex items-center gap-2"><Dot ok={false} /> {d.integrations?.qpay?.error || 'Хүрэхгүй'}</div>
          )}
        </div>
        <div className="card">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-2"><Wifi size={16} /> WebSocket</div>
          <div className="text-2xl font-bold font-mono text-accent">{d.integrations?.websocket_clients ?? 0}</div>
          <div className="text-xs text-slate-500">холбогдсон клиент (dashboard/касс)</div>
        </div>
        <div className="card">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-2"><Server size={16} /> Mock горим</div>
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {[['QPay', mock.qpay], ['Хаалт', mock.barrier], ['e-Barimt', mock.ebarimt]].map(([l, v]) => (
              <span key={l} className={`px-2 py-0.5 rounded ${v ? 'bg-amber-500/15 text-amber-400' : 'bg-accent/15 text-accent'}`}>
                {l}: {v ? 'MOCK' : 'бодит'}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* Үйл ажиллагаа ба хамгаалалт — SSL, backup, ТЕГ авто-илгээлт, backend restart */}
      {d.ops && (
        <div className="card">
          <div className="flex items-center gap-2 text-slate-400 text-sm mb-3"><ShieldCheck size={16} /> Үйл ажиллагаа ба хамгаалалт</div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 text-sm">
            <div>
              <div className="text-xs text-slate-500 mb-1">SSL сертификат</div>
              {d.ops.ssl ? (
                <div>
                  <span className={`font-mono font-bold ${sslColor(d.ops.ssl.days_left)}`}>{d.ops.ssl.days_left} хоног үлдсэн</span>
                  <div className="text-[11px] text-slate-500">{new Date(d.ops.ssl.expires_at).toLocaleDateString()} хүртэл · {d.ops.ssl.host}</div>
                </div>
              ) : <span className="text-slate-500">— (тест/localhost)</span>}
            </div>
            <div>
              <div className="text-xs text-slate-500 mb-1">Сүүлийн backup</div>
              {d.ops.backup?.age_sec != null ? (
                <div>
                  <span className={`font-mono font-bold ${d.ops.backup.age_sec > 172800 ? 'text-amber-400' : 'text-accent'}`}>{fmtDur(d.ops.backup.age_sec)} өмнө</span>
                  <div className="text-[11px] text-slate-500">{fmtBytes(d.ops.backup.size_bytes)}{d.ops.backup.replicas != null ? ` · replica ${d.ops.backup.replicas}` : ''}</div>
                </div>
              ) : <span className="text-slate-500">Backup файл олдсонгүй</span>}
            </div>
            <div>
              <div className="text-xs text-slate-500 mb-1">ТЕГ авто-илгээлт (e-Barimt)</div>
              {d.ops.ebarimt_last_send ? (
                <div>
                  <span className="font-mono font-bold text-accent">{fmtDur(d.generated_at - d.ops.ebarimt_last_send)} өмнө</span>
                  <div className="text-[11px] text-slate-500">{new Date(d.ops.ebarimt_last_send * 1000).toLocaleString()}</div>
                </div>
              ) : <span className="text-amber-400">Хараахан илгээгээгүй</span>}
            </div>
            <div>
              <div className="text-xs text-slate-500 mb-1">Backend restart</div>
              <div>
                <span className="font-mono font-bold">{fmtDur(d.app?.uptime_seconds)} өмнө</span>
                <div className="text-[11px] text-slate-500">{d.app?.started_at ? new Date(d.app.started_at * 1000).toLocaleString() : ''}</div>
              </div>
            </div>
          </div>
          {(sys.disk_io || sys.open_files) && (
            <div className="mt-3 pt-3 border-t border-surface-border/50 text-xs text-slate-500 flex flex-wrap gap-x-6 gap-y-1">
              {sys.disk_io && <span>Диск I/O: уншсан {fmtBytes(sys.disk_io.read_bytes)} · бичсэн {fmtBytes(sys.disk_io.write_bytes)}</span>}
              {sys.open_files && <span>Нээлттэй файл: {sys.open_files.allocated.toLocaleString()} / {sys.open_files.max.toLocaleString()}</span>}
            </div>
          )}
        </div>
      )}

      {/* Камер + Хаалт харилцан холболт (хаалт тусад нь бүртгэлтэй үед л харагдана —
          зарим зогсоолд хаалт нь камертайгаа хамт удирддаг тул тусдаа төхөөрөмж байхгүй) */}
      <div className={`grid grid-cols-1 gap-5 ${d.integrations?.barriers?.length ? 'lg:grid-cols-2' : ''}`}>
        <DeviceTable title="Камерууд (хаалт удирдлагатай)" icon={Camera} rows={d.integrations?.cameras} />
        {d.integrations?.barriers?.length > 0 && (
          <DeviceTable title="Хаалтууд" icon={DoorClosed} rows={d.integrations?.barriers} />
        )}
      </div>
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="font-mono font-semibold">{value}</div>
    </div>
  )
}

function DeviceTable({ title, icon: Icon, rows }) {
  return (
    <div className="card">
      <div className="flex items-center gap-2 text-slate-400 text-sm mb-3"><Icon size={16} /> {title}</div>
      <Table headers={['', 'Нэр', 'IP', 'Сүүлд', 'Төлөв']} empty={!rows?.length}>
        {rows?.map((r) => (
          <tr key={r.id}>
            <td className="td w-6"><Dot ok={r.reachable} /></td>
            <td className="td text-sm">{r.name}</td>
            <td className="td font-mono text-xs text-slate-400">{r.ip || '—'}</td>
            <td className="td font-mono text-xs text-slate-400">{ageLabel(r.last_seen_age_sec)}</td>
            <td className="td text-xs">
              {r.reachable === true ? <span className="text-accent">Онлайн</span>
                : r.reachable === false ? <span className="text-red-400">Холбогдохгүй</span>
                  : <span className="text-slate-500">IP-гүй</span>}
            </td>
          </tr>
        ))}
      </Table>
    </div>
  )
}
