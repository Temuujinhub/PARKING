// Касс — операторын гол дэлгэц: гарах машинууд real-time, төлбөр авах, хаалт нээх, ээлж
import { Banknote, CarFront, CreditCard, DoorOpen, QrCode, RefreshCw, Search } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, fmt, fmtDate, fmtDur, wsConnect } from '../api'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

export default function Cashier() {
  const toast = useToast()
  const [sites, setSites] = useState([])
  const [siteId, setSiteId] = useState('')
  const [exits, setExits] = useState([])
  const [shift, setShift] = useState(null)
  const [selected, setSelected] = useState(null)
  const [searchPlate, setSearchPlate] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const [discounts, setDiscounts] = useState([])
  const [barriers, setBarriers] = useState([])
  const [busy, setBusy] = useState(false)
  const [qpayInfo, setQpayInfo] = useState(null)
  const [manualEntry, setManualEntry] = useState(null) // {plate_number, entry_time}

  const loadExits = useCallback((sid) => {
    if (!sid) return
    api(`/api/sessions/recent-exits?site_id=${sid}`).then(setExits).catch(() => {})
  }, [])
  const loadShift = () => api('/api/cashier/shift/current').then(setShift).catch(() => {})

  useEffect(() => {
    api('/api/admin/sites').then((s) => {
      setSites(s)
      if (s.length) setSiteId(s[0].id)
    })
    api('/api/admin/discounts').then((d) => setDiscounts(d.filter((x) => x.is_active))).catch(() => {})
    loadShift()
  }, [])

  useEffect(() => {
    if (!siteId) return
    loadExits(siteId)
    api(`/api/admin/devices?site_id=${siteId}`).then((d) =>
      setBarriers(d.filter((x) => x.device_type === 'barrier' && x.status === 'active')))
    const close = wsConnect(siteId, () => loadExits(siteId))
    return close
  }, [siteId, loadExits])

  const search = async () => {
    if (!searchPlate.trim()) return
    try {
      setSearchResults(await api(`/api/sessions/check?plate=${encodeURIComponent(searchPlate)}&site_id=${siteId}`))
    } catch (e) { toast(e.message, 'error') }
  }

  const pay = async (method) => {
    if (!selected) return
    setBusy(true)
    try {
      if (method === 'CASH') {
        await api('/api/payments/cash', { method: 'POST', body: { session_id: selected.id } })
        toast('Бэлэн мөнгөөр төлөгдлөө. Хаалт нээгдэж байна.')
        setSelected(null)
      } else if (method === 'QPAY') {
        const inv = await api('/api/payments/qpay/invoice', { method: 'POST', body: { session_id: selected.id } })
        setQpayInfo(inv)
      }
      loadExits(siteId); loadShift()
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const applyDiscount = async (discountId) => {
    try {
      const updated = await api(`/api/sessions/${selected.id}/apply-discount`,
        { method: 'POST', body: { discount_id: discountId || null } })
      setSelected(updated)
      toast('Хөнгөлөлт шинэчлэгдлээ')
    } catch (e) { toast(e.message, 'error') }
  }

  const openBarrier = async (deviceId) => {
    try {
      await api(`/api/barriers/${deviceId}/open`, { method: 'POST', body: {} })
      toast('Хаалт нээх команд илгээгдлээ')
    } catch (e) { toast(e.message, 'error') }
  }

  const manualExit = async () => {
    if (!confirm(`${selected.plate_number} дугаартай машиныг төлбөргүйгээр гаргах уу?`)) return
    try {
      await api(`/api/sessions/${selected.id}/manual-exit`,
        { method: 'POST', body: { open_barrier: true, reason: 'Кассын гараар гаргалт' } })
      toast('Гаргалаа'); setSelected(null); loadExits(siteId)
    } catch (e) { toast(e.message, 'error') }
  }

  const saveManualEntry = async (e) => {
    e.preventDefault()
    try {
      const body = { site_id: siteId, plate_number: manualEntry.plate_number }
      // datetime-local нь локал цаг — backend UTC хадгалдаг тул хөрвүүлнэ
      if (manualEntry.entry_time) body.entry_time = new Date(manualEntry.entry_time).toISOString().slice(0, 19)
      const s = await api('/api/sessions/manual-entry', { method: 'POST', body })
      toast(`${s.plate_number} бүртгэгдлээ`)
      setManualEntry(null)
    } catch (err) { toast(err.message, 'error') }
  }

  const toggleShift = async () => {
    try {
      if (shift?.open) {
        const res = await api('/api/cashier/shift/close', { method: 'POST' })
        toast(`Ээлж хаагдлаа. Нийт: ${fmt(res.total)}₮`)
      } else {
        await api('/api/cashier/shift/open', { method: 'POST', body: { site_id: siteId } })
        toast('Ээлж нээгдлээ')
      }
      loadShift()
    } catch (e) { toast(e.message, 'error') }
  }

  const fee = selected?.fee

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Касс</h1>
        <div className="flex items-center gap-3">
          <select className="input w-56" value={siteId} onChange={(e) => setSiteId(e.target.value)} aria-label="Зогсоол сонгох">
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <button onClick={() => setManualEntry({ plate_number: '', entry_time: '' })} className="btn-secondary">
            <CarFront size={16} /> Машин бүртгэх
          </button>
          <button onClick={toggleShift}
            className={shift?.open ? 'btn-danger' : 'btn-primary'}>
            {shift?.open ? 'Ээлж хаах' : 'Ээлж нээх'}
          </button>
        </div>
      </div>

      {shift?.open && (
        <div className="card py-3 flex flex-wrap gap-6 text-sm">
          <span className="text-slate-400">Ээлж нээсэн: <span className="text-slate-200 font-mono">{fmtDate(shift.shift.opened_at)}</span></span>
          <span className="text-slate-400">Гүйлгээ: <span className="text-slate-200 font-mono">{shift.count}</span></span>
          <span className="text-slate-400">Нийт орлого: <span className="text-accent font-mono font-semibold">{fmt(shift.total)}₮</span></span>
          {Object.entries(shift.by_provider || {}).map(([k, v]) => (
            <span key={k} className="text-slate-500 font-mono">{k}: {fmt(v.amount)}₮</span>
          ))}
        </div>
      )}

      <div className="grid lg:grid-cols-2 gap-5">
        {/* Гарах гэж буй машинууд */}
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold">Гарах машинууд (төлбөр хүлээж буй)</h2>
            <button onClick={() => loadExits(siteId)} className="p-1.5 rounded hover:bg-surface-muted cursor-pointer" aria-label="Шинэчлэх">
              <RefreshCw size={15} />
            </button>
          </div>
          <div className="space-y-2 max-h-[26rem] overflow-y-auto">
            {exits.length === 0 && <div className="text-sm text-slate-500 text-center py-6">Одоогоор гарах машин алга</div>}
            {exits.map((s) => (
              <button key={s.id} onClick={() => setSelected(s)}
                className={`w-full text-left px-4 py-3 rounded-lg border transition-colors cursor-pointer
                  ${selected?.id === s.id ? 'border-accent bg-accent/5' : 'border-surface-border/60 bg-surface-muted/30 hover:border-slate-500'}`}>
                <div className="flex items-center justify-between">
                  <span className="font-mono font-bold text-lg">{s.plate_number}</span>
                  <span className="font-mono font-semibold text-amber-400">{fmt(s.fee?.total_fee ?? s.total_fee)}₮</span>
                </div>
                <div className="text-xs text-slate-500 mt-1">
                  Орсон: {fmtDate(s.entry_time)} · {fmtDur(s.fee?.duration_minutes ?? s.duration_minutes)}
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Төлбөрийн дэлгэрэнгүй */}
        <div className="card">
          <h2 className="font-semibold mb-3">Төлбөр авах</h2>
          <div className="flex gap-2 mb-4">
            <input className="input" placeholder="Дугаар хайх… (жишээ: 1234АБВ)" value={searchPlate}
              onChange={(e) => setSearchPlate(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === 'Enter' && search()} aria-label="Улсын дугаар хайх" />
            <button onClick={search} className="btn-secondary" aria-label="Хайх"><Search size={16} /></button>
          </div>
          {searchResults && (
            <div className="mb-4 space-y-1.5">
              {searchResults.length === 0 && <div className="text-sm text-slate-500">Нээлттэй бүртгэл олдсонгүй</div>}
              {searchResults.map((s) => (
                <button key={s.id} onClick={() => { setSelected(s); setSearchResults(null) }}
                  className="w-full text-left px-3 py-2 rounded-lg bg-surface-muted/40 hover:bg-surface-muted text-sm cursor-pointer flex justify-between">
                  <span className="font-mono font-semibold">{s.plate_number}</span>
                  <Badge value={s.status} />
                </button>
              ))}
            </div>
          )}

          {selected ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="font-mono text-2xl font-bold">{selected.plate_number}</span>
                <Badge value={selected.status} />
              </div>
              <div className="grid grid-cols-2 gap-2 text-sm bg-surface-muted/30 rounded-lg p-3">
                <span className="text-slate-400">Орсон цаг</span><span className="font-mono text-right">{fmtDate(selected.entry_time)}</span>
                <span className="text-slate-400">Хугацаа</span><span className="font-mono text-right">{fmtDur(fee?.duration_minutes)}</span>
                <span className="text-slate-400">Үндсэн дүн</span><span className="font-mono text-right">{fmt(fee?.base_fee)}₮</span>
                <span className="text-slate-400">Хөнгөлөлт</span><span className="font-mono text-right text-cyan-400">-{fmt(fee?.discount_amount)}₮</span>
                <span className="text-slate-400">НӨАТ (10%)</span><span className="font-mono text-right">{fmt(fee?.vat_amount)}₮</span>
                <span className="text-slate-300 font-semibold">Нийт дүн</span>
                <span className="font-mono text-right text-xl font-bold text-accent">{fmt(fee?.total_fee)}₮</span>
              </div>
              <Field label="Хөнгөлөлт хэрэглэх">
                <select className="input" value={selected.discount_id || ''} onChange={(e) => applyDiscount(e.target.value)}>
                  <option value="">Хөнгөлөлтгүй</option>
                  {discounts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <button onClick={() => pay('CASH')} disabled={busy || fee?.is_free} className="btn-primary justify-center">
                  <Banknote size={16} /> Бэлнээр
                </button>
                <button onClick={() => pay('QPAY')} disabled={busy || fee?.is_free} className="btn-secondary justify-center">
                  <QrCode size={16} /> QPay
                </button>
              </div>
              {fee?.is_free && (
                <div className="text-sm text-cyan-400 bg-cyan-500/10 rounded-lg px-3 py-2">
                  Төлбөргүй: {fee.reason || 'Үнэгүй хугацаанд байна'}
                </div>
              )}
              <button onClick={manualExit} className="btn-secondary w-full justify-center text-xs">
                <DoorOpen size={14} /> Гараар гаргах (төлбөргүй)
              </button>
            </div>
          ) : (
            <div className="text-sm text-slate-500 text-center py-10">
              Зүүн талаас машин сонгох эсвэл дугаараар хайна уу
            </div>
          )}
        </div>
      </div>

      {/* Хаалт шууд удирдах */}
      <div className="card">
        <h2 className="font-semibold mb-3">Хаалт удирдлага</h2>
        <div className="flex flex-wrap gap-2">
          {barriers.map((b) => (
            <button key={b.id} onClick={() => openBarrier(b.id)} className="btn-secondary">
              <DoorOpen size={15} /> {b.name} нээх
            </button>
          ))}
          {barriers.length === 0 && <span className="text-sm text-slate-500">Barrier төхөөрөмж бүртгэгдээгүй</span>}
        </div>
      </div>

      {/* Гараар бүртгэх modal — уншигдалгүй орсон машин (эргүүлийн шалгалт) */}
      <Modal open={!!manualEntry} onClose={() => setManualEntry(null)} title="Машин гараар бүртгэх">
        {manualEntry && (
          <form onSubmit={saveManualEntry} className="space-y-3">
            <div className="text-sm text-slate-400 bg-surface-muted/40 rounded-lg px-3 py-2">
              Орох камерт уншигдалгүй орсон машиныг (эргүүлээр илэрсэн) энд бүртгэнэ.
              Бүртгэсэн цагаас нь төлбөр тооцогдоно.
            </div>
            <Field label="Улсын дугаар" required>
              <input className="input font-mono text-xl text-center tracking-widest uppercase" autoFocus
                value={manualEntry.plate_number} required maxLength={10}
                onChange={(e) => setManualEntry({ ...manualEntry, plate_number: e.target.value.toUpperCase() })} />
            </Field>
            <Field label="Орсон гэж үзэх цаг (хоосон бол одоо)">
              <input className="input" type="datetime-local" value={manualEntry.entry_time}
                onChange={(e) => setManualEntry({ ...manualEntry, entry_time: e.target.value })} />
            </Field>
            <button className="btn-primary w-full justify-center">Бүртгэх</button>
          </form>
        )}
      </Modal>

      {/* QPay QR modal */}
      <Modal open={!!qpayInfo} onClose={() => setQpayInfo(null)} title="QPay төлбөр">
        {qpayInfo && (
          <div className="text-center space-y-3">
            <div className="text-3xl font-bold font-mono text-accent">{fmt(qpayInfo.amount)}₮</div>
            {qpayInfo.qr_image
              ? <img src={`data:image/png;base64,${qpayInfo.qr_image}`} alt="QPay QR код" className="mx-auto w-52 h-52 rounded-lg bg-white p-2" />
              : <div className="text-sm bg-surface-muted rounded-lg p-4 font-mono break-all">{qpayInfo.qr_text}</div>}
            <div className="text-sm text-slate-400">Хэрэглэгч QPay апп-аар уншуулж төлнө. Төлөгдмөгц хаалт автоматаар нээгдэнэ.</div>
            {qpayInfo.mock && <div className="text-xs text-amber-400">MOCK горим — бодит QPay холбогдоогүй</div>}
          </div>
        )}
      </Modal>
    </div>
  )
}
