import { useEffect, useRef, useState } from 'react'
import { api, fmt } from '../../api'
import { Modal } from '../../components/ui'
import { SnapshotImg } from '../../components/Snapshot'
import { exitFacts, reviewReady, reviewDate as fmtDate } from '../../exitReview'

function EventImage({ event }) {
  const [url, setUrl] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true, objectUrl
    setUrl(null); setError('')
    api(event.image_url, { blob: true }).then(blob => {
      if (!active) return
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl)
    }).catch(() => { if (active) setError('Энэ уншилтын эх зураг хадгалагдаагүй. Шинэ кадрыг тусад нь авна.') })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [event.id, event.image_url])
  return url ? <img className="w-full max-h-64 object-contain bg-black rounded-lg" src={url} alt={`Гарах уншилтын эх зураг ${event.raw_plate || 'дугааргүй'}`} />
    : <p className="text-sm text-slate-300 p-4 bg-surface-muted rounded-lg" role="status">{error || 'Зураг уншиж байна…'}</p>
}

export function ExitFacts({ facts }) {
  return <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 text-sm bg-surface-muted rounded-lg p-3">
    {exitFacts(facts).map(([label, value]) => <div key={label}><dt className="text-slate-300">{label}</dt><dd className="font-medium">{value}</dd></div>)}
  </dl>
}

export default function ExitReview({ siteId, onResolved }) {
  const [data, setData] = useState(null), [error, setError] = useState('')
  const [loading, setLoading] = useState(false), [item, setItem] = useState(null)
  const [expanded, setExpanded] = useState(false)
  const generation = useRef(0)
  const load = async () => {
    const ticket = ++generation.current
    setLoading(true); setError('')
    try { const result = await api(`/api/exit-review?site_id=${encodeURIComponent(siteId)}`); if (ticket === generation.current) setData(result) }
    catch (e) { if (ticket === generation.current) setError(e.message) }
    finally { if (ticket === generation.current) setLoading(false) }
  }
  useEffect(() => { setData(null); setItem(null); generation.current++; if (siteId) load(); return () => { generation.current++ } }, [siteId])
  useEffect(() => { if (!siteId) return; const timer = setInterval(load, 15000); return () => clearInterval(timer) }, [siteId])
  if (!siteId) return null
  return <section className="card space-y-3" aria-label="Гарах уншилтыг шийдвэрлэх">
    <div className="flex flex-wrap gap-3 items-center justify-between">
      <button type="button" className="btn-secondary min-h-11" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        Гарах уншилт ба төлсөн хүлээлт {data ? `(${data.events.length}${data.events_more ? '+' : ''} уншилт · ${data.paid_waiting.length}${data.paid_more ? '+' : ''} төлсөн)` : ''}
      </button>
      <button type="button" className="btn-secondary min-h-11" disabled={loading} onClick={load}>{loading ? 'Уншиж байна…' : 'Шинэчлэх'}</button>
    </div>
    {error && <p role="alert" className="text-red-700 dark:text-red-300">{error}</p>}
    {expanded && data && <div className="space-y-4">
      <p className="text-sm text-slate-300">QR-аар зогсоолын аль ч хэсгээс төлж болно. Төлсөн машин гарах камерт шинэ уншилт өгөх хүртэл хүлээлтэд байна. Хугацаа нь тухайн зогсоолын тарифаар үйлчилнэ.</p>
      <div className="grid lg:grid-cols-2 gap-4">
        <div className="space-y-2 max-h-96 overflow-y-auto">
          <h2 className="font-semibold">Уншилт ирсэн ч татгалзсан</h2>
          {!data.events.length && <p>Татгалзсан уншилт алга.</p>}
          {data.events.map(event => <button type="button" key={event.id} className="btn-secondary w-full text-left min-h-11 block" onClick={() => setItem({ event })}>
            <span className="font-mono font-semibold">{event.raw_plate || 'Дугааргүй'}</span> · {event.camera || 'Камер тодорхойгүй'} / эгнээ {event.lane ?? '—'}
            <span className="block text-sm text-slate-300">{fmtDate(event.created_at)} · итгэлцэл {event.confidence ?? '—'} · {event.reason}</span>
          </button>)}
        </div>
        <div className="space-y-2 max-h-96 overflow-y-auto">
          <h2 className="font-semibold">Төлсөн — гарахыг хүлээж байна</h2>
          {!data.paid_waiting.length && <p>Төлсөн хүлээлт алга.</p>}
          {data.paid_waiting.map(s => <button type="button" key={s.id} className="btn-secondary w-full text-left min-h-11 block" onClick={() => setItem({ sessionId: s.id })}>
            <span className="font-mono font-semibold">{s.plate}</span><span className="block text-sm text-slate-300">Төлсөн: {fmtDate(s.paid_at)}</span>
            <span className={`block text-sm ${s.grace_expired ? 'text-amber-700 dark:text-amber-300' : 'text-slate-300'}`}>{s.grace_expired ? 'Гарах хугацаа хэтэрсэн — үлдэгдлийг дахин шалгана' : `Гарах хугацаа: ${fmtDate(s.exit_deadline)}`}</span>
            {s.attention_reason && <span className="block text-amber-700 dark:text-amber-300">Шалгах шаардлагатай: гарах камер/хаалтын холбоос дутуу.</span>}
          </button>)}
        </div>
      </div>
      {!!data.command_attention?.length && <div className="space-y-2">
        <h2 className="font-semibold">Нээх командын үр дүнг шалгах {data.commands_more ? '(эхний мөрүүд)' : ''}</h2>
        <p>Дахин төлбөр авахгүй. Төхөөрөмж, камерын одоогийн байдлыг шалгаж, шаардлагатай бол хаалтын удирдлагаар шалтгаантай шийдвэрлэнэ.</p>
        {data.command_attention.map(c => <button type="button" className="btn-secondary min-h-11 w-full text-left" key={c.id} onClick={() => setItem({sessionId:c.session_id})}>
          {c.plate} · {c.camera} / {c.lane} · {c.status === 'FAILED' ? 'Команд амжилтгүй' : c.status === 'UNKNOWN' ? 'Үр дүн тодорхойгүй' : 'Команд хүлээгдэж байна'} · {fmtDate(c.created_at)}
        </button>)}
      </div>}
      <p className="text-sm text-slate-300">Зөвхөн 0₮ эсвэл QR төлсөн гэдгээр хаалт нээхгүй. Орох зураг, шинэ гарах кадрыг тулгаж шийдвэрлэнэ.</p>
    </div>}
    {item && <ReviewDialog key={`${siteId}:${item.event?.id || item.sessionId}`} item={item} siteId={siteId} cameras={data?.cameras || []}
      onClose={() => setItem(null)} onResolved={sid => { load(); onResolved?.(sid) }} />}
  </section>
}

function ReviewDialog({ item, siteId, cameras, onClose, onResolved }) {
  const [stay, setStay] = useState(null), [facts, setFacts] = useState(null), [query, setQuery] = useState('')
  const [results, setResults] = useState([]), [cameraId, setCameraId] = useState(item.event?.camera_id || '')
  const [proof, setProof] = useState(null), [expiresAt, setExpiresAt] = useState(0), [now, setNow] = useState(Date.now())
  const [confirmed, setConfirmed] = useState(false), [reason, setReason] = useState(''), [busy, setBusy] = useState(false)
  const [error, setError] = useState(''), [message, setMessage] = useState('')
  const [ocr, setOcr] = useState(null)
  const [evidence, setEvidence] = useState(null)
  const generation = useRef(0), alive = useRef(true)
  useEffect(() => { alive.current = true; const timer = setInterval(() => setNow(Date.now()), 1000); return () => { alive.current = false; generation.current++; clearInterval(timer) } }, [])
  const pick = async sid => {
    const ticket = ++generation.current
    setBusy(true); setError(''); setProof(null); setConfirmed(false); setFacts(null); setStay(null); setMessage('')
    try {
      const [s, f] = await Promise.all([api(`/api/sessions/${sid}`), api(`/api/exit-review/sessions/${sid}`)])
      if (alive.current && ticket === generation.current) { setStay(s); setFacts(f); setResults([]) }
    } catch (e) { if (alive.current && ticket === generation.current) setError(e.message) }
    finally { if (alive.current && ticket === generation.current) setBusy(false) }
  }
  useEffect(() => { if (item.sessionId) pick(item.sessionId) }, [item.sessionId])
  useEffect(() => {
    let active = true
    if (item.event) api(`/api/exit-review/events/${item.event.id}/evidence`).then(value => { if (active) setEvidence(value) }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [item.event?.id])
  const search = async () => {
    const ticket = ++generation.current; setBusy(true); setError('')
    try { const r = await api(`/api/sessions/check?site_id=${encodeURIComponent(siteId)}&plate=${encodeURIComponent(query)}`); if (alive.current && ticket === generation.current) setResults(r.filter(s => ['OPEN','AWAITING_PAYMENT','PAID'].includes(s.status))) }
    catch (e) { if (alive.current && ticket === generation.current) setError(e.message) }
    finally { if (alive.current && ticket === generation.current) setBusy(false) }
  }
  const capture = async () => {
    setBusy(true); setError(''); setProof(null); setConfirmed(false)
    try {
      const p = await api(`/api/exit-review/sessions/${stay.id}/capture`, { method: 'POST', body: { camera_id: cameraId, event_id: item.event?.camera_id === cameraId ? item.event.id : null } })
      if (alive.current) { const captured = Date.now(); setNow(captured); setProof(p); setExpiresAt(captured+p.expires_seconds*1000) }
    } catch (e) { if (alive.current) setError(e.message) }
    finally { if (alive.current) setBusy(false) }
  }
  const resolve = async () => {
    setBusy(true); setError('')
    try {
      const r = await api(`/api/exit-review/sessions/${stay.id}/resolve`, { method: 'POST', body: { proof_id: proof.proof_id, confirmed_same_vehicle: confirmed, reason: reason.trim() } })
      if (alive.current) { setFacts(r.facts); setProof(null); setConfirmed(false); setMessage(r.facts.acknowledged ? 'Төхөөрөмж команд зөвшөөрсөн. Бодит гаралтыг оператор хянана.' : 'Командын үр дүнг шалгана уу. Бодитоор гарсан гэж батлаагүй.') }
      onResolved(stay.id)
    } catch (e) { if (alive.current) setError(e.message) }
    finally { if (alive.current) setBusy(false) }
  }
  const retryOcr = async () => {
    setBusy(true); setError(''); setOcr(null)
    try {
      const r = await api(`/api/exit-review/events/${item.event.id}/ocr`, { method: 'POST' })
      if (alive.current) setOcr(r)
    } catch (e) { if (alive.current) setError(e.message) }
    finally { if (alive.current) setBusy(false) }
  }
  const ready = stay && !facts?.closed && reviewReady({proof,expiresAt,now,confirmed,reason,busy})
  return <Modal open title="Гарах баримтыг тулгах" wide focusTrap onClose={() => { if (!busy) onClose() }}>
    <div className="space-y-4 text-sm">
      {item.event && <><p><b>Raw дугаар: {item.event.raw_plate || 'байхгүй'}</b> · хадгалсан: {item.event.plate}<br />{item.event.camera} / эгнээ {item.event.lane ?? '—'} · {fmtDate(item.event.created_at)}<br />Итгэлцэл: {item.event.confidence ?? '—'} · Татгалзсан шалтгаан: {item.event.reason}</p><EventImage event={item.event} />
        {evidence && <div className="space-y-1 bg-surface-muted p-3 rounded-lg">
          <p>Дугаарын итгэлцэл: {evidence.plate_confidence ?? 'мэдээлэлгүй'} · Машины итгэлцэл: {evidence.vehicle_confidence ?? 'мэдээлэлгүй'}</p>
          <p>Машин: {evidence.vehicle_color || 'мэдээлэлгүй'} / {evidence.vehicle_type || 'мэдээлэлгүй'} · Ялгаатай цаг/кадрын ID: {evidence.distinct_observation_ids} (тусдаа шинэ кадр гэдгийг дангаар батлахгүй)</p>
          <p>Камерын цаг: {evidence.camera_time || 'мэдээлэлгүй'} · Кадр: {evidence.frame_id || 'мэдээлэлгүй'}</p>
          {evidence.text_conflict && <p className="text-amber-700 dark:text-amber-300">Камерын дугаарын талбарууд хоорондоо зөрсөн.</p>}
          <p>Өнгө, төрөл, давтан уншилт нь дангаараа гарах эрх батлахгүй.</p>
        </div>}
        {item.event.candidates.map(s => <button type="button" key={s.id} disabled={busy} className="btn-secondary min-h-11" onClick={() => pick(s.id)}>{s.plate} — яг таарах нээлттэй зогсолтыг үзэх</button>)}</>}
      {!item.sessionId && <div><label className="label" htmlFor="review-plate">Машины зөв дугаараар зогсолт хайх</label><div className="flex gap-2"><input id="review-plate" className="input" value={query} onChange={e => setQuery(e.target.value.toUpperCase())} /><button type="button" className="btn-secondary min-h-11" disabled={busy || query.trim().length<2} onClick={search}>Хайх</button></div>{results.map(s => <button type="button" className="btn-secondary m-1 min-h-11" disabled={busy} key={s.id} onClick={() => pick(s.id)}>{s.plate_number} · {fmtDate(s.entry_time)}</button>)}</div>}
      {item.event && <div className="space-y-2"><button type="button" className="btn-secondary min-h-11" disabled={busy} onClick={retryOcr}>Эх кадр / тайрдас дээр дахин таних (туршилт)</button>
        <p className="text-slate-300">Танилтын үр дүнгээр машин сонгох, төлбөр холбох, хаалт нээх үйлдэл автоматаар хийхгүй.</p>
        {ocr && <div role="status"><p>{ocr.source === 'event_plate_crop' ? 'Эх event-ийн дугаарын тайрдас' : 'Эх event-ийн кадр'} · {ocr.engine}</p>{ocr.suggestions.length ? ocr.suggestions.map((s,i) => <div key={i}>
          <p className="font-mono">{s.text} · {Math.round(s.confidence)} · {s.valid_format ? 'Формат таарсан' : 'Формат зөрсөн'}</p>
          {(s.exact_candidates || []).map(c => <button type="button" className="btn-secondary min-h-11" key={c.id} disabled={busy} onClick={() => pick(c.id)}>{c.plate} — орох зургийг тулгах</button>)}
        </div>) : <p>Дугаар танигдсангүй. Оператор зурагт тулгаж сонгоно.</p>}<p>Нэг зургийн хоёр боловсруулалт нь хоёр өөр кадрын баталгаа болохгүй.</p></div>}
      </div>}
      {stay && <>
        <p className="text-lg font-semibold font-mono">{stay.plate_number}</p><ExitFacts facts={facts} />
        <p>Орох үеийн машин: {facts?.vehicle_color || 'мэдээлэлгүй'} / {facts?.vehicle_type || 'мэдээлэлгүй'}</p>
        <p>Одоогийн үлдэгдэл: <b>{fmt(facts?.amount_due)}₮</b>. Сервер үйлдэл хийх үед дахин шалгана.</p>
        <label className="label" htmlFor="review-camera">Машин одоо байгаа гарах камер / эгнээ</label>
        <select id="review-camera" className="input min-h-11" value={cameraId} disabled={busy} onChange={e => {setCameraId(e.target.value); setProof(null); setConfirmed(false)}}><option value="">Гарцыг сонгоно уу</option>{cameras.map(c => <option key={c.id} value={c.id}>{c.name} · эгнээ {c.lane ?? '—'}</option>)}</select>
        <button type="button" className="btn-secondary min-h-11" disabled={busy || !cameraId || facts?.closed} onClick={capture}>Гарах камерын шинэ кадр авах</button>
        <div className="grid sm:grid-cols-2 gap-3"><SnapshotImg sessionId={stay.id} kind="entry" label="Орох зураг" />{proof && <SnapshotImg key={proof.proof_id} sessionId={stay.id} kind="verify" label={`Одоогийн гарах кадр · ${fmtDate(proof.taken_at)}`} />}</div>
        {proof && <p role="status">{now < expiresAt ? `Баталгаажуулах хугацаа: ${Math.ceil((expiresAt-now)/1000)} секунд` : 'Кадр хуучирсан. Шинэ зураг авна уу.'}</p>}
        <label className="flex items-start gap-3 min-h-11"><input type="checkbox" className="mt-1" checked={confirmed} disabled={busy || !proof || now>=expiresAt} onChange={e => setConfirmed(e.target.checked)} /><span>Орох зураг ба сонгосон гарцын шинэ кадрт ижил машин байгааг шалгасан.</span></label>
        <label className="label" htmlFor="review-reason">Шийдвэрлэсэн шалтгаан</label><input id="review-reason" className="input min-h-11" maxLength={200} value={reason} onChange={e => setReason(e.target.value)} aria-describedby="review-reason-hint" /><p id="review-reason-hint" className="text-slate-300">Дугаарын зөрүү болон тулгасан баримтаа бичнэ үү. Эргэлзээтэй бол гаргахгүй.</p>
        <button type="button" className="btn-primary min-h-11" disabled={!ready} onClick={resolve}>{busy ? 'Шалгаж байна…' : 'Баримтыг баталгаажуулж нээх команд өгөх'}</button>
      </>}
      {error && <p role="alert" className="text-red-700 dark:text-red-300">{error}</p>}{message && <p role="status">{message}</p>}
    </div>
  </Modal>
}
