// Системийн эрүүл мэнд — сервер metrics, сервисүүд, DB, харилцан холболт (5 сек auto-refresh)
import {
  Activity, AlertTriangle, Camera, Cpu, Database, HardDrive,
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
  'Зогсолт/төлбөр': '#34d399', 'Камер/хаалт': '#a78bfa', 'Лог/түүх': '#fbbf24',
  'Тохиргоо': '#60a5fa', 'Бусад': '#94a3b8',
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
  const [camPerf, setCamPerf] = useState(null) // камерын гүйцэтгэл (1ц/6ц)

  const loadCamPerf = () => api('/api/health/cameras').then(setCamPerf).catch(() => {})
  const load = () => (loadCamPerf(), api('/api/health/system').then((r) => {
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
  }).catch((e) => setErr(e.message)))

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
            {/* Дахин эхэлсэн тоо — олон бол тэр агшин бүрд машин алдагддаг тул
                зөвхөн техникийн биш, ОРЛОГЫН асуудал */}
            {d.restarts && (
              <div className="pt-2 border-t border-surface-border/50 space-y-1">
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2">
                    <Dot ok={d.restarts.level === 'ok' ? true : d.restarts.level === 'warn' ? null : false} />
                    Дахин эхэлсэн ({d.restarts.hours}ц)
                  </span>
                  <span className={`font-mono text-xs ${d.restarts.level === 'ok' ? 'text-accent'
                    : d.restarts.level === 'warn' ? 'text-amber-400' : 'text-red-400'}`}>
                    {d.restarts.restarts} удаа
                  </span>
                </div>
                {d.restarts.reasons?.length > 0 && (
                  <div className="text-[11px] text-slate-400 space-y-0.5 pl-4">
                    {d.restarts.reasons.map((r, i) => (
                      <div key={i} className="flex justify-between gap-2">
                        <span>{r.label}</span><span className="font-mono">{r.count}</span>
                      </div>
                    ))}
                  </div>
                )}
                {d.restarts.level !== 'ok' && (
                  <div className="text-[11px] text-amber-300/90 pl-4">
                    Дахин эхлэх бүрд тэр агшинд орж байсан машин бүртгэгдэхгүй өнгөрдөг.
                  </div>
                )}
              </div>
            )}
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
              {d.database.storage.snapshots && (
                <div className="pt-2 mt-2 border-t border-surface-border/50 flex justify-between text-sm">
                  <span className="flex items-center gap-2 text-slate-300">
                    <span className="w-3 h-3 rounded-sm bg-pink-400" />
                    Камерын зураг (snapshot, диск дээр)
                  </span>
                  <span className="font-mono text-slate-300">
                    {d.database.storage.snapshots.files} файл · {fmtBytes(d.database.storage.snapshots.bytes)}
                  </span>
                </div>
              )}
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
          {/* Мерчант данс тус бүрийн бодит дуудлагын үр дүн. «QPay сайт нээгдэж
              байна» гэдэг нь жолооч QR авч чадна гэсэн үг БИШ — 401/татгалзал
              нь дансны түвшинд болдог тул тусад нь харуулна. */}
          {(d.integrations?.qpay?.accounts || []).filter((a) => a.consecutive_fail > 0).map((a) => (
            <div key={a.username} className="text-xs text-red-400/90 mt-1.5">
              {a.username}: дараалан {a.consecutive_fail} удаа унав — {a.last_error}
            </div>
          ))}
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
            <div>
              {/* Серверийн цаг — «тайлан N цагаар зөрж байна» гомдлын эхний шалгалт.
                  DB бүхэлдээ UTC; харуулах давхарга нь +8 нэмж УБ-ын цаг гаргана. */}
              <div className="text-xs text-slate-500 mb-1">Серверийн цаг</div>
              {d.clock ? (
                <div>
                  <span className="font-mono font-bold">{d.clock.local}</span>
                  <div className="text-[11px] text-slate-500">
                    УБ (UTC+{d.clock.tz_offset_hours}) · UTC {d.clock.utc}
                  </div>
                  {(d.clock.os_utc_skew_sec !== 0 || d.clock.os_tz !== 'Etc/UTC') && (
                    <div className="text-[11px] text-amber-400">
                      OS цагийн бүс {d.clock.os_tz} (зөрүү {d.clock.os_utc_skew_sec}с) —
                      сервер UTC байх ёстой
                    </div>
                  )}
                </div>
              ) : <span className="text-slate-500">—</span>}
            </div>
            <div>
              <div className="text-xs text-slate-500 mb-1">Сервер сүүлд асаасан (reboot)</div>
              {sys.boot_time ? (
                <div>
                  <span className="font-mono font-bold">{fmtDur(sys.uptime_seconds)} өмнө</span>
                  <div className="text-[11px] text-slate-500">{new Date(sys.boot_time * 1000).toLocaleString()}</div>
                </div>
              ) : <span className="text-slate-500">—</span>}
            </div>
          </div>
          {(sys.disk_io || sys.open_files) && (
            <div className="mt-3 pt-3 border-t border-surface-border/50 text-xs text-slate-500 flex flex-wrap gap-x-6 gap-y-1">
              {sys.disk_io && <span>Диск I/O: уншсан {fmtBytes(sys.disk_io.read_bytes)} · бичсэн {fmtBytes(sys.disk_io.write_bytes)}</span>}
              {sys.open_files && <span>Нээлттэй файл: {sys.open_files.allocated.toLocaleString()}{sys.open_files.max > 0 && sys.open_files.max < 1e9 ? ` / ${sys.open_files.max.toLocaleString()}` : ''}</span>}
            </div>
          )}
        </div>
      )}

      {/* Камерын гүйцэтгэл (хуучин Камерууд/Хаалтууд картуудыг орлосон) —
          бүгд DB/санах ойгоос тооцогдоно, камерт нэмэлт ачаалалгүй */}
      {camPerf?.rows?.length > 0 && (
        <div className="card">
          <div className="flex items-center gap-2 mb-2 text-slate-300 font-semibold">
            <Camera size={16} /> Камерын гүйцэтгэл (сүүлийн 1ц / 6ц)
          </div>
          {camPerf.alerts?.length > 0 && (
            <div className="mb-3 space-y-0.5">
              {camPerf.alerts.map((a, i) => (
                <div key={i} className={`text-xs flex items-center gap-1.5 ${a.level === 'red' ? 'text-red-400' : 'text-amber-400'}`}>
                  <AlertTriangle size={12} /> {a.text}
                </div>
              ))}
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead>
                <tr className="text-left text-slate-500">
                  <th className="py-1 pr-3">Камер</th>
                  <th className="py-1 pr-3">Хаалт 1ц</th>
                  <th className="py-1 pr-3">Хаалт 6ц</th>
                  <th className="py-1 pr-3">RPC p95</th>
                  <th className="py-1 pr-3">LED 1ц</th>
                  <th className="py-1 pr-3">LED 6ц</th>
                  <th className="py-1 pr-3">Уншилт 1ц/6ц</th>
                  <th className="py-1 pr-3">Сүүлийн уншилтаас</th>
                  <th className="py-1 pr-3">Сүүлийн бүтэлгүйтэл</th>
                  <th className="py-1">Гадны хандалт</th>
                </tr>
              </thead>
              <tbody>
                {camPerf.rows.map((r) => {
                  const pct = (c) => (c.total ? `${c.ok}/${c.total} (${c.success_pct}%)` : '—')
                  const pctCls = (c) => (c.total >= 5 && c.success_pct < 90 ? 'text-red-400 font-semibold' : '')
                  const led = (l) => ((l.ok + l.fail) ? `${l.ok}/${l.ok + l.fail}` : '—')
                  const ledCls = (l) => ((l.ok + l.fail) >= 5 && l.ok * 2 < l.ok + l.fail ? 'text-red-400 font-semibold' : '')
                  return (
                    <tr key={r.ip + r.camera} className="border-t border-surface-muted">
                      <td className="py-1.5 pr-3">
                        {r.site_code} · {r.camera}{' '}
                        <span className="font-mono text-slate-500">{r.ip}</span>
                      </td>
                      <td className={`py-1.5 pr-3 font-mono ${pctCls(r.cmd_1h)}`}>{pct(r.cmd_1h)}</td>
                      <td className={`py-1.5 pr-3 font-mono ${pctCls(r.cmd_6h)}`}>{pct(r.cmd_6h)}</td>
                      <td className="py-1.5 pr-3 font-mono">
                        {r.cmd_6h.p95_ms != null
                          ? (r.cmd_6h.p95_ms >= 1000 ? `${(r.cmd_6h.p95_ms / 1000).toFixed(1)}с` : `${r.cmd_6h.p95_ms}мс`)
                          : '—'}
                      </td>
                      <td className={`py-1.5 pr-3 font-mono ${ledCls(r.led_1h)}`}>{led(r.led_1h)}</td>
                      <td className={`py-1.5 pr-3 font-mono ${ledCls(r.led_6h)}`}>{led(r.led_6h)}</td>
                      <td className="py-1.5 pr-3 font-mono">{r.events_1h}/{r.events_6h}</td>
                      <td className="py-1.5 pr-3">{r.gap_now_min != null ? `${r.gap_now_min} мин` : '—'}</td>
                      <td className="py-1.5 pr-3 font-mono">
                        {r.cmd_6h.last_fail_at ? new Date(r.cmd_6h.last_fail_at + 'Z').toLocaleTimeString() : '—'}
                      </td>
                      <td className="py-1.5">
                        {/* Гурван ТӨЛӨВ: илэрсэн (улаан) / хэмжигдсэн-цэвэр (саарал) /
                            ХЭМЖИГДЭЭГҮЙ (шар). Өмнө нь гурвуулаа «—» харагддаг тул
                            хэмжилт бүтэн унасныг «гадны хандалт алга» гэж уншиж байв. */}
                        {(r.foreign_sessions || []).length > 0 ? (
                          <span className="text-red-400">
                            {r.foreign_sessions.map((s) => `${s.user}@${s.ip}`).join(', ')}
                          </span>
                        ) : r.foreign_checked_at ? (
                          <span
                            className="text-slate-600"
                            title={`Хэмжигдсэн: ${new Date(r.foreign_checked_at + 'Z').toLocaleTimeString()} — гадны хандалт илрээгүй`}
                          >
                            цэвэр
                          </span>
                        ) : (
                          <span className="text-amber-400" title={r.foreign_error || 'Хэмжилт хараахан ажиллаагүй'}>
                            хэмжигдээгүй
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-slate-500 mt-2">
            Бүх үзүүлэлт серверийн өгөгдлөөс тооцогдоно — камерт нэмэлт ачаалал өгөхгүй.
            LED тоолуур backend restart-аас хойш цуглардаг.
          </p>
          {camPerf.foreign_status && (
            <p className="text-[11px] text-slate-500">
              Гадны хандалт:{' '}
              {camPerf.foreign_status.enabled
                ? `${camPerf.foreign_status.measured}/${camPerf.foreign_status.cameras} камер хэмжигдсэн · ${Math.round(camPerf.foreign_status.period_sec / 60)} мин тутам, сүүлийн ${camPerf.foreign_status.window_min} мин цонх`
                : `ХЭМЖИГДЭХГҮЙ — ${camPerf.foreign_status.reason}`}
              {camPerf.foreign_status.last_ok_at
                ? ` · сүүлд ${new Date(camPerf.foreign_status.last_ok_at + 'Z').toLocaleTimeString()}`
                : ''}
            </p>
          )}
        </div>
      )}
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

