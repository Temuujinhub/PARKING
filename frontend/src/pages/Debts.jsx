// Санхүү → Өр цэвэрлэх. Зогсоол дээр хуримтлагдсан төлөгдөөгүй өрийг (нөхөн төлбөр)
// дугаараар бүлэглэж, сонгоод ТАЙЛБАРТАЙГААР бөөнөөр цэвэрлэнэ. Өр устдаггүй —
// «Цуцалсан» болж хэн/хэзээ/яагаад нь мөр дээрээ + аудит логонд үлдэнэ
// («Цэвэрлэлтийн лог» таб). Цэвэрлэсэн өр QR нэхэмжлэл, Түүхийн улаан «өр N₮»,
// кассын тэмдэглэгээнээс шууд алга болно.
// 2026-10-22 (№58): өр ҮҮССЭН огноо/цагийн муж («хэдний өдөр хэдэн цагаас хэдэн цагийн
// хооронд аль зогсоол дээр ямар машинд өр үүссэн»), төлөв/шалтгаан, «Өр тус бүр»
// харагдац, «Цааш ачаалах» хуудаслалт (өмнө нь 1000 мөрөөр таслагддаг байв), Excel.
import { AlertTriangle, ChevronDown, ChevronRight, Eraser, FileSpreadsheet, List, RefreshCw, ScrollText, Search, Users } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, fmt, fmtDate, fmtMDHM, fmtShort, preferredSite, rememberSite } from '../api'
import { useAuth } from '../auth'
import { Field, Modal, Table, useToast } from '../components/ui'
import { useDownload } from '../hooks/useDownload'
import { dateRangeError, toDateInput } from '../validation'

const REASONS = {
  unpaid_exit: 'Төлбөргүй гаргасан', night_close: 'Шөнийн хаалт', shift_close: 'Ээлж хаалт', manual: 'Гараар',
  admin_remove: 'Админ хассан', auto_close: 'Авто хаалт', log_exit: 'Логоор гарсан',
  camera_sync: 'Камерын логоор нөхсөн', camera_sync_exit: 'Камерын логоор гарсан', camera_backfill: 'Камерын нөхөлт',
  reconcile: 'Тулгалт', system_error: 'Системийн алдаа', other: 'Бусад',
}
const STATUS_OPTS = [['PENDING', 'Төлөгдөөгүй'], ['PAID', 'Төлөгдсөн'], ['CANCELLED', 'Цуцлагдсан'], ['', 'Бүгд']]
const STATUS_STYLE = { PENDING: 'bg-red-500/15 text-red-400', PAID: 'bg-accent/15 text-accent', CANCELLED: 'bg-slate-500/15 text-slate-300' }
const AGE_OPTS = [[0, 'Бүх хугацаа'], [7, '7+ хоног'], [30, '30+ хоног'], [90, '90+ хоног']]
const PAGE = 500           // нэг ачаалалтын мөр («Цааш ачаалах» дараагийнхыг нэмнэ)
const WRITEOFF_MAX = 1000  // backend WRITEOFF_MAX_IDS
const EXCEL_MAX = 50000    // backend DEBT_EXCEL_MAX
const EMPTY = { plate: '', from: '', to: '' }
// datetime-local утга (YYYY-MM-DDTHH:MM, хөтчийн = УБ-ын цаг). Backend нь локал гэж
// ойлгож UTC руу хөрвүүлнэ; «23:59» дээд зааг тэр минутыг бүхэлд нь багтаана.
const dayStart = (d) => `${toDateInput(d)}T00:00`
const dayEnd = (d) => `${toDateInput(d)}T23:59`
const daysAgo = (n) => new Date(Date.now() - n * 86400000)
const PRESETS = [
  ['Өнөөдөр', () => ({ from: dayStart(new Date()), to: dayEnd(new Date()) })],
  ['Өчигдөр', () => ({ from: dayStart(daysAgo(1)), to: dayEnd(daysAgo(1)) })],
  ['7 хоног', () => ({ from: dayStart(daysAgo(6)), to: dayEnd(new Date()) })],
  ['30 хоног', () => ({ from: dayStart(daysAgo(29)), to: dayEnd(new Date()) })],
]
// Түгээмэл тайлбар — нэг товшилтоор; гараар засаж болно
const QUICK_REASONS = [
  'Системийн алдаа — хуурамч өр (машин бодитоор зогсоогүй)',
  'Эмнэлгийн ажилтан / гэрээт машин — өр хамаарахгүй',
  'Төлбөрийг гараар авсан, системд бүртгэгдээгүй',
  'Удирдлагын шийдвэрээр чөлөөлөв',
]

const STATUS_LABEL = Object.fromEntries(STATUS_OPTS)
function StatusPill({ s }) {
  return <span className={`ml-1 px-1.5 py-0.5 rounded text-[10px] ${STATUS_STYLE[s] || 'bg-slate-500/15 text-slate-300'}`}>{STATUS_LABEL[s] || s}</span>
}

export default function Debts() {
  const toast = useToast()
  const { user } = useAuth()
  const [sites, setSites] = useState([])
  const [siteId, setSiteId] = useState('')
  const [minDays, setMinDays] = useState(0)
  const [status, setStatus] = useState('PENDING')
  const [reason, setReason] = useState('')
  // Дугаар/огноо нь бичих явцад биш «Хайх»/Enter дарахад хэрэгжинэ (draft → applied)
  const [draft, setDraft] = useState(EMPTY)
  const [applied, setApplied] = useState(EMPTY)
  const [nonce, setNonce] = useState(0)                         // ижил шүүлтээр дахин ачаалах
  const [view, setView] = useState('plate')                     // plate = дугаараар · list = өр тус бүр
  const [tab, setTab] = useState('pending')
  const [data, setData] = useState({ rows: [], total: 0, sum_amount: 0, plates: 0, total_pending: 0, pending_count: 0 })
  const [logData, setLogData] = useState({ rows: [], total: 0 })
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const reqId = useRef(0)                                       // хуучирсан хариуг хаях
  const dl = useDownload()
  const [selected, setSelected] = useState(() => new Set())   // comp id-ууд
  const [expanded, setExpanded] = useState(() => new Set())   // задалсан дугаарууд
  const [modal, setModal] = useState(null)                    // {reason, unblacklist}
  const [busy, setBusy] = useState(false)
  const [lastResult, setLastResult] = useState(null)

  useEffect(() => {
    api('/api/admin/sites').then((s) => { setSites(s); setSiteId(preferredSite(s)) }).catch(() => {})
  }, [])

  // Жагсаалт ба Excel НЭГ шүүлтээр (хоёр өөр үр дүн гаргахгүй)
  const params = () => {
    const p = new URLSearchParams()
    if (status) p.set('status', status)
    if (siteId) p.set('site_id', siteId)
    if (applied.plate.trim()) p.set('plate', applied.plate.trim())
    if (minDays) p.set('min_days', minDays)
    if (applied.from) p.set('created_from', applied.from)
    if (applied.to) p.set('created_to', applied.to)
    if (reason) p.set('reason', reason)
    return p
  }
  const load = (offset = 0) => {
    const id = ++reqId.current
    const p = params()
    p.set('limit', PAGE)
    p.set('offset', offset)
    offset ? setLoadingMore(true) : setLoading(true)
    api(`/api/compensations?${p}`).then((d) => {
      if (id !== reqId.current) return
      if (offset) {
        // Хооронд шинэ өр үүсвэл offset шилжинэ — давхардсан мөрийг хаяна
        setData((prev) => {
          const seen = new Set(prev.rows.map((c) => c.id))
          return { ...d, rows: [...prev.rows, ...d.rows.filter((c) => !seen.has(c.id))] }
        })
      } else { setData(d); setSelected(new Set()) }
    }).catch((e) => toast(e.message, 'error'))
      .finally(() => { if (id === reqId.current) { setLoading(false); setLoadingMore(false) } })
  }
  const loadLog = () => {
    const p = new URLSearchParams({ limit: 500 })
    if (siteId) p.set('site_id', siteId)
    if (applied.plate.trim()) p.set('plate', applied.plate.trim())
    api(`/api/compensations/write-off-log?${p}`).then(setLogData).catch((e) => toast(e.message, 'error'))
  }
  useEffect(() => { if (sites.length) { load(); loadLog() } }, [siteId, minDays, status, reason, applied, nonce, sites.length]) // eslint-disable-line react-hooks/exhaustive-deps
  const rangeErr = dateRangeError(draft.from, draft.to)
  const search = () => {
    if (rangeErr) return toast(rangeErr, 'error')
    setApplied({ ...draft }); setNonce((n) => n + 1)
  }
  const applyRange = (r) => { const next = { ...draft, ...r }; setDraft(next); setApplied(next) }
  const exportExcel = () => {
    if (data.total > EXCEL_MAX && !confirm(`Шүүлтэд ${fmt(data.total)} өр байна — Excel-д хамгийн сүүлийн ${fmt(EXCEL_MAX)} нь л орно.\nИнгээд татах уу? (Бүгдийг авахын тулд огноо/зогсоолоор хувааж татна уу)`)) return
    dl(`/api/compensations/excel?${params()}`, `ur_${toDateInput()}.xlsx`)
  }

  const siteName = sites.find((s) => s.id === siteId)?.name || 'Бүх зогсоол'
  const rangeLabel = applied.from || applied.to
    ? `${applied.from ? applied.from.replace('T', ' ') : '…'} — ${applied.to ? applied.to.replace('T', ' ') : '…'}`
    : ''

  // Дугаараар бүлэглэх — «өртэй хүн» бүр нэг мөр
  const groups = useMemo(() => {
    const m = new Map()
    for (const c of data.rows) {
      const g = m.get(c.plate_number) || { plate: c.plate_number, comps: [], total: 0, sites: new Set(), oldest: c.created_at, newest: c.created_at, pending_count: c.pending_count }
      g.comps.push(c); g.total += Number(c.amount || 0)
      if (c.site_name) g.sites.add(c.site_name)
      if (c.created_at < g.oldest) g.oldest = c.created_at
      if (c.created_at > g.newest) g.newest = c.created_at
      m.set(c.plate_number, g)
    }
    return [...m.values()].sort((a, b) => b.total - a.total)
  }, [data.rows])

  // Зөвхөн ТӨЛӨГДӨӨГҮЙ өрийг цэвэрлэж болно (төлөв «Бүгд»/«Төлөгдсөн» үед бусад нь сонгогдохгүй)
  const allIds = useMemo(() => data.rows.filter((c) => c.status === 'PENDING').map((c) => c.id), [data.rows])
  const selComps = data.rows.filter((c) => selected.has(c.id))
  const selTotal = selComps.reduce((s, c) => s + Number(c.amount || 0), 0)
  const selPlates = new Set(selComps.map((c) => c.plate_number)).size

  const toggle = (ids, on) => setSelected((prev) => {
    const n = new Set(prev); ids.forEach((id) => (on ? n.add(id) : n.delete(id))); return n
  })
  const pendingIds = (g) => g.comps.filter((c) => c.status === 'PENDING').map((c) => c.id)
  const groupState = (g) => {
    const ids = pendingIds(g)
    const n = ids.filter((id) => selected.has(id)).length
    return n === 0 ? 'none' : n === ids.length ? 'all' : 'some'
  }
  const toggleExpand = (p) => setExpanded((prev) => { const n = new Set(prev); n.has(p) ? n.delete(p) : n.add(p); return n })

  const doWriteOff = async () => {
    if (!modal.reason || modal.reason.trim().length < 3) return toast('Тайлбар заавал (3+ тэмдэгт)', 'error')
    if (selected.size > WRITEOFF_MAX) return toast(`Нэг удаад ${WRITEOFF_MAX} хүртэл өр цэвэрлэнэ — сонголтоо багасгана уу`, 'error')
    setBusy(true)
    try {
      const r = await api('/api/compensations/write-off', {
        method: 'POST', body: { ids: [...selected], reason: modal.reason.trim(), unblacklist: modal.unblacklist },
      })
      setLastResult(r)
      toast(`${r.count} өр · ${fmt(r.total)}₮ · ${r.plates.length} дугаар цэвэрлэгдлээ`)
      setModal(null); load(); loadLog()
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold">Өр цэвэрлэх</h1>
          <p className="text-sm text-slate-400 mt-1">
            Төлөгдөөгүй өрийг тайлбартайгаар цуцална — өр устахгүй, хэн/хэзээ/яагаад нь логонд үлдэнэ.
            Цэвэрлэсэн өр QR нэхэмжлэл, Түүхийн улаан «өр», кассын тэмдэглэгээнээс шууд алга болно.
          </p>
        </div>
        <button className="btn-secondary" onClick={search} disabled={loading}><RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> Шинэчлэх</button>
      </div>

      {/* Шүүлтүүр — өр ҮҮССЭН огноо/цаг, зогсоол, дугаар, төлөв, шалтгаан, нас */}
      <div className="card space-y-3">
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 items-end">
          <Field label="Зогсоол">
            <select className="input" value={siteId} onChange={(e) => { setSiteId(e.target.value); rememberSite(e.target.value) }}>
              <option value="">Бүх зогсоол</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </Field>
          <Field label="Төлөв">
            <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUS_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          <Field label="Шалтгаан">
            <select className="input" value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="">Бүх шалтгаан</option>
              {Object.entries(REASONS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          <Field label="Өрийн нас">
            <select className="input" value={minDays} onChange={(e) => setMinDays(Number(e.target.value))}>
              {AGE_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </Field>
          <div className="col-span-2">
            <Field label="Дугаар">
              <input className="input font-mono" placeholder="Хайх…" value={draft.plate}
                onChange={(e) => setDraft({ ...draft, plate: e.target.value.toUpperCase() })}
                onKeyDown={(e) => e.key === 'Enter' && search()} />
            </Field>
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Өр үүссэн — эхлэх (огноо, цаг)">
            <input type="datetime-local" className={`input w-auto${rangeErr ? ' input-error' : ''}`} value={draft.from}
              max={draft.to || undefined} aria-invalid={!!rangeErr}
              onChange={(e) => setDraft({ ...draft, from: e.target.value })}
              onKeyDown={(e) => e.key === 'Enter' && search()} />
          </Field>
          <Field label="дуусах (огноо, цаг)">
            <input type="datetime-local" className={`input w-auto${rangeErr ? ' input-error' : ''}`} value={draft.to}
              min={draft.from || undefined} aria-invalid={!!rangeErr}
              onChange={(e) => setDraft({ ...draft, to: e.target.value })}
              onKeyDown={(e) => e.key === 'Enter' && search()} />
          </Field>
          <div className="flex flex-wrap gap-1.5 pb-1">
            {PRESETS.map(([l, fn]) => (
              <button key={l} type="button" onClick={() => applyRange(fn())}
                className="text-xs px-2.5 py-1.5 rounded-md bg-surface-muted/60 text-slate-300 hover:bg-surface-muted cursor-pointer border border-surface-border/60">
                {l}
              </button>
            ))}
            {(draft.from || draft.to) && (
              <button type="button" onClick={() => applyRange({ from: '', to: '' })}
                className="text-xs px-2.5 py-1.5 rounded-md text-slate-400 hover:text-slate-200 cursor-pointer underline">
                Огноо арилгах
              </button>
            )}
          </div>
          <button className="btn-primary ml-auto" onClick={search} disabled={loading || !!rangeErr}>
            <Search size={15} /> Хайх
          </button>
        </div>
        {rangeErr && <div className="hint-error" role="alert">{rangeErr}</div>}
      </div>

      {/* Шүүлтийн дүн — бүх мөрөөр (хуудаслалтаас үл хамаарна) */}
      <div className="card py-3 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <div className="text-xs text-slate-500">
            Шүүлтийн үр дүн · {siteName}{rangeLabel ? ` · ${rangeLabel}` : ''}
          </div>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="font-mono text-2xl font-bold text-red-400">{fmt(data.sum_amount)}₮</span>
            <span className="text-sm text-slate-300"><b className="font-mono">{fmt(data.total)}</b> өр · <b className="font-mono">{fmt(data.plates)}</b> дугаар</span>
          </div>
          <div className="text-xs text-slate-500 mt-0.5">
            {siteName} нийт төлөгдөөгүй: {fmt(data.total_pending)}₮ · {fmt(data.pending_count)} өр
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex rounded-lg border border-surface-border/60 overflow-hidden" role="group" aria-label="Харагдац">
            {[['plate', 'Дугаараар', Users], ['list', 'Өр тус бүр', List]].map(([v, l, Icon]) => (
              <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}
                className={`px-3 py-1.5 text-sm flex items-center gap-1.5 cursor-pointer ${view === v ? 'bg-accent/15 text-accent' : 'text-slate-400 hover:text-slate-200'}`}>
                <Icon size={14} /> {l}
              </button>
            ))}
          </div>
          <button className="btn-secondary" onClick={exportExcel} disabled={!data.total}
            title="Одоогийн шүүлтийн бүх мөрийг (огноо, цаг, зогсоол, дугаар, дүн, орсон/гарсан цаг) Excel болгож татна">
            <FileSpreadsheet size={16} /> Excel татах
          </button>
        </div>
      </div>

      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {[['pending', 'Өрийн жагсаалт', Eraser], ['log', 'Цэвэрлэлтийн лог', ScrollText]].map(([v, l, Icon]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer flex items-center gap-1.5
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            <Icon size={14} /> {l}{v === 'log' && logData.rows.length ? ` (${logData.rows.length})` : ''}
          </button>
        ))}
      </div>

      {tab === 'pending' && (
        <>
          {view === 'plate' && data.rows.length < data.total && (
            <div className="card py-2 text-xs text-amber-400 flex items-center gap-2">
              <AlertTriangle size={14} /> Дугаараар бүлэглэлт ачаалсан {fmt(data.rows.length)} / {fmt(data.total)} өр дээр —
              бүгдийг хамруулахын тулд «Цааш ачаалах» эсвэл огноо/зогсоолоор нарийсгана уу.
            </div>
          )}
          {lastResult && (
            <div className="card py-2 text-sm text-accent flex items-center justify-between gap-2 flex-wrap">
              <span>Сүүлийн цэвэрлэлт: <b>{lastResult.count}</b> өр · <b>{fmt(lastResult.total)}₮</b> · {lastResult.plates.length} дугаар
                {lastResult.unblacklisted?.length ? ` · хар жагсаалтаас хасав: ${lastResult.unblacklisted.join(', ')}` : ''}
                {lastResult.skipped?.length || lastResult.missing ? ` · алгассан: ${lastResult.skipped.length + (lastResult.missing || 0)}` : ''}
              </span>
              <span className="text-xs text-slate-500 font-mono">batch {lastResult.batch_id}</span>
            </div>
          )}

          {view === 'plate' ? (
            <Table headers={[
              <input key="all" type="checkbox" aria-label="Бүгдийг сонгох" disabled={!allIds.length}
                checked={allIds.length > 0 && selected.size === allIds.length}
                ref={(el) => { if (el) el.indeterminate = selected.size > 0 && selected.size < allIds.length }}
                onChange={(e) => toggle(allIds, e.target.checked)} />,
              'Дугаар', 'Зогсоол', 'Өрийн тоо', 'Нийт дүн', 'Хамгийн хуучин', 'Хамгийн сүүлийн', 'Шалтгаан',
            ]} empty={groups.length === 0} maxH="60vh">
              {groups.map((g) => {
                const st = groupState(g)
                const open = expanded.has(g.plate)
                const kinds = [...new Set(g.comps.map((c) => REASONS[c.reason] || c.reason))]
                const selectable = pendingIds(g)
                return [
                  <tr key={g.plate} className={st !== 'none' ? 'bg-accent/5' : ''}>
                    <td className="td w-8">
                      <input type="checkbox" checked={st === 'all'} aria-label={`${g.plate} сонгох`} disabled={!selectable.length}
                        ref={(el) => { if (el) el.indeterminate = st === 'some' }}
                        onChange={(e) => toggle(selectable, e.target.checked)} />
                    </td>
                    <td className="td font-mono font-bold text-red-400 cursor-pointer select-none" onClick={() => toggleExpand(g.plate)}>
                      <span className="inline-flex items-center gap-1">
                        {open ? <ChevronDown size={14} className="text-slate-500" /> : <ChevronRight size={14} className="text-slate-500" />}
                        {g.plate}
                      </span>
                      {g.pending_count >= 3 && <span className="ml-1 text-[10px] bg-red-500/20 text-red-400 px-1 rounded">хориг</span>}
                    </td>
                    <td className="td text-xs">{[...g.sites].join(', ') || '—'}</td>
                    <td className="td font-mono">{g.comps.length}</td>
                    <td className="td font-mono font-semibold">{fmt(g.total)}₮</td>
                    <td className="td font-mono text-xs">{fmtShort(g.oldest)}</td>
                    <td className="td font-mono text-xs">{fmtShort(g.newest)}</td>
                    <td className="td text-xs text-slate-400">{kinds.join(', ')}</td>
                  </tr>,
                  open && g.comps.map((c) => (
                    <tr key={c.id} className="bg-surface-muted/20">
                      <td className="td w-8 pl-6">
                        <input type="checkbox" checked={selected.has(c.id)} aria-label={`${c.plate_number} ${fmt(c.amount)}₮ сонгох`}
                          disabled={c.status !== 'PENDING'} onChange={(e) => toggle([c.id], e.target.checked)} />
                      </td>
                      <td className="td text-xs text-slate-500 pl-8">↳ өр{c.status !== 'PENDING' && <StatusPill s={c.status} />}</td>
                      <td className="td text-xs">{c.site_name || '—'}</td>
                      <td className="td text-xs text-slate-500">{c.days_old} хоног</td>
                      <td className="td font-mono">{fmt(c.amount)}₮</td>
                      <td className="td font-mono text-xs" colSpan={2}>
                        {fmtDate(c.created_at)}
                        {c.entry_time && <div className="text-[10px] font-sans text-slate-500">зогсолт {fmtMDHM(c.entry_time)} → {c.exit_time ? fmtMDHM(c.exit_time) : '…'}</div>}
                      </td>
                      <td className="td text-xs text-slate-400">{REASONS[c.reason] || c.reason} · {c.created_by}</td>
                    </tr>
                  )),
                ]
              })}
            </Table>
          ) : (
            <Table headers={[
              <input key="all" type="checkbox" aria-label="Бүгдийг сонгох" disabled={!allIds.length}
                checked={allIds.length > 0 && selected.size === allIds.length}
                ref={(el) => { if (el) el.indeterminate = selected.size > 0 && selected.size < allIds.length }}
                onChange={(e) => toggle(allIds, e.target.checked)} />,
              'Өр үүссэн', 'Дугаар', 'Зогсоол', 'Дүн', 'Шалтгаан', 'Зогсолт (орсон → гарсан)', 'Төлөв', 'Үүсгэсэн',
            ]} empty={data.rows.length === 0} maxH="60vh">
              {data.rows.map((c) => (
                <tr key={c.id} className={selected.has(c.id) ? 'bg-accent/5' : ''}>
                  <td className="td w-8">
                    <input type="checkbox" checked={selected.has(c.id)} aria-label={`${c.plate_number} ${fmt(c.amount)}₮ сонгох`}
                      disabled={c.status !== 'PENDING'} onChange={(e) => toggle([c.id], e.target.checked)} />
                  </td>
                  <td className="td font-mono text-xs whitespace-nowrap">{fmtDate(c.created_at)}</td>
                  <td className="td font-mono font-bold text-red-400">
                    {c.plate_number}
                    {c.pending_count >= 3 && <span className="ml-1 text-[10px] bg-red-500/20 text-red-400 px-1 rounded">хориг</span>}
                  </td>
                  <td className="td text-xs">{c.site_name || '—'}</td>
                  <td className="td font-mono font-semibold">{fmt(c.amount)}₮</td>
                  <td className="td text-xs text-slate-400">{REASONS[c.reason] || c.reason}</td>
                  <td className="td font-mono text-xs whitespace-nowrap">
                    {c.entry_time ? `${fmtMDHM(c.entry_time)} → ${c.exit_time ? fmtMDHM(c.exit_time) : '…'}` : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="td"><StatusPill s={c.status} /></td>
                  <td className="td text-xs text-slate-500">{c.created_by}</td>
                </tr>
              ))}
            </Table>
          )}

          {data.rows.length < data.total && (
            <div className="flex items-center justify-center gap-3 text-sm text-slate-400">
              <span>Ачаалсан {fmt(data.rows.length)} / {fmt(data.total)}</span>
              <button className="btn-secondary" onClick={() => load(data.rows.length)} disabled={loadingMore || loading}>
                {loadingMore ? 'Ачаалж байна…' : `Цааш ачаалах (+${fmt(Math.min(PAGE, data.total - data.rows.length))})`}
              </button>
            </div>
          )}

          {/* Үйлдлийн мөр — сонголт байхад л */}
          <div className={`card sticky bottom-3 flex items-center justify-between gap-3 flex-wrap transition-opacity ${selected.size ? 'opacity-100' : 'opacity-60'}`}>
            <div className="text-sm">
              Сонгосон: <b className="font-mono">{selPlates}</b> дугаар · <b className="font-mono">{selected.size}</b> өр ·{' '}
              <b className="font-mono text-red-400">{fmt(selTotal)}₮</b>
              {selected.size > 0 && <button className="ml-3 text-xs text-slate-400 underline cursor-pointer" onClick={() => setSelected(new Set())}>цэвэрлэх</button>}
              {selected.size > WRITEOFF_MAX && <span className="ml-3 text-xs text-amber-400">нэг удаад {WRITEOFF_MAX} хүртэл</span>}
            </div>
            <button className="btn-danger" disabled={!selected.size || selected.size > WRITEOFF_MAX}
              onClick={() => setModal({ reason: '', unblacklist: true })}>
              <Eraser size={16} /> Сонгосон өрийг цэвэрлэх…
            </button>
          </div>
        </>
      )}

      {tab === 'log' && (
        <>
          <div className="text-sm text-slate-400 flex items-center justify-between flex-wrap gap-2">
            <span>{siteName} · цэвэрлэсэн/цуцалсан өр — өр тус бүр нэг мөр (хэн, хэзээ, тайлбар)</span>
            <span>Нийт: <b className="font-mono text-slate-200">{fmt(logData.total)}₮</b> · {logData.rows.length} мөр</span>
          </div>
          <Table headers={['Огноо', 'Хэн', 'Дугаар', 'Зогсоол', 'Дүн', 'Өр үүссэн', 'Тайлбар', 'Төрөл']} empty={logData.rows.length === 0} maxH="65vh">
            {logData.rows.map((r) => (
              <tr key={r.id}>
                <td className="td font-mono text-xs whitespace-nowrap">{fmtDate(r.at)}</td>
                <td className="td text-xs">{r.by}</td>
                <td className="td font-mono font-bold">{r.plate || '—'}</td>
                <td className="td text-xs">{r.site_name || '—'}</td>
                <td className="td font-mono">{r.amount != null ? `${fmt(r.amount)}₮` : '—'}</td>
                <td className="td text-xs text-slate-500">{r.debt_created_at ? fmtShort(r.debt_created_at) : '—'}{r.debt_reason ? ` · ${REASONS[r.debt_reason] || r.debt_reason}` : ''}</td>
                <td className="td text-sm max-w-md">{r.reason || <span className="text-slate-600">—</span>}</td>
                <td className="td text-xs">
                  {r.kind === 'write_off'
                    ? <span className="px-2 py-0.5 rounded-md bg-accent/15 text-accent">Цэвэрлэлт{r.batch_id ? <span className="font-mono text-[10px] text-slate-500 ml-1">{r.batch_id}</span> : null}</span>
                    : <span className="px-2 py-0.5 rounded-md bg-slate-500/15 text-slate-300">Цуцлалт</span>}
                </td>
              </tr>
            ))}
          </Table>
        </>
      )}

      {/* Баталгаажуулах — тайлбар заавал */}
      <Modal open={!!modal} onClose={() => !busy && setModal(null)} title="Өр цэвэрлэх — баталгаажуулах">
        {modal && (
          <div className="space-y-4">
            <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 text-sm">
              <div className="flex items-center gap-2 text-red-400 font-semibold"><AlertTriangle size={16} /> Буцаах боломжгүй үйлдэл</div>
              <div className="mt-1 text-slate-300">
                <b className="font-mono">{selPlates}</b> дугаарын <b className="font-mono">{selected.size}</b> өр,
                нийт <b className="font-mono text-red-400">{fmt(selTotal)}₮</b> «Цуцалсан» болно.
                Таны нэр ({user?.username}), огноо, тайлбар өр бүр дээр + логонд үлдэнэ.
              </div>
            </div>
            <Field label="Тайлбар (заавал — яагаад цэвэрлэж байгаа нь)" required>
              <textarea className="input min-h-[80px]" value={modal.reason} maxLength={500} autoFocus
                placeholder="Ж: 3-р эмнэлгийн ажилтны машин — өр хамаарахгүй, захирлын 09-07-ны шийдвэр"
                onChange={(e) => setModal({ ...modal, reason: e.target.value })} />
              <div className="flex flex-wrap gap-1.5 mt-2">
                {QUICK_REASONS.map((q) => (
                  <button key={q} type="button" onClick={() => setModal({ ...modal, reason: q })}
                    className="text-[11px] px-2 py-1 rounded-md bg-surface-muted/60 text-slate-300 hover:bg-surface-muted cursor-pointer border border-surface-border/60">
                    {q}
                  </button>
                ))}
              </div>
            </Field>
            <label className="flex items-start gap-2 text-sm cursor-pointer">
              <input type="checkbox" className="mt-0.5" checked={modal.unblacklist}
                onChange={(e) => setModal({ ...modal, unblacklist: e.target.checked })} />
              <span>Өр үлдээгүй дугаарын <b>автомат</b> хар жагсаалтын хоригийг цуцлах
                <span className="block text-xs text-slate-500">Гараар нэмсэн хар жагсаалтын бичлэгт хүрэхгүй.</span></span>
            </label>
            <button onClick={doWriteOff} disabled={busy || modal.reason.trim().length < 3}
              className="btn-danger w-full justify-center py-3">
              <Eraser size={16} /> {busy ? 'Цэвэрлэж байна…' : `${selected.size} өр · ${fmt(selTotal)}₮ цэвэрлэх`}
            </button>
          </div>
        )}
      </Modal>
    </div>
  )
}
