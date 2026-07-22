// Шалгах — зогсоолд ОДОО байгаа машинуудын хяналтын жагсаалт (эргүүл/хяналтын дэлгэц).
// Дугаарын эхний тэмдэгтээр live шүүнэ, төлөв/зогсоолоор шүүнэ, real-time шинэчлэгдэнэ.
import { RefreshCw, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api, fmt, fmtDate, fmtDur, wsConnect } from '../api'
import { useAuth } from '../auth'
import { SnapshotButton } from '../components/Snapshot'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

const STATUSES = [
  ['', 'Бүгд (зогсоолд байгаа)'],
  ['OPEN', 'Зогсож байна'],
  ['AWAITING_PAYMENT', 'Төлбөр хүлээж буй'],
  ['PAID', 'Төлсөн (гараагүй)'],
]

export default function Check() {
  const { user } = useAuth()
  const toast = useToast()
  const isAdmin = ['ADMIN', 'SUPER_ADMIN'].includes(user?.role)
  const [sites, setSites] = useState([])
  const [siteId, setSiteId] = useState('')
  const [status, setStatus] = useState('')
  const [plate, setPlate] = useState('')
  const [data, setData] = useState({ total: 0, rows: [] })
  const [sel, setSel] = useState([]) // админ: хасахаар сонгосон session id-ууд
  const [removing, setRemoving] = useState(null) // {ids, createComp, reason}
  const debounceRef = useRef(null)

  const load = () => {
    const params = new URLSearchParams({
      status: status || 'OPEN,AWAITING_PAYMENT,PAID',
      with_fee: '1', limit: 200,
    })
    if (siteId) params.set('site_id', siteId)
    if (plate.trim()) params.set('plate', plate.trim())
    api(`/api/sessions?${params}`).then((d) => {
      setData(d)
      // Жагсаалтаас алга болсон мөрийн сонголтыг цэвэрлэнэ
      setSel((prev) => prev.filter((id) => d.rows.some((r) => r.id === id)))
    }).catch(() => {})
  }

  const doRemove = async (e) => {
    e.preventDefault()
    try {
      const r = await api('/api/sessions/bulk-remove', {
        method: 'POST',
        body: { session_ids: removing.ids, create_compensation: removing.createComp, reason: removing.reason },
      })
      toast(`${r.removed} машин хасагдлаа${r.debt_total ? `, өр ${fmt(r.debt_total)}₮` : ''}${r.skipped ? ` (${r.skipped} алгассан)` : ''}`)
      setRemoving(null); setSel([]); load()
    } catch (err) { toast(err.message, 'error') }
  }

  useEffect(() => { api('/api/admin/sites').then(setSites) }, [])
  useEffect(load, [siteId, status])
  useEffect(() => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(load, 350)
    return () => clearTimeout(debounceRef.current)
  }, [plate])
  useEffect(() => wsConnect('all', load), [siteId, status, plate])

  const unpaidTotal = data.rows.reduce((sum, s) => sum + (s.fee?.total_fee ?? Number(s.total_fee) ?? 0), 0)

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Шалгах</h1>
        <button className="btn-secondary" onClick={load}><RefreshCw size={15} /> Шинэчлэх</button>
      </div>

      <div className={`card grid grid-cols-1 gap-3 ${sites.length > 1 ? 'md:grid-cols-3' : 'md:grid-cols-2'}`}>
        <input className="input font-mono text-lg" placeholder="Дугаараар шүүх… (эхний тоо хангалттай)"
          value={plate} onChange={(e) => setPlate(e.target.value.toUpperCase())} autoFocus
          aria-label="Улсын дугаараар шүүх" />
        {sites.length > 1 && (
          <select className="input" value={siteId} onChange={(e) => setSiteId(e.target.value)} aria-label="Зогсоол">
            <option value="">Бүх зогсоол</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Төлөв">
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>

      {isAdmin && sel.length > 0 && (
        <div className="card py-3 flex items-center gap-3 border-red-500/40">
          <span className="text-sm"><b className="font-mono">{sel.length}</b> машин сонгогдсон</span>
          <button className="btn-secondary text-red-400"
            onClick={() => setRemoving({ ids: sel, createComp: true, reason: '' })}>
            <Trash2 size={15} /> Зогсоолоос хасах
          </button>
          <button className="btn-secondary text-xs" onClick={() => setSel([])}>Цуцлах</button>
        </div>
      )}

      <Table headers={[...(isAdmin ? [
        <input key="all" type="checkbox" className="cursor-pointer" title="Бүгдийг сонгох"
          checked={data.rows.length > 0 && sel.length === data.rows.length}
          onChange={(e) => setSel(e.target.checked ? data.rows.map((r) => r.id) : [])} />,
      ] : []), 'Дугаар', 'Зогсоол', 'Орсон', 'Хугацаа', 'Дүн', 'Өр', 'Гэрээт', 'Төлөв', 'Зураг',
      ...(isAdmin ? [''] : [])]}
        empty={data.rows.length === 0}>
        {data.rows.map((s) => (
          <tr key={s.id} className={s.debt ? 'bg-red-500/10' : 'hover:bg-surface-muted/30'}>
            {isAdmin && (
              <td className="td">
                <input type="checkbox" className="cursor-pointer" checked={sel.includes(s.id)}
                  onChange={(e) => setSel(e.target.checked ? [...sel, s.id] : sel.filter((x) => x !== s.id))} />
              </td>
            )}
            <td className="td font-mono font-bold text-base">{s.plate_number}</td>
            <td className="td">{s.site_name}</td>
            <td className="td font-mono text-xs">{fmtDate(s.entry_time)}</td>
            <td className="td font-mono">{fmtDur(s.fee?.duration_minutes ?? s.duration_minutes)}</td>
            <td className="td font-mono font-semibold">
              {s.fee?.is_free ? <span className="text-cyan-400">Үнэгүй</span> : `${fmt(s.fee?.total_fee ?? s.total_fee)}₮`}
            </td>
            <td className="td">
              {s.debt ? (
                <span className="text-red-400 font-mono font-bold" title={`${s.debt.count} төлөгдөөгүй нэхэмжлэл (бүх зогсоол)`}>
                  {fmt(s.debt.amount)}₮{s.debt.count >= 3 && <span className="ml-1 text-[10px] bg-red-500/20 px-1 rounded">хориг</span>}
                </span>
              ) : <span className="text-slate-600">-</span>}
            </td>
            <td className="td text-xs">{s.is_registered ? <span className="text-accent">Тийм</span> : '-'}</td>
            <td className="td"><Badge value={s.status} /></td>
            <td className="td"><SnapshotButton session={s} /></td>
            {isAdmin && (
              <td className="td text-right">
                <button className="btn-secondary py-1 text-xs text-red-400" title="Зогсоолоос хасах"
                  onClick={() => setRemoving({ ids: [s.id], createComp: true, reason: '' })}>
                  <Trash2 size={13} />
                </button>
              </td>
            )}
          </tr>
        ))}
      </Table>

      {/* Зогсоолоос хасах modal — өр үүсгэх эсэх + шалтгаан */}
      <Modal open={!!removing} onClose={() => setRemoving(null)} title="Зогсоолоос хасах">
        {removing && (
          <form onSubmit={doRemove} className="space-y-3">
            <div className="text-sm">
              <b className="font-mono">{removing.ids.length}</b> машиныг бүртгэлээс хасна
              (хаалт нээгдэхгүй, session хаагдана).
            </div>
            <label className="flex items-start gap-2 text-sm cursor-pointer">
              <input type="checkbox" className="mt-0.5" checked={removing.createComp}
                onChange={(e) => setRemoving({ ...removing, createComp: e.target.checked })} />
              <span>Төлөгдөөгүй дүнгээр <b>өр (нөхөн төлбөр)</b> үүсгэх — дараа ирэхэд нэхэгдэнэ,
                3+ өртэй бол хаалт автоматаар нээгдэхгүй</span>
            </label>
            <div className="text-[11px] text-slate-500">
              Өрийн дүн: гарах хаалтанд уншигдсан машинд тэр үеийн дүнгээр,
              бусад нь одоог хүртэлх дүнгээр бодогдоно.
            </div>
            <Field label="Шалтгаан (заавал биш)">
              <input className="input" value={removing.reason} placeholder="ж: 72 цаг хэтэрсэн"
                onChange={(e) => setRemoving({ ...removing, reason: e.target.value })} />
            </Field>
            <button className="btn-primary w-full justify-center bg-red-600 hover:bg-red-500">
              <Trash2 size={15} /> Хасах
            </button>
          </form>
        )}
      </Modal>

      <div className="card py-3 flex flex-wrap gap-6 text-sm">
        <span>Зогсоолд байгаа: <b className="font-mono">{fmt(data.total)}</b> машин</span>
        <span>Тооцоолсон нийт дүн: <b className="font-mono text-amber-400">{fmt(unpaidTotal)}₮</b></span>
      </div>
    </div>
  )
}
