// Түүх — бүх session-ийн жагсаалт, шүүлтүүр
import { ArrowDown, ArrowUp, ArrowUpDown, FileSpreadsheet, RotateCcw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, fmt, fmtDate, fmtDur } from '../api'
import { useAuth } from '../auth'
import { useDownload } from '../hooks/useDownload'
import { useFetch } from '../hooks/useFetch'
import { PaymentWaitSince } from '../components/PaymentWait'
import { SnapshotButton } from '../components/Snapshot'
import { ExitReadCell, HistoryPaymentCell, SessionDebtCell } from '../components/HistoryFinancials'
import { Badge, DateRange, Table, useToast } from '../components/ui'
import { normalizePlate } from '../validation'

// Нэг экспортын огнооны дээд муж (backend history_export_max_days-тай ижил)
const EXPORT_MAX_DAYS = 93

// Баганын толгой дээр дарж эрэмбэлэх (system test №59): орсон эсвэл гарсан
// цагаар, буурах ↔ өсөх. Гараагүй мөрүүд гарсан цагийн эрэмбэд төгсгөлд.
function SortHeader({ label, field, sort, setSort }) {
  const [cur, dir] = sort.split('_')
  const active = cur === field
  const Icon = !active ? ArrowUpDown : dir === 'desc' ? ArrowDown : ArrowUp
  return (
    <button type="button" className={`inline-flex items-center gap-1 cursor-pointer ${active ? 'text-accent' : ''}`}
      title={`${label} цагаар эрэмбэлэх`}
      onClick={() => setSort(`${field}_${active && dir === 'desc' ? 'asc' : 'desc'}`)}>
      {label} <Icon size={12} />
    </button>
  )
}

function exportRangeError(f) {
  if (!f.date_from || !f.date_to) return f.plate ? '' : 'Excel татахын тулд огнооны муж (эхлэх, дуусах) сонгоно уу'
  const days = (new Date(f.date_to) - new Date(f.date_from)) / 86400000 + 1
  return days > EXPORT_MAX_DAYS ? `Нэг удаад ${EXPORT_MAX_DAYS} хоног (≈3 сар) хүртэл татна — мужийг хуваана уу` : ''
}

// Том экспорт арын процесст (1–3 сар) — явцыг 2с тутам асууж, бэлэн болмогц татна.
function useHistoryExport() {
  const toast = useToast()
  const dl = useDownload()
  const [job, setJob] = useState(null)
  const fetched = useRef(new Set())

  // Хуудас дахин нээхэд явагдаж буй экспортоо үргэлжлүүлж харна
  useEffect(() => {
    api('/api/sessions/export').then((jobs) => {
      const live = jobs.find((j) => j.status === 'queued' || j.status === 'running')
      if (live) setJob(live)
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return undefined
    const t = setInterval(() => {
      api(`/api/sessions/export/${job.id}`).then(setJob).catch(() => {})
    }, 2000)
    return () => clearInterval(t)
  }, [job?.id, job?.status])

  useEffect(() => {
    if (job?.status === 'done' && !fetched.current.has(job.id)) {
      fetched.current.add(job.id)
      dl(`/api/sessions/export/${job.id}/download`, job.filename || 'tuuh.xlsx')
    }
    if (job?.status === 'failed' && !fetched.current.has(job.id)) {
      fetched.current.add(job.id)
      toast(job.error || 'Экспорт амжилтгүй', 'error')
    }
  }, [job?.status])

  const start = async (body) => {
    try { setJob(await api('/api/sessions/export', { method: 'POST', body })) }
    catch (e) { toast(e.message, 'error') }
  }
  return { job, start, download: () => job && dl(`/api/sessions/export/${job.id}/download`, job.filename || 'tuuh.xlsx') }
}

function ExportStatus({ job, onDownload }) {
  if (!job) return null
  const running = job.status === 'queued' || job.status === 'running'
  const pct = job.total ? Math.min(100, Math.round((job.done || 0) / job.total * 100)) : 0
  return (
    <div className="card text-sm flex flex-wrap items-center gap-3" role="status">
      {running && <>
        <span>Excel бэлтгэж байна… {fmt(job.done || 0)}{job.total != null && ` / ${fmt(job.total)}`} мөр</span>
        <div className="h-2 w-48 rounded bg-surface-muted overflow-hidden"><div className="h-full bg-accent" style={{ width: `${pct}%` }} /></div>
      </>}
      {job.status === 'done' && <>
        <span className="text-accent">Excel бэлэн — {fmt(job.done)} мөр</span>
        <button type="button" className="btn-secondary py-1 text-xs" onClick={onDownload}>Дахин татах</button>
      </>}
      {job.status === 'failed' && <span className="text-red-400">Экспорт амжилтгүй: {job.error}</span>}
      {job.truncated && <span className="text-amber-300">⚠ {fmt(job.max_rows)} мөрийн дээд хязгаар — мужийг богиносгоно уу</span>}
    </div>
  )
}

// Хаагдсан бүртгэлийг буцаан зогсоолд оруулж болох төлвүүд (төлбөргүй хаагдсан)
const REOPENABLE = new Set(['MANUAL_CLOSED', 'FREE', 'CLOSED'])

function ClosedByCell({ s }) {
  const c = s.closed_by
  if (!c) return <span className="text-slate-600">-</span>
  return (
    <span className={`text-[11px] whitespace-nowrap cursor-help ${c.auto ? 'text-slate-400' : 'text-amber-300'}`}
      title={`${c.label}${c.auto ? '' : ` — ${c.by}`}${c.at ? `\n${fmtDate(c.at)}` : ''}`}>
      {c.auto ? 'Систем' : c.by}
    </span>
  )
}

// Хаалтын шалтгааныг камерын уншилт болон өрийн төлвөөс тусад нь харуулна.
function ReasonCell({ s }) {
  const c = s.closed_by
  if (!c) return <span className="text-slate-300">-</span>
  return (
    <span className="text-xs text-slate-200"
      title={`${c.label} — ${c.by}${c.reason_code ? ` (код: ${c.reason_code})` : ''}`}>
      {c.reason_label || c.reason || c.label}
      {c.threshold_hours != null && <span className="block text-slate-300">Босго: {c.threshold_hours} цаг</span>}
      <span className="block text-slate-300">Хаасан: {fmtDate(c.closed_at || c.at)}</span>
    </span>
  )
}

const STATUSES = [
  ['', 'Бүгд'], ['OPEN', 'Зогсож буй'], ['AWAITING_PAYMENT', 'Төлбөр хүлээж буй'],
  ['PAID', 'Төлсөн'], ['CLOSED', 'Гарсан'], ['FREE', 'Үнэгүй'], ['MANUAL_CLOSED', 'Хаасан'],
  // Nested зогсоолтой газарт (Рашбулаг ЭТТ): доторх зогсоолд орж, төлбөрийн
  // тоолуур нь зогссон машинууд. Төлбөр яагаад бага байсныг тайлбарлана.
  ['INNER', 'Дотор зогссон'],
  // Төлөв биш — төлөгдөөгүй нөхөн төлбөртэй (өртэй) дугаарын түүхийг шүүнэ
  ['DEBT', 'Өртэй машин'],
]

export default function History() {
  const { user } = useAuth()
  const toast = useToast()
  const dl = useDownload()
  const isAdmin = ['ADMIN', 'SUPER_ADMIN'].includes(user?.role)
  const [searchParams, setSearchParams] = useSearchParams()
  const linkedSession = searchParams.get('session_id')
  const [filters, setFilters] = useState({ site_id: '', status: '', plate: '', date_from: '', date_to: '',
    date_field: 'entry', company: '' })
  const [sort, setSort] = useState('entry_desc')
  const [page, setPage] = useState(0)
  const limit = 50
  const exporter = useHistoryExport()

  const { data: sites } = useFetch('/api/admin/sites', { initial: [], silent: true })
  // Гэрээт байгууллагын нэрийн санал (Бүртгэлтэй машины эрхгүй бол хоосон үлдэнэ)
  const [companyQuery, setCompanyQuery] = useState('')
  const { data: companies } = useFetch(
    `/api/admin/drivers/company-search?q=${encodeURIComponent(companyQuery)}`,
    { initial: [], silent: true, enabled: companyQuery.trim().length >= 2 })

  const params = new URLSearchParams({ limit, offset: page * limit })
  Object.entries(filters).forEach(([k, v]) => {
    if (!v || (k === 'date_field' && v === 'entry')) return
    // «Дотор зогссон» нь төлөв биш — тусдаа шүүлтүүр (аль хэдийн гарсан ч харагдана)
    if (k === 'status' && v === 'INNER') params.set('inner', 'ever')
    else if (k === 'status' && v === 'DEBT') params.set('debt', '1')
    else params.set(k, v)
  })
  if (sort !== 'entry_desc') params.set('sort', sort)
  if (linkedSession) {
    // A debt link opens the exact permitted stay, independently of prior filters/page.
    for (const key of ['site_id', 'status', 'plate', 'date_from', 'date_to', 'debt', 'inner', 'date_field', 'company', 'sort']) params.delete(key)
    params.set('session_id', linkedSession)
    params.set('offset', '0')
  }
  const { data, reload } = useFetch(`/api/sessions?${params}`, { initial: { total: 0, rows: [] } })

  // Андуурч хаасан бүртгэлийг буцаан зогсоолд оруулах (status→OPEN, цаг үргэлжилнэ)
  const reopen = async (s) => {
    if (!window.confirm(`${s.plate_number}-г буцаан зогсоолд оруулах уу? Орсон цаг хэвээр үлдэж хугацаа үргэлжлэн бодогдоно.`)) return
    try {
      await api(`/api/sessions/${s.id}/reopen`, { method: 'POST' })
      toast(`${s.plate_number} буцаан зогсоолд орлоо`)
      reload()
    } catch (err) { toast(err.message, 'error') }
  }

  // Шүүлтүүр/эрэмбэ өөрчлөгдвөл эхний хуудас руу (path өөрчлөгдмөгц автоматаар дахин татна)
  useEffect(() => { setPage(0) }, [filters, sort])

  // Шүүлтүүрийн ҮР ДҮНГ БҮХЭЛД НЬ (хуудаслалтгүй) Excel — арын процесст, 3 сар хүртэл
  const exportRunning = ['queued', 'running'].includes(exporter.job?.status)
  const exportExcel = () => {
    if (linkedSession) {
      // Нэг зогсолтын холбоос — жижиг тул шууд (синхрон) татна
      dl(`/api/sessions/excel?session_id=${encodeURIComponent(linkedSession)}`, 'tuuh_zogsolt.xlsx')
      return
    }
    const err = exportRangeError(filters)
    if (err) { toast(err, 'error'); return }
    const body = { sort }
    for (const [k, v] of params.entries()) if (!['limit', 'offset'].includes(k)) body[k] = v
    exporter.start(body)
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Түүх</h1>
        <button className="btn-secondary" onClick={exportExcel} disabled={exportRunning}
          title={`Одоогийн шүүлтүүрийн бүх мөрийг Excel болгож татна (${EXPORT_MAX_DAYS} хоног хүртэл)`}>
          <FileSpreadsheet size={16} /> {exportRunning ? 'Бэлтгэж байна…' : 'Excel татах'}
        </button>
      </div>
      <ExportStatus job={exporter.job} onDownload={exporter.download} />
      <p className="text-sm text-slate-300">Гарах камерын уншилт, зогсолтын хаалт, өрийн төлөв тусдаа. Камерт уншигдсан нь хаалт давж гарсны баталгаа биш.</p>
      {linkedSession && <div className="card flex flex-wrap items-center gap-3 text-sm" role="status">
        <span>Өр төлсөн гүйлгээний зогсолтыг харуулж байна.</span>
        <button type="button" className="btn-secondary" onClick={() => { setSearchParams({}); setPage(0) }}>Бүх түүх рүү</button>
      </div>}
      {!linkedSession && <div className="card grid grid-cols-2 gap-3 lg:grid-cols-4">
        {sites.length > 1 && (
          <select className="input" value={filters.site_id} onChange={(e) => setFilters({ ...filters, site_id: e.target.value })} aria-label="Зогсоол">
            <option value="">Бүх зогсоол</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
        <select className="input" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} aria-label="Төлөв">
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <input className="input font-mono uppercase" placeholder="Дугаар…" value={filters.plate} maxLength={7}
          onChange={(e) => setFilters({ ...filters, plate: normalizePlate(e.target.value) })} aria-label="Дугаар" />
        {/* Үсэг бүрд биш — саналаас сонгох, Enter эсвэл талбараас гарахад л шүүнэ */}
        <input className="input" placeholder="Гэрээт байгууллага…" list="history-companies" value={companyQuery}
          aria-label="Гэрээт байгууллага" title="Энэ байгууллагад бүртгэлтэй машинуудын түүх"
          onChange={(e) => {
            const v = e.target.value
            setCompanyQuery(v)
            if (!v || companies.some((c) => c.company === v)) setFilters({ ...filters, company: v })
          }}
          onBlur={() => setFilters({ ...filters, company: companyQuery.trim() })}
          onKeyDown={(e) => { if (e.key === 'Enter') setFilters({ ...filters, company: companyQuery.trim() }) }} />
        <datalist id="history-companies">
          {companies.map((c) => <option key={c.company} value={c.company}>{c.count} машин</option>)}
        </datalist>
        <select className="input" value={filters.date_field} aria-label="Огнооны төрөл"
          title="Огнооны мужийг орсон эсвэл гарсан цагаар шүүнэ"
          onChange={(e) => setFilters({ ...filters, date_field: e.target.value })}>
          <option value="entry">Огноо: орсон цагаар</option>
          <option value="exit">Огноо: гарсан цагаар</option>
        </select>
        <DateRange className="" from={filters.date_from} to={filters.date_to}
          setFrom={(v) => setFilters({ ...filters, date_from: v })}
          setTo={(v) => setFilters({ ...filters, date_to: v })} />
      </div>}

      <Table headers={['Дугаар', 'Зогсоол',
        <SortHeader key="entry" label="Орсон" field="entry" sort={sort} setSort={setSort} />,
        <SortHeader key="exit" label="Гарах цаг / уншилт" field="exit" sort={sort} setSort={setSort} />,
        'Хугацаа', 'Зогсолтын дүн', 'Төлбөрийн задаргаа', 'Энэ зогсолтын өр', 'Хөнгөлөлт',
        'Төлөв', 'Хаасан', 'Шалтгаан', 'Зураг', ...(isAdmin ? [''] : [])]}
        empty={data.rows.length === 0}>
        {data.rows.map((s) => (
          <tr key={s.id} className="hover:bg-surface-muted/30">
            <td className="td font-mono font-bold">
              {s.plate_number}
              {/* Камерын таних итгэлцүүр (орох → гарах). 2026-08-29 хүртэл
                  бүх уншилт «100» гэж бүртгэгддэг байсан тул хуучин мөрөнд
                  100/100 харагдана — бодит хэмжилт үүнээс хойш эхэлсэн. */}
              {(s.confidence_entry != null || s.confidence_exit != null) && (
                <div className="font-normal text-[10px] tabular-nums"
                  title="Камерын дугаар таних итгэлцүүр: орох → гарах (%)">
                  {[s.confidence_entry, s.confidence_exit].map((c, i) => (
                    <span key={i}>
                      {i > 0 && <span className="text-slate-600"> → </span>}
                      {c == null ? <span className="text-slate-600">—</span> : (
                        <span className={c < 60 ? 'text-red-400'
                          : c < 85 ? 'text-amber-400' : 'text-slate-500'}>
                          {Math.round(c)}%
                        </span>
                      )}
                    </span>
                  ))}
                </div>
              )}
            </td>
            <td className="td">{s.site_name}</td>
            {/* Орсон/гарсан цагийн доор АЛЬ камерт уншигдсан нь (2+2 эгнээтэй зогсоол) */}
            <td className="td font-mono text-xs">
              {fmtDate(s.entry_time)}
              {s.entry_device_name && <div className="text-[10px] font-sans text-slate-500">{s.entry_device_name}</div>}
            </td>
            <td className="td font-mono text-xs">
              {fmtDate(s.exit_time)}
              {s.exit_device_name && <div className="text-[10px] font-sans text-sky-300/80">{s.exit_device_name}</div>}
              <ExitReadCell session={s} />
            </td>
            <td className="td font-mono">
              {fmtDur(s.duration_minutes)}
              {/* Доторх (nested) зогсоолд өнгөрүүлсэн хугацаа төлбөрөөс хасагдсан —
                  «яагаад ийм бага дүн гарав» гэдгийг мөрөн дээр нь шууд харуулна */}
              {(s.paused_minutes > 0 || s.paused_since) && (
                <div className="text-[10px] text-sky-300"
                  title={s.paused_since
                    ? 'ОДОО доторх зогсоолд байна — тоолуур зогссон'
                    : 'Доторх зогсоолд өнгөрүүлсэн — энэ хугацаа төлбөрөөс хасагдсан'}>
                  {s.paused_since ? '⏸ дотор' : `дотор ${fmtDur(s.paused_minutes)}`}
                </div>
              )}
            </td>
            <td className="td font-mono font-semibold">{s.total_fee !== null ? `${fmt(s.total_fee)}₮` : '-'}</td>
            <td className="td">
              <HistoryPaymentCell session={s} selectedPaymentId={searchParams.get('payment_id')} />
            </td>
            <td className="td"><SessionDebtCell session={s} /></td>
            <td className="td text-xs">{s.discount_name || '-'}</td>
            <td className="td"><Badge value={s.status} /><PaymentWaitSince session={s} className="mt-0.5" /></td>
            <td className="td"><ClosedByCell s={s} /></td>
            <td className="td max-w-[14rem]"><ReasonCell s={s} /></td>
            <td className="td"><SnapshotButton session={s} /></td>
            {isAdmin && (
              <td className="td text-right">
                {REOPENABLE.has(s.status) && !s.paid_at && (
                  <button className="btn-secondary py-1 text-xs" title="Буцаан зогсоолд оруулах"
                    onClick={() => reopen(s)}>
                    <RotateCcw size={13} /> Сэргээх
                  </button>
                )}
              </td>
            )}
          </tr>
        ))}
      </Table>

      <div className="flex items-center justify-between text-sm text-slate-400">
        <span>Нийт: {fmt(data.total)} мөр</span>
        <div className="flex gap-2">
          <button className="btn-secondary py-1" disabled={!!linkedSession || page === 0}
            onClick={() => setPage(page - 1)}>Өмнөх</button>
          <button className="btn-secondary py-1" disabled={!!linkedSession || (page + 1) * limit >= data.total}
            onClick={() => setPage(page + 1)}>Дараах</button>
        </div>
      </div>
    </div>
  )
}
