// Санхүү → Өр цэвэрлэх. Зогсоол дээр хуримтлагдсан төлөгдөөгүй өрийг (нөхөн төлбөр)
// дугаараар бүлэглэж, сонгоод ТАЙЛБАРТАЙГААР бөөнөөр цэвэрлэнэ. Өр устдаггүй —
// «Цуцалсан» болж хэн/хэзээ/яагаад нь мөр дээрээ + аудит логонд үлдэнэ
// («Цэвэрлэлтийн лог» таб). Цэвэрлэсэн өр QR нэхэмжлэл, Түүхийн улаан «өр N₮»,
// кассын тэмдэглэгээнээс шууд алга болно.
import { AlertTriangle, ChevronDown, ChevronRight, Eraser, RefreshCw, ScrollText, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, fmt, fmtDate, fmtShort, preferredSite, rememberSite } from '../api'
import { useAuth } from '../auth'
import { Field, Modal, Table, useToast } from '../components/ui'

const REASONS = { unpaid_exit: 'Төлбөргүй гаргасан', night_close: 'Шөнийн хаалт', shift_close: 'Ээлж хаалт', manual: 'Гараар', admin_remove: 'Админ хассан', auto_close: 'Авто хаалт', log_exit: 'Логоор гарсан' }
const AGE_OPTS = [[0, 'Бүх хугацаа'], [7, '7+ хоног'], [30, '30+ хоног'], [90, '90+ хоног']]
// Түгээмэл тайлбар — нэг товшилтоор; гараар засаж болно
const QUICK_REASONS = [
  'Системийн алдаа — хуурамч өр (машин бодитоор зогсоогүй)',
  'Эмнэлгийн ажилтан / гэрээт машин — өр хамаарахгүй',
  'Төлбөрийг гараар авсан, системд бүртгэгдээгүй',
  'Удирдлагын шийдвэрээр чөлөөлөв',
]

export default function Debts() {
  const toast = useToast()
  const { user } = useAuth()
  const [sites, setSites] = useState([])
  const [siteId, setSiteId] = useState('')
  const [plate, setPlate] = useState('')
  const [minDays, setMinDays] = useState(0)
  const [tab, setTab] = useState('pending')
  const [data, setData] = useState({ rows: [], total_pending: 0, pending_count: 0 })
  const [logData, setLogData] = useState({ rows: [], total: 0 })
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState(() => new Set())   // comp id-ууд
  const [expanded, setExpanded] = useState(() => new Set())   // задалсан дугаарууд
  const [modal, setModal] = useState(null)                    // {reason, unblacklist}
  const [busy, setBusy] = useState(false)
  const [lastResult, setLastResult] = useState(null)

  useEffect(() => {
    api('/api/admin/sites').then((s) => { setSites(s); setSiteId(preferredSite(s)) }).catch(() => {})
  }, [])

  const params = () => {
    const p = new URLSearchParams({ status: 'PENDING', limit: 1000 })
    if (siteId) p.set('site_id', siteId)
    if (plate.trim()) p.set('plate', plate.trim())
    if (minDays) p.set('min_days', minDays)
    return p
  }
  const load = () => {
    setLoading(true)
    api(`/api/compensations?${params()}`).then((d) => { setData(d); setSelected(new Set()) })
      .catch((e) => toast(e.message, 'error')).finally(() => setLoading(false))
  }
  const loadLog = () => {
    const p = new URLSearchParams({ limit: 500 })
    if (siteId) p.set('site_id', siteId)
    if (plate.trim()) p.set('plate', plate.trim())
    api(`/api/compensations/write-off-log?${p}`).then(setLogData).catch((e) => toast(e.message, 'error'))
  }
  useEffect(() => { if (sites.length) { load(); loadLog() } }, [siteId, minDays, sites.length]) // eslint-disable-line react-hooks/exhaustive-deps
  const search = () => { load(); loadLog() }

  const siteName = sites.find((s) => s.id === siteId)?.name || 'Бүх зогсоол'

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

  const allIds = useMemo(() => data.rows.map((c) => c.id), [data.rows])
  const selComps = data.rows.filter((c) => selected.has(c.id))
  const selTotal = selComps.reduce((s, c) => s + Number(c.amount || 0), 0)
  const selPlates = new Set(selComps.map((c) => c.plate_number)).size

  const toggle = (ids, on) => setSelected((prev) => {
    const n = new Set(prev); ids.forEach((id) => (on ? n.add(id) : n.delete(id))); return n
  })
  const groupState = (g) => {
    const n = g.comps.filter((c) => selected.has(c.id)).length
    return n === 0 ? 'none' : n === g.comps.length ? 'all' : 'some'
  }
  const toggleExpand = (p) => setExpanded((prev) => { const n = new Set(prev); n.has(p) ? n.delete(p) : n.add(p); return n })

  const doWriteOff = async () => {
    if (!modal.reason || modal.reason.trim().length < 3) return toast('Тайлбар заавал (3+ тэмдэгт)', 'error')
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

      {/* Шүүлтүүр */}
      <div className="card grid grid-cols-2 md:grid-cols-5 gap-3 items-end">
        <Field label="Зогсоол">
          <select className="input" value={siteId} onChange={(e) => { setSiteId(e.target.value); rememberSite(e.target.value) }}>
            <option value="">Бүх зогсоол</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        <Field label="Өрийн нас">
          <select className="input" value={minDays} onChange={(e) => setMinDays(Number(e.target.value))}>
            {AGE_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
        <Field label="Дугаар">
          <div className="flex gap-2">
            <input className="input font-mono" placeholder="Хайх…" value={plate}
              onChange={(e) => setPlate(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === 'Enter' && search()} />
            <button className="btn-secondary" onClick={search} aria-label="Хайх"><Search size={15} /></button>
          </div>
        </Field>
        <div className="col-span-2 text-right">
          <div className="text-xs text-slate-500">{siteName} · төлөгдөөгүй авлага</div>
          <div className="font-mono text-2xl font-bold text-red-400">{fmt(data.total_pending)}₮</div>
          <div className="text-xs text-slate-500">{data.pending_count} өр · {groups.length} дугаар (шүүлтээр)</div>
        </div>
      </div>

      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {[['pending', 'Төлөгдөөгүй өр', Eraser], ['log', 'Цэвэрлэлтийн лог', ScrollText]].map(([v, l, Icon]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer flex items-center gap-1.5
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            <Icon size={14} /> {l}{v === 'log' && logData.rows.length ? ` (${logData.rows.length})` : ''}
          </button>
        ))}
      </div>

      {tab === 'pending' && (
        <>
          {data.rows.length >= 1000 && (
            <div className="card py-2 text-xs text-amber-400 flex items-center gap-2">
              <AlertTriangle size={14} /> 1000 мөрийн хязгаарт хүрлээ — зогсоол/нас/дугаараар нарийсгаж дахин ачаална уу.
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

          <Table headers={[
            <input key="all" type="checkbox" aria-label="Бүгдийг сонгох"
              checked={allIds.length > 0 && selected.size === allIds.length}
              ref={(el) => { if (el) el.indeterminate = selected.size > 0 && selected.size < allIds.length }}
              onChange={(e) => toggle(allIds, e.target.checked)} />,
            'Дугаар', 'Зогсоол', 'Өрийн тоо', 'Нийт дүн', 'Хамгийн хуучин', 'Хамгийн сүүлийн', 'Шалтгаан',
          ]} empty={groups.length === 0} maxH="60vh">
            {groups.map((g) => {
              const st = groupState(g)
              const open = expanded.has(g.plate)
              const kinds = [...new Set(g.comps.map((c) => REASONS[c.reason] || c.reason))]
              return [
                <tr key={g.plate} className={st !== 'none' ? 'bg-accent/5' : ''}>
                  <td className="td w-8">
                    <input type="checkbox" checked={st === 'all'} aria-label={`${g.plate} сонгох`}
                      ref={(el) => { if (el) el.indeterminate = st === 'some' }}
                      onChange={(e) => toggle(g.comps.map((c) => c.id), e.target.checked)} />
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
                        onChange={(e) => toggle([c.id], e.target.checked)} />
                    </td>
                    <td className="td text-xs text-slate-500 pl-8">↳ өр</td>
                    <td className="td text-xs">{c.site_name || '—'}</td>
                    <td className="td text-xs text-slate-500">{c.days_old} хоног</td>
                    <td className="td font-mono">{fmt(c.amount)}₮</td>
                    <td className="td font-mono text-xs" colSpan={2}>{fmtDate(c.created_at)}</td>
                    <td className="td text-xs text-slate-400">{REASONS[c.reason] || c.reason} · {c.created_by}</td>
                  </tr>
                )),
              ]
            })}
          </Table>

          {/* Үйлдлийн мөр — сонголт байхад л */}
          <div className={`card sticky bottom-3 flex items-center justify-between gap-3 flex-wrap transition-opacity ${selected.size ? 'opacity-100' : 'opacity-60'}`}>
            <div className="text-sm">
              Сонгосон: <b className="font-mono">{selPlates}</b> дугаар · <b className="font-mono">{selected.size}</b> өр ·{' '}
              <b className="font-mono text-red-400">{fmt(selTotal)}₮</b>
              {selected.size > 0 && <button className="ml-3 text-xs text-slate-400 underline cursor-pointer" onClick={() => setSelected(new Set())}>цэвэрлэх</button>}
            </div>
            <button className="btn-danger" disabled={!selected.size}
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
