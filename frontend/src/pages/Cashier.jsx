// Касс — операторын гол дэлгэц: гарах машинууд real-time, төлбөр авах, хаалт нээх, ээлж
// Дэд хэсгүүд нь cashier/ хавтаст — энд төлөв, API дуудлага, real-time урсгал төвлөрнө
import { AlertTriangle, CarFront, FlaskConical, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, fmt, wsConnect } from '../api'
import { useAuth } from '../auth'
import { useToast } from '../components/ui'
import CashierStats from './cashier/CashierStats'
import ExitQueue from './cashier/ExitQueue'
import FreeExitModal from './cashier/FreeExitModal'
import ManualEntryModal, { minutesAgo } from './cashier/ManualEntryModal'
import PaymentPanel from './cashier/PaymentPanel'
import QpayModal from './cashier/QpayModal'
import ShiftCloseModal from './cashier/ShiftCloseModal'
import TodayExitsTable from './cashier/TodayExitsTable'

export default function Cashier() {
  const toast = useToast()
  const { testMode, user, can } = useAuth()
  const [sites, setSites] = useState([])
  const [siteId, setSiteId] = useState('')
  const [exits, setExits] = useState([])
  const [overview, setOverview] = useState(null) // {capacity, occupied, free, rows: өнөөдөр гарсан}
  const [shift, setShift] = useState(null)
  const [selected, setSelected] = useState(null)
  const [searchPlate, setSearchPlate] = useState('')
  const [searchResults, setSearchResults] = useState(null)
  const searchDebounce = useRef(null)
  const [discounts, setDiscounts] = useState([])
  const [busy, setBusy] = useState(false)
  const [qpayInfo, setQpayInfo] = useState(null)
  const [manualEntry, setManualEntry] = useState(null) // {plate_number, entry_time, offset}
  const [blAlert, setBlAlert] = useState(null) // хар жагсаалтын машин орж ирсэн анхааруулга

  const loadExits = useCallback((sid) => {
    if (!sid) return
    api(`/api/sessions/recent-exits?site_id=${sid}`).then(setExits).catch(() => {})
    api(`/api/sessions/today-exits?site_id=${sid}`).then(setOverview).catch(() => {})
  }, [])
  const loadShift = () => api('/api/cashier/shift/current').then(setShift).catch(() => {})

  useEffect(() => {
    api('/api/admin/sites').then((s) => {
      // Оператор зөвхөн өөрийн хариуцах зогсоолыг сонгоно (site_id тохируулсан бол)
      const scoped = user?.role === 'OPERATOR' && user?.site_id
        ? s.filter((x) => x.id === user.site_id) : s
      setSites(scoped)
      if (scoped.length) setSiteId(scoped[0].id)
    })
    api('/api/admin/discounts').then((d) => setDiscounts(d.filter((x) => x.is_active))).catch(() => {})
    loadShift()
  }, [user])

  useEffect(() => {
    if (!siteId) return
    loadExits(siteId)
    const close = wsConnect(siteId, (ev) => {
      loadExits(siteId)
      if (ev?.type === 'DEBT_ALERT') {
        toast(`⚠ ${ev.data?.plate} — ${fmt(ev.data?.debt_amount || 0)}₮ өртэй машин гарах хаалтанд ирлээ!`, 'error')
      }
      // Хар жагсаалтын машин ОРЖ ИРЛЭЭ — өрөө төлүүлэх ганц боломж энэ. Хаалт
      // нээгдсэн эсэхээс үл хамааран самбар дээр наалдсан анхааруулга үлдээнэ.
      if (ev?.type === 'BLACKLIST_ALERT' && ev.data?.lane === 'entry') {
        setBlAlert({ ...ev.data, at: new Date().toISOString() })
      }
    })
    return close
  }, [siteId, loadExits])

  const search = async (q) => {
    const value = (q ?? searchPlate).trim()
    if (value.length < 2) { setSearchResults(null); return }
    try {
      setSearchResults(await api(`/api/sessions/check?plate=${encodeURIComponent(value)}&site_id=${siteId}`))
    } catch (e) { toast(e.message, 'error') }
  }

  // Live хайлт: эхний 2+ тэмдэгт бичихэд таарах машинууд шууд гарна
  const onSearchChange = (value) => {
    const v = value.toUpperCase()
    setSearchPlate(v)
    clearTimeout(searchDebounce.current)
    if (v.trim().length < 2) { setSearchResults(null); return }
    searchDebounce.current = setTimeout(() => search(v), 300)
  }

  const pay = async (method) => {
    if (!selected) return
    // Дансаар: оператор шилжүүлэг ОРЖ ИРСНИЙГ хуулгаас шалгасныг баталгаажуулна
    if (method === 'TRANSFER') {
      const site = sites.find((s) => s.id === siteId)
      const acc = site?.bank_account
        ? `${site.bank_name || ''} ${site.bank_account} (${site.bank_account_name || ''})` : 'зогсоолын данс'
      if (!confirm(`${selected.plate_number} — ${fmt(selected.fee?.total_fee)}₮\n\n${acc} руу шилжүүлэг ОРЖ ИРСНИЙГ хуулгаас шалгасан уу?\n\nOK = төлбөр баталгаажуулж хаалт нээнэ`)) return
    }
    setBusy(true)
    try {
      if (method === 'CASH') {
        await api('/api/payments/cash', { method: 'POST', body: { session_id: selected.id } })
        toast('Бэлэн мөнгөөр төлөгдлөө. Хаалт нээгдэж байна.')
        setSelected(null)
      } else if (method === 'TRANSFER') {
        await api('/api/payments/transfer', { method: 'POST', body: { session_id: selected.id } })
        toast('Дансаар төлөгдлөө. Хаалт нээгдэж байна.')
        setSelected(null)
      } else if (method === 'QPAY') {
        const inv = await api('/api/payments/qpay/invoice', { method: 'POST', body: { session_id: selected.id, source: 'POS' } })
        setQpayInfo(inv)
      }
      loadExits(siteId); loadShift()
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const applyDiscount = async (discountId) => {
    try {
      // Хөнгөлөлт хэрэглэх шалтгааны тайлбар (аудитад хадгалагдана)
      let note = ''
      if (discountId) {
        note = prompt('Хөнгөлөлт хэрэглэх тайлбар (жишээ: дэлгүүрийн купон үзүүлсэн):') || ''
      }
      const updated = await api(`/api/sessions/${selected.id}/apply-discount`,
        { method: 'POST', body: { discount_id: discountId || null, note } })
      setSelected(updated)
      toast('Хөнгөлөлт шинэчлэгдлээ')
    } catch (e) { toast(e.message, 'error') }
  }

  // Төлбөргүй гаргах — шалтгааныг ЖАГСААЛТААС сонгуулна (FreeExitModal).
  // Өмнө нь тогтмол «Кассын гараар гаргалт» текст бичигддэг байсан тул
  // тайлан дээр шалтгаанаар нь ялгах боломжгүй байв.
  const [freeExit, setFreeExit] = useState(false)
  const manualExit = () => setFreeExit(true)

  const doFreeExit = async (body) => {
    setBusy(true)
    try {
      await api(`/api/sessions/${selected.id}/manual-exit`,
        { method: 'POST', body: { open_barrier: true, ...body } })
      toast(body.create_compensation ? 'Гаргаж, нөхөн төлбөрийн нэхэмжлэл үүслээ' : 'Гаргалаа')
      setFreeExit(false); setSelected(null); loadExits(siteId)
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const addTestCar = async () => {
    if (!siteId) return
    try {
      const s = await api('/api/sessions/test-awaiting', { method: 'POST', body: { site_id: siteId } })
      toast(`Тест машин нэмэгдлээ: ${s.plate_number} (${fmt(s.fee?.total_fee ?? s.total_fee)}₮)`)
      loadExits(siteId)
    } catch (e) { toast(e.message, 'error') }
  }

  const [closeModal, setCloseModal] = useState(null) // ээлж хаах тооцоо

  const toggleShift = async () => {
    if (shift?.open) {
      // Ээлж хаах тооцооны дэлгэц нээнэ (шууд хаахгүй)
      const cash = shift.by_provider?.CASH?.amount || 0
      setCloseModal({ confirmed_cash: cash, close_cars: false, note: '' })
      return
    }
    try {
      await api('/api/cashier/shift/open', { method: 'POST', body: { site_id: siteId } })
      toast('Ээлж нээгдлээ'); loadShift()
    } catch (e) { toast(e.message, 'error') }
  }

  const doCloseShift = async () => {
    setBusy(true)
    try {
      const res = await api('/api/cashier/shift/close', {
        method: 'POST',
        body: {
          confirmed_cash: +closeModal.confirmed_cash || 0,
          close_cars: closeModal.close_cars,
          note: closeModal.note,
        },
      })
      toast(`Ээлж хаагдлаа. Нийт орлого: ${fmt(res.total)}₮${res.closed_cars ? ` · ${res.closed_cars} машин гаргав` : ''}`)
      setCloseModal(null); loadShift(); loadExits(siteId)
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const fee = selected?.fee
  // Кассын үйлдлийг РОЛИОР биш ЭРХЭЭР шалгана. Backend 2026-08-12-нд
  // require_role → require("cashier") болсон ч энд роль шалгасаар байсан тул
  // ADMIN/ONLINE_OPERATOR хуудсыг хараад товчнууд нь түгжээтэй байв.
  const canAct = can('cashier')
  const canFreeExit = can('free_exit')  // гараар/төлбөргүй гаргах эрх (санхүүгийн хамгаалалт)
  // «Дансаар» (шилжүүлэг) — банкны API холбогдоогүй тул оператор гараар
  // баталгаажуулна. Ээлж хаахад тусад нь нийлбэрээр харагддаг (аудитын мөр).
  const canTransfer = can('pay_transfer')
  // «Бэлнээр» — ЗӨВХӨН оффисоос ажилладаг ONLINE_OPERATOR дээр нуугдана
  // (тэдэнд бэлэн мөнгөний касс байхгүй). Бусад бүх эрхэд харагдана.
  const showCash = user?.role !== 'ONLINE_OPERATOR'
  const site = sites.find((s) => s.id === siteId)

  const saveNote = async () => {
    if (!selected) return
    try {
      await api(`/api/sessions/${selected.id}/note`, { method: 'PUT', body: { note: selected.note || '' } })
      toast('Тэмдэглэл хадгалагдлаа'); loadExits(siteId)
    } catch (e) { toast(e.message, 'error') }
  }

  return (
    <div className="space-y-5">
      {/* Хар жагсаалтын машин ОРЖ ИРЛЭЭ — өрийг нь авах цорын ганц боломж.
          Оператор өөрөө хаах хүртэл байрандаа үлдэнэ (toast шиг алга болохгүй). */}
      {blAlert && (
        <div className="card border-red-500/60 bg-red-500/10 py-3 flex flex-wrap items-center gap-4">
          <AlertTriangle size={22} className="text-red-400 shrink-0" />
          <div className="flex-1 min-w-56">
            <div className="font-bold text-red-300">
              Хар жагсаалтын машин орлоо: <span className="font-mono text-lg">{blAlert.plate}</span>
              {blAlert.blocked
                ? <span className="ml-2 text-xs bg-red-500/30 px-1.5 py-0.5 rounded">хаалт нээгээгүй</span>
                : <span className="ml-2 text-xs bg-amber-500/25 text-amber-200 px-1.5 py-0.5 rounded">зогсоолд оруулав</span>}
            </div>
            <div className="text-sm text-slate-300">
              {blAlert.debt_amount > 0
                ? <>Төлөгдөөгүй өр: <b className="font-mono text-red-300">{fmt(blAlert.debt_amount)}₮</b>
                    {' '}({blAlert.debt_count} нэхэмжлэл) — гарахад нь заавал барагдуулна уу.</>
                : <>Шалтгаан: {blAlert.reason || '—'}</>}
            </div>
          </div>
          {blAlert.debt_amount > 0 && (
            <Link className="btn-primary bg-red-600 hover:bg-red-500" to="/compensations"
              onClick={() => setBlAlert(null)}>
              Өр барагдуулах
            </Link>
          )}
          <button className="btn-secondary py-1.5" onClick={() => setBlAlert(null)} aria-label="Анхааруулга хаах">
            <X size={15} />
          </button>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Касс</h1>
        <div className="flex items-center gap-3">
          <select className="input w-56" value={siteId} onChange={(e) => setSiteId(e.target.value)} aria-label="Зогсоол сонгох">
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          {testMode && (
            <button onClick={addTestCar} className="btn-secondary border-amber-500/40 text-amber-400"
              title="Тест: камергүйгээр гарах машин нэмнэ">
              <FlaskConical size={16} /> Тест машин
            </button>
          )}
          {canAct && (
            <button onClick={() => setManualEntry({ plate_number: '', entry_time: minutesAgo(0), offset: 0 })} className="btn-secondary">
              <CarFront size={16} /> Машин бүртгэх
            </button>
          )}
          {canAct && (
            <button onClick={toggleShift}
              className={shift?.open ? 'btn-danger' : 'btn-primary'}>
              {shift?.open ? 'Ээлж хаах' : 'Ээлж нээх'}
            </button>
          )}
        </div>
      </div>

      {!canAct && (
        <div className="card border-blue-500/30 bg-blue-500/5 text-sm text-blue-300 py-2.5">
          Та зөвхөн <b>харах</b> эрхтэй. Кассын үйлдэл (төлбөр авах, ээлж) зөвхөн оператор эрхтэй ажилтан хийнэ.
        </div>
      )}

      <CashierStats overview={overview} shift={shift} />

      <div className="grid lg:grid-cols-2 gap-5">
        {/* Гарах гэж буй машинууд */}
        <ExitQueue exits={exits} selected={selected} onSelect={setSelected} onRefresh={() => loadExits(siteId)} />

        {/* Төлбөрийн дэлгэрэнгүй */}
        <PaymentPanel
          selected={selected} setSelected={setSelected} fee={fee} canAct={canAct} canFreeExit={canFreeExit} busy={busy}
          canTransfer={canTransfer} showCash={showCash} site={site}
          discounts={discounts} searchPlate={searchPlate} searchResults={searchResults}
          onSearchChange={onSearchChange} onSearch={search}
          onPickResult={(s) => { setSelected(s); setSearchResults(null); setSearchPlate('') }}
          onPay={pay} onApplyDiscount={applyDiscount} onManualExit={manualExit} onSaveNote={saveNote}
          siteId={siteId} loadExits={loadExits} />
      </div>

      {/* Өнөөдөр гарсан машинууд — гарах камерт уншсан бүх машин (төлбөргүй/үнэгүй ч) */}
      <TodayExitsTable overview={overview} />

      {/* Гараар бүртгэх modal — уншигдалгүй орсон машин (эргүүлийн шалгалт) */}
      <ManualEntryModal manualEntry={manualEntry} setManualEntry={setManualEntry} siteId={siteId} />

      {/* Төлбөргүй гаргах — шалтгаан сонгох */}
      <FreeExitModal open={freeExit} session={selected} fee={fee} busy={busy}
        onClose={() => setFreeExit(false)} onConfirm={doFreeExit} />

      {/* QPay QR modal */}
      <QpayModal qpayInfo={qpayInfo} onClose={() => setQpayInfo(null)} />

      {/* Ээлж хаах — тооцооны дэлгэц */}
      <ShiftCloseModal closeModal={closeModal} setCloseModal={setCloseModal} shift={shift}
        busy={busy} onConfirm={doCloseShift} />
    </div>
  )
}
