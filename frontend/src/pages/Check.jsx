// Шалгах — зогсоолд ОДОО байгаа машинуудын хяналтын жагсаалт (эргүүл/хяналтын дэлгэц).
// Дугаарын эхний тэмдэгтээр live шүүнэ, төлөв/зогсоолоор шүүнэ, real-time шинэчлэгдэнэ.
// Админ "Аудит горим"-оор сэжигтэй (гарсан ч хаагдаагүй / буруу дугаар / удсан) бүртгэлийг
// ялган нэг товчоор цэвэрлэж, зогсоолын тоог бодит байдалтай тулгана.
import { CarFront, Pencil, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
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
  // Nested зогсоолтой газарт: тоолуур нь зогссон (дотор байгаа) машинууд
  ['INNER', 'Дотор зогсоолд'],
]

// Аудитын сэжигтэй тэмдгүүд — оператор цэвэрлэх ёстой мөрийг шуурхай ялгана
function FlagBadges({ audit }) {
  if (!audit?.flags?.length) return null
  return (
    <div className="flex flex-wrap gap-1 mt-0.5">
      {audit.exit_read && (
        <span className="text-[10px] bg-amber-500/20 text-amber-300 px-1.5 py-0.5 rounded"
          title={`Орсны дараа гарах камерт уншигдсан (${fmtDate(audit.exit_read_at)}) — гарсан байх магадлалтай`}>
          гарсан?
        </span>
      )}
      {audit.invalid_plate && (
        <span className="text-[10px] bg-red-500/20 text-red-300 px-1.5 py-0.5 rounded"
          title="Дугаар стандарт формат биш (4 орон + 3 кирилл үсэг биш) — камерын буруу уншилт байж магадгүй">
          буруу дугаар
        </span>
      )}
      {audit.stale && (
        <span className="text-[10px] bg-orange-500/20 text-orange-300 px-1.5 py-0.5 rounded"
          title={`${audit.hours_parked} цаг зогссон — хэт удсан`}>
          удсан {Math.floor(audit.hours_parked)}ц
        </span>
      )}
      {audit.cam_exit_read && (
        <span className="text-[10px] bg-violet-500/20 text-violet-300 px-1.5 py-0.5 rounded"
          title={`Камерын ДОТООД логоор: орсны дараа гарах камераар өнгөрсөн (${fmtDate(audit.cam_exit_at)}) — сервер event алдсан байсан ч машин гарсан`}>
          камерт гарсан
        </span>
      )}
      {audit.ocr_similar && (
        <span className="text-[10px] bg-sky-500/20 text-sky-300 px-1.5 py-0.5 rounded"
          title={`Камерын логт энэ дугаар яг байхгүй, харин 1 тэмдэгтийн зөрүүтэй ${audit.cam_similar.map((c) => c.plate).join(', ')} бий — OCR буруу уншилт байж магадгүй. Харандаагаар засна уу.`}>
          OCR? {audit.cam_similar[0]?.plate}
        </span>
      )}
    </div>
  )
}

export default function Check() {
  const { user } = useAuth()
  const toast = useToast()
  const isAdmin = ['ADMIN', 'SUPER_ADMIN'].includes(user?.role)
  const [sites, setSites] = useState([])
  const [siteId, setSiteId] = useState('')
  const [status, setStatus] = useState('')
  const [plate, setPlate] = useState('')
  const [audit, setAudit] = useState(false)        // админ: аудит горим
  const [suspectOnly, setSuspectOnly] = useState(false)
  const [data, setData] = useState({ total: 0, rows: [], suspect: 0 })
  const [sel, setSel] = useState([]) // админ: хасахаар сонгосон session id-ууд
  const [removing, setRemoving] = useState(null) // {ids, createComp, reason}
  const [backfill, setBackfill] = useState(null) // {rows, debt} — камерын логоос нөхөж бүртгэх
  const debounceRef = useRef(null)

  const load = () => {
    if (audit) {
      const p = new URLSearchParams()
      // Зогсоол сонгосон үед камерын дотоод логтой автоматаар тулгана
      if (siteId) { p.set('site_id', siteId); p.set('camera', '1') }
      api(`/api/sessions/audit?${p}`).then((d) => {
        setData({ total: d.total, rows: d.rows, suspect: d.suspect, camera: d.camera })
        setSel((prev) => prev.filter((id) => d.rows.some((r) => r.id === id)))
      }).catch((e) => toast(e.message, 'error'))
      return
    }
    const params = new URLSearchParams({
      status: status === 'INNER' ? 'OPEN,AWAITING_PAYMENT,PAID' : (status || 'OPEN,AWAITING_PAYMENT,PAID'),
      with_fee: '1', limit: 200,
    })
    if (status === 'INNER') params.set('inner', '1')
    if (siteId) params.set('site_id', siteId)
    if (plate.trim()) params.set('plate', plate.trim())
    api(`/api/sessions?${params}`).then((d) => {
      setData({ ...d, suspect: 0 })
      setSel((prev) => prev.filter((id) => d.rows.some((r) => r.id === id)))
    }).catch(() => {})
  }

  // Камер буруу уншсан дугаарыг гараар засах (ж: 1101ЭН → 7707ХЭН).
  // Давхардал бол (жинхэнэ дугаартай нь аль хэдийн бүртгэлтэй) засахын оронд
  // буруугий нь "өр үүсгэхгүй" чагтгүйгээр хасна — endpoint давхардлыг өөрөө хориглоно.
  const editPlate = async (s) => {
    const entered = window.prompt(
      `${s.plate_number} дугаарыг засах — зөв дугаарыг оруулна уу:`, s.plate_number)
    if (!entered || entered.trim().toUpperCase() === s.plate_number) return
    const newPlate = entered.trim().toUpperCase()
    try {
      await api(`/api/sessions/${s.id}/plate`, { method: 'PUT', body: { plate_number: newPlate } })
      toast(`${s.plate_number} → ${newPlate} болж засагдлаа`)
      load()
    } catch (err) {
      if (/формат буруу/.test(err.message) &&
          window.confirm(`${err.message}\n\nТусгай/дипломат дугаар мөн бол ЗАСАХ уу?`)) {
        try {
          await api(`/api/sessions/${s.id}/plate`, { method: 'PUT', body: { plate_number: newPlate, force: true } })
          toast(`${s.plate_number} → ${newPlate} болж засагдлаа`)
          load()
        } catch (e2) { toast(e2.message, 'error') }
      } else toast(err.message, 'error')
    }
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

  const doBackfill = async (e) => {
    e.preventDefault()
    try {
      const r = await api('/api/sessions/register-from-camera', {
        method: 'POST',
        body: {
          site_id: siteId,
          create_debt: backfill.debt,
          cars: backfill.rows.map((u) => ({ plate: u.plate, at: u.at, exit_at: u.exit_at })),
        },
      })
      const why = Object.entries(r.skip_reasons || {})
        .map(([k, v]) => `${v} ${k}`).join(', ')
      toast(`${r.created} машин бүртгэгдлээ${r.debt_total ? `, өр ${fmt(r.debt_total)}₮` : ''}`
        + `${r.skipped ? ` · алгассан: ${why}` : ''}`)
      setBackfill(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  useEffect(() => { api('/api/admin/sites').then(setSites) }, [])
  useEffect(load, [siteId, status, audit])
  useEffect(() => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(load, 350)
    return () => clearTimeout(debounceRef.current)
  }, [plate])
  useEffect(() => wsConnect('all', load), [siteId, status, plate, audit])

  // Дэлгэцэнд харуулах мөрүүд: аудит горимд "зөвхөн сэжигтэй" + дугаарын шүүлт client талд
  const rows = data.rows.filter((r) => {
    if (audit && suspectOnly && !r.audit?.suspect) return false
    if (plate.trim() && !r.plate_number?.startsWith(plate.trim())) return false
    return true
  })
  const unpaidTotal = rows.reduce((sum, s) => sum + (s.fee?.total_fee ?? Number(s.total_fee) ?? 0), 0)
  // Буруу уншсан дугаар БӨГӨӨД камерын логоос ижил төстэй дугаар олдоогүй —
  // ийм бүртгэл жинхэнэ машинтай хэзээ ч тохирохгүй тул өргүйгээр цэвэрлэнэ
  const junkRows = rows.filter((r) => r.audit?.invalid_plate && !r.audit?.ocr_similar)

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Шалгах{audit && <span className="text-accent text-lg font-semibold ml-2">· Аудит</span>}</h1>
        <div className="flex items-center gap-2">
          {isAdmin && (
            <button className={`btn-secondary ${audit ? 'text-accent border-accent/50' : ''}`}
              onClick={() => { setAudit((a) => !a); setSel([]); setSuspectOnly(false) }}
              title="Гарсан ч хаагдаагүй / буруу дугаар / удсан бүртгэлийг ялгаж цэвэрлэх">
              <ShieldCheck size={15} /> {audit ? 'Аудит хаах' : 'Аудит горим'}
            </button>
          )}
          <button className="btn-secondary" onClick={load}><RefreshCw size={15} /> Шинэчлэх</button>
        </div>
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
        {audit ? (
          <label className="input flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={suspectOnly} onChange={(e) => setSuspectOnly(e.target.checked)} />
            <span className="text-sm">Зөвхөн сэжигтэй ({data.suspect})</span>
          </label>
        ) : (
          <select className="input" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Төлөв">
            {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        )}
      </div>

      {audit && (
        <div className="card py-3 text-sm text-slate-400 border-accent/30">
          Аудит: зогсоолд <b className="font-mono text-slate-200">{data.total}</b> бүртгэл байгаагаас
          <b className="font-mono text-amber-300"> {data.suspect}</b> нь сэжигтэй.
          «гарсан?» = гарах камерт уншигдсан, «буруу дугаар» = формат буруу (junk),
          «удсан» = хэт удсан, «камерт гарсан»/«OCR?» = камерын дотоод логтой тулгасан.
          Сонгоод <b>Зогсоолоос хас</b>-аар цэвэрлэнэ.
          {!siteId && (
            <div className="mt-1 text-xs text-sky-300">
              Камерын дотоод логтой тулгахын тулд дээрээс <b>зогсоол сонгоно уу</b>.
            </div>
          )}
        </div>
      )}

      {/* Камерын дотоод логтой тулгалтын дүн (зогсоол сонгосон үед автоматаар) */}
      {audit && data.camera && (
        <div className="card py-3 text-sm space-y-2">
          <div className="flex flex-wrap gap-4 items-center text-slate-400">
            <b className="text-slate-200">Камерын лог ({data.camera.window_hours}ц):</b>
            {data.camera.cameras.map((c) => (
              <span key={c.ip} className={c.error ? 'text-red-400' : ''}
                title={c.error || `${c.events} event`}>
                {c.name} — {c.error ? 'холбогдсонгүй' : `${c.events} event`}
              </span>
            ))}
            {data.camera.error && <span className="text-red-400">{data.camera.error}</span>}
          </div>
          {data.camera.unmatched_total > 0 && (
            <div className="space-y-2">
              <div>
                <span className="text-amber-300">
                  Камераар орсон ч СИСТЕМД БҮРТГЭЛГҮЙ {data.camera.unmatched_total} машин
                </span>
                <span className="text-slate-500 text-xs"> (сервер унтарсан/event алдсан үеийнх):</span>
                {data.camera.unmatched_exited > 0 && (
                  <span className="text-xs text-slate-400 ml-2">
                    үүнээс <b className="text-slate-200">{data.camera.unmatched_exited}</b> нь
                    гарах камерт ч уншигдсан (төлбөр нь бодогдоно)
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {data.camera.unmatched.map((u, i) => (
                  <span key={i}
                    className={`font-mono text-xs px-1.5 py-0.5 rounded ${u.exit_at
                      ? 'bg-amber-500/15 text-amber-200' : 'bg-surface-muted'}`}
                    title={u.exit_at
                      ? `${u.camera} · орсон ${fmtDate(u.at)} → гарсан ${fmtDate(u.exit_at)} (${u.hours}ц)`
                      : `${u.camera} · орсон ${fmtDate(u.at)} — гарах камерт уншигдаагүй, зогсоолд байж магадгүй`}>
                    {u.plate}
                    <span className="text-slate-400 ml-1">
                      {u.exit_at ? `${u.hours}ц` : 'дотор?'}
                    </span>
                  </span>
                ))}
                {data.camera.unmatched_total > data.camera.unmatched.length && (
                  <span className="text-xs text-slate-500">…+{data.camera.unmatched_total - data.camera.unmatched.length}</span>
                )}
              </div>
              {isAdmin && (
                <button className="btn-secondary py-1 text-xs text-amber-300 border-amber-500/40"
                  onClick={() => setBackfill({ rows: data.camera.unmatched, debt: true })}>
                  <CarFront size={14} /> Эдгээрийг нөхөж бүртгэх
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Буруу уншсан (junk) дугаар — ижил төстэй дугаар ч олдоогүй бол энэ нь
          машин биш, камерын хог уншилт. Өр үүсгэх нь худал өр болох тул
          ӨРГҮЙГЭЭР нэг товчоор цэвэрлэнэ. */}
      {audit && isAdmin && junkRows.length > 0 && (
        <div className="card py-3 flex flex-wrap items-center gap-3 border-red-500/30">
          <span className="text-sm">
            <b className="font-mono text-red-300">{junkRows.length}</b> бүртгэл буруу
            уншсан дугаартай <span className="text-slate-500">(ижил төстэй дугаар ч олдоогүй)</span>
          </span>
          <button className="btn-secondary text-red-400 py-1 text-xs"
            onClick={() => setRemoving({
              ids: junkRows.map((r) => r.id), createComp: false,
              reason: 'Аудит: буруу уншсан дугаар (өргүй)',
            })}>
            <Trash2 size={14} /> Өргүйгээр цэвэрлэх
          </button>
          <span className="text-[11px] text-slate-500">
            Тооцоолсон {fmt(junkRows.reduce((a, r) => a + (r.fee?.total_fee ?? Number(r.total_fee) ?? 0), 0))}₮
            нь хиймэл — өр болгохгүй.
          </span>
        </div>
      )}

      {isAdmin && sel.length > 0 && (
        <div className="card py-3 flex items-center gap-3 border-red-500/40">
          <span className="text-sm"><b className="font-mono">{sel.length}</b> машин сонгогдсон</span>
          <button className="btn-secondary text-red-400"
            onClick={() => setRemoving({ ids: sel, createComp: false, reason: audit ? 'Аудит цэвэрлэгээ' : '' })}>
            <Trash2 size={15} /> Зогсоолоос хасах
          </button>
          <button className="btn-secondary text-xs" onClick={() => setSel([])}>Цуцлах</button>
        </div>
      )}

      <Table headers={[...(isAdmin ? [
        <input key="all" type="checkbox" className="cursor-pointer" title="Бүгдийг сонгох"
          checked={rows.length > 0 && rows.every((r) => sel.includes(r.id))}
          onChange={(e) => setSel(e.target.checked ? rows.map((r) => r.id) : [])} />,
      ] : []), 'Дугаар', 'Зогсоол', 'Орсон', 'Хугацаа', 'Дүн', 'Өр', 'Гэрээт', 'Төлөв', 'Зураг',
      ...(isAdmin ? [''] : [])]}
        empty={rows.length === 0}>
        {rows.map((s) => (
          <tr key={s.id} className={s.debt ? 'bg-red-500/10' : (s.audit?.suspect ? 'bg-amber-500/5' : 'hover:bg-surface-muted/30')}>
            {isAdmin && (
              <td className="td">
                <input type="checkbox" className="cursor-pointer" checked={sel.includes(s.id)}
                  onChange={(e) => setSel(e.target.checked ? [...sel, s.id] : sel.filter((x) => x !== s.id))} />
              </td>
            )}
            <td className="td font-mono font-bold text-base">
              {s.plate_number}
              {(s.vehicle_color || s.vehicle_type) && (
                <div className="text-[10px] text-slate-400 font-sans font-normal">
                  {[s.vehicle_color, s.vehicle_type].filter(Boolean).join(' · ')}
                </div>
              )}
              <FlagBadges audit={s.audit} />
            </td>
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
            <td className="td">
              <Badge value={s.status} />
              {s.paused_since && (
                <span className="ml-1 text-[10px] bg-sky-500/20 text-sky-300 px-1.5 py-0.5 rounded"
                  title={`Дотор зогсоолд орсон ${fmtDate(s.paused_since)} — тоолуур зогссон (нийт ${s.paused_minutes || 0} мин хасагдана)`}>
                  дотор
                </span>
              )}
            </td>
            <td className="td"><SnapshotButton session={s} /></td>
            {isAdmin && (
              <td className="td text-right whitespace-nowrap">
                <button className="btn-secondary py-1 text-xs mr-1" title="Дугаар засах (камер буруу уншсан)"
                  onClick={() => editPlate(s)}>
                  <Pencil size={13} />
                </button>
                <button className="btn-secondary py-1 text-xs text-red-400" title="Зогсоолоос хасах"
                  onClick={() => setRemoving({ ids: [s.id], createComp: false, reason: '' })}>
                  <Trash2 size={13} />
                </button>
              </td>
            )}
          </tr>
        ))}
      </Table>

      {/* Камерын логоос нөхөж бүртгэх — гарсан нь мэдэгдэж байгааг хааж өр үүсгэнэ */}
      <Modal open={!!backfill} onClose={() => setBackfill(null)} title="Камерын логоос нөхөж бүртгэх">
        {backfill && (() => {
          const exited = backfill.rows.filter((r) => r.exit_at)
          const inside = backfill.rows.filter((r) => !r.exit_at)
          return (
            <form onSubmit={doBackfill} className="space-y-3">
              <div className="text-sm">
                Камер уншсан ч системд бүртгэгдээгүй <b className="font-mono">{backfill.rows.length}</b> машиныг
                камерын цагаар нөхөж бүртгэнэ.
              </div>
              <div className="rounded-lg bg-surface-muted/50 p-3 text-xs space-y-1.5">
                <div>
                  <b className="text-amber-300">{exited.length}</b> нь гарах камерт ч уншигдсан
                  — орсон/гарсан цагаар нь <b>хаагдаж, төлбөр бодогдоно</b>
                </div>
                <div>
                  <b className="text-slate-300">{inside.length}</b> нь гарах уншилтгүй
                  — <b>зогсоолд байгаа</b> гэж нээлттэй бүртгэгдэнэ
                </div>
              </div>
              <label className="flex items-start gap-2 text-sm cursor-pointer">
                <input type="checkbox" className="mt-0.5" checked={backfill.debt}
                  onChange={(e) => setBackfill({ ...backfill, debt: e.target.checked })} />
                <span>Гарсан машины төлбөрөөр <b>өр (нөхөн төлбөр)</b> үүсгэх
                  <span className="block text-slate-500">
                    Дараа ирэхэд нь нэхэгдэнэ. Чагтыг авбал зүгээр л хаагдана.
                  </span>
                </span>
              </label>
              <div className="text-[11px] text-slate-500">
                Тухайн цагийн ±1 цагийн дотор аль хэдийн бүртгэл байвал давхардуулахгүй алгасна.
              </div>
              <button className="btn-primary w-full justify-center">
                <CarFront size={15} /> {backfill.rows.length} машиныг бүртгэх
              </button>
            </form>
          )
        })()}
      </Modal>

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
              бусад нь одоог хүртэлх дүнгээр бодогдоно. Junk/буруу уншсан дугаар бол
              өр үүсгэхгүйгээр (checkbox-г болиулж) хасаж болно.
            </div>
            <Field label="Шалтгаан (заавал биш)">
              <input className="input" value={removing.reason} placeholder="ж: аудит — гарсан ч хаагдаагүй"
                onChange={(e) => setRemoving({ ...removing, reason: e.target.value })} />
            </Field>
            <button className="btn-primary w-full justify-center bg-red-600 hover:bg-red-500">
              <Trash2 size={15} /> Хасах
            </button>
          </form>
        )}
      </Modal>

      <div className="card py-3 flex flex-wrap gap-6 text-sm">
        <span>Зогсоолд байгаа: <b className="font-mono">{fmt(audit ? data.total : data.total)}</b> машин</span>
        {audit && <span>Сэжигтэй: <b className="font-mono text-amber-400">{fmt(data.suspect)}</b></span>}
        <span>Тооцоолсон нийт дүн: <b className="font-mono text-amber-400">{fmt(unpaidTotal)}₮</b></span>
      </div>
    </div>
  )
}
