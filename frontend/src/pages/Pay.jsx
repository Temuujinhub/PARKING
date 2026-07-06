// Public /pay — жолоочийн QR төлбөрийн хуудас (нэвтрэлтгүй, mobile-first)
// Урсгал: QR уншина → дугаараа оруулна (эсвэл сүүлд уншигдсанаас сонгоно) → QPay → нээгдэнэ
import { ArrowLeft, Car, CheckCircle2, Clock, CreditCard, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { fmt, fmtDur } from '../api'
import { LogoMark, LogoText } from '../components/Logo'

async function publicApi(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || 'GET',
    headers: opts.body ? { 'Content-Type': 'application/json' } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || 'Алдаа гарлаа')
  return data
}

const fmtTime = (s) => new Date(s + 'Z').toLocaleString('mn-MN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })

export default function Pay() {
  const [params] = useSearchParams()
  const siteCode = params.get('site') || ''
  const [site, setSite] = useState(null)
  const [recent, setRecent] = useState([])
  const [plate, setPlate] = useState('')
  const [session, setSession] = useState(null)
  const [payment, setPayment] = useState(null)
  const [paid, setPaid] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [matches, setMatches] = useState([])
  const [receipt, setReceipt] = useState(null)
  const [vatType, setVatType] = useState('PERSON') // PERSON | ORG
  const [orgTin, setOrgTin] = useState('')
  const pollRef = useRef(null)
  const debounceRef = useRef(null)

  useEffect(() => {
    if (!siteCode) { setError('QR код буруу байна — зогсоолын код олдсонгүй.'); return }
    publicApi(`/api/public/site/${siteCode}`).then(setSite).catch((e) => setError(e.message))
    publicApi(`/api/public/recent-exits/${siteCode}`).then(setRecent).catch(() => {})
  }, [siteCode])

  useEffect(() => () => { clearInterval(pollRef.current); clearTimeout(debounceRef.current) }, [])

  // Хялбар хайлт: 2+ тэмдэгт бичихэд таарах машинуудыг live харуулна
  const onPlateChange = (value) => {
    const v = value.toUpperCase()
    setPlate(v)
    setError('')
    clearTimeout(debounceRef.current)
    if (v.trim().length < 2) { setMatches([]); return }
    debounceRef.current = setTimeout(async () => {
      try {
        const list = await publicApi(`/api/public/search?site=${siteCode}&q=${encodeURIComponent(v.trim())}`)
        setMatches(list)
      } catch { setMatches([]) }
    }, 350)
  }

  const search = async (p) => {
    const target = (p || plate).toUpperCase().replace(/\s/g, '')
    if (!target) return
    setBusy(true); setError('')
    try {
      const s = await publicApi(`/api/public/sessions?plate=${encodeURIComponent(target)}&site=${siteCode}`)
      setSession(s)
      if (s.paid) setPaid(true)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const payQpay = async () => {
    if (vatType === 'ORG' && !/^\d{7}$|^\d{10,14}$/.test(orgTin.trim())) {
      setError('Байгууллагын ТТД буруу байна (7 эсвэл 10+ оронтой тоо)')
      return
    }
    setBusy(true); setError('')
    try {
      const body = { session_id: session.session_id }
      if (vatType === 'ORG') body.customer_tin = orgTin.trim()
      const inv = await publicApi('/api/payments/qpay/invoice', { method: 'POST', body })
      setPayment(inv)
      // Утасны QPay апп руу шилжүүлнэ
      if (inv.deep_link && !inv.mock) window.location.href = inv.deep_link
      // Төлөлт шалгах polling (5 сек тутам)
      pollRef.current = setInterval(async () => {
        try {
          const st = await publicApi(`/api/payments/qpay/check/${inv.payment_id}`, { method: 'POST' })
          if (st.status === 'PAID') { clearInterval(pollRef.current); onPaid(inv.payment_id) }
        } catch {}
      }, 5000)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const onPaid = async (paymentId) => {
    setPaid(true)
    try {
      const r = await publicApi(`/api/public/receipt/${paymentId}`)
      setReceipt({ ...r, qr_png: r.qr_data ? `/api/public/receipt/${paymentId}/qr.png` : null })
    } catch {}
  }

  // Туршилтын горим: QPay-г алгасаж төлөгдсөн болгоно (зөвхөн mock үед харагдана)
  const mockPay = async () => {
    setBusy(true)
    try {
      await publicApi(`/api/payments/qpay/webhook?payment_id=${payment.payment_id}`, {
        method: 'POST', body: { payment_status: 'PAID', amount: payment.amount },
      })
      clearInterval(pollRef.current)
      onPaid(payment.payment_id)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  // ─── Дэлгэцүүд ───
  if (paid) {
    return (
      <Shell site={site}>
        <div className="text-center py-6 space-y-4">
          <CheckCircle2 size={64} className="mx-auto text-accent" aria-hidden />
          <h2 className="text-2xl font-bold">Төлбөр төлөгдлөө!</h2>
          <p className="text-slate-300">
            Хаалт нээгдэнэ — <b>сайн замаараа яваарай!</b>
          </p>
          {receipt && (
            <div className="text-left bg-surface-muted/40 rounded-xl p-4 space-y-2">
              <div className="text-center text-xs font-bold tracking-widest text-slate-400 uppercase pb-1 border-b border-dashed border-surface-border">
                НӨАТ-ийн баримт (e-Barimt)
              </div>
              <div className="grid grid-cols-2 gap-y-1.5 text-sm pt-1">
                <span className="text-slate-400">Машины дугаар</span>
                <span className="font-mono text-right font-bold">{receipt.plate_number}</span>
                <span className="text-slate-400">Төлсөн дүн</span>
                <span className="font-mono text-right">{fmt(receipt.amount)}₮</span>
                <span className="text-slate-400">НӨАТ (10%)</span>
                <span className="font-mono text-right">{fmt(receipt.vat_amount)}₮</span>
                <span className="text-slate-400">ДДТД</span>
                <span className="font-mono text-right text-[10px] pt-1 break-all">{receipt.ebarimt_id || '-'}</span>
                <span className="text-slate-400">Сугалааны код</span>
                <span className="font-mono text-right text-lg font-bold text-accent">{receipt.lottery_code || '-'}</span>
              </div>
              {receipt.qr_png && (
                <div className="pt-2 text-center border-t border-dashed border-surface-border">
                  <img src={receipt.qr_png} alt="e-Barimt баримтын QR код"
                    className="mx-auto w-40 h-40 bg-white rounded-lg p-2 mt-2" />
                  <div className="text-xs text-slate-500 mt-1.5">ebarimt апп-аар уншуулж баримтаа бүртгүүлээрэй</div>
                </div>
              )}
            </div>
          )}
          <p className="text-sm text-slate-400">
            {site?.grace_minutes || 15} минутын дотор гарна уу. Хаалт нээгдэхгүй бол дугаараа гарах камерт дахин уншуулаарай.
          </p>
        </div>
      </Shell>
    )
  }

  if (payment) {
    return (
      <Shell site={site}>
        <button onClick={() => { setPayment(null); clearInterval(pollRef.current) }}
          className="flex items-center gap-1 text-sm text-slate-400 mb-4 cursor-pointer"><ArrowLeft size={15} /> Буцах</button>
        <div className="text-center space-y-4">
          <div className="text-sm text-slate-400">Төлөх дүн</div>
          <div className="text-4xl font-bold font-mono text-accent">{fmt(payment.amount)}₮</div>
          {payment.qr_image ? (
            <img src={`data:image/png;base64,${payment.qr_image}`} alt="QPay QR код" className="mx-auto w-56 h-56 bg-white rounded-2xl p-3" />
          ) : (
            <div className="text-xs bg-surface-muted rounded-xl p-4 font-mono break-all">{payment.qr_text}</div>
          )}
          {payment.deep_link && (
            <a href={payment.deep_link} className="btn-primary w-full justify-center text-base py-3">
              <CreditCard size={18} /> QPay апп нээх
            </a>
          )}
          <div className="flex items-center justify-center gap-2 text-sm text-slate-400">
            <Loader2 size={15} className="animate-spin" aria-hidden /> Төлбөр хүлээж байна…
          </div>
          {payment.mock && (
            <div className="space-y-2">
              <div className="text-xs text-amber-400">Туршилтын горим (QPay холбогдоогүй)</div>
              <button onClick={mockPay} disabled={busy}
                className="btn w-full justify-center bg-amber-500/15 text-amber-400 border border-amber-500/40 hover:bg-amber-500/25">
                {busy ? <Loader2 className="animate-spin" size={16} /> : null}
                Туршилт: Төлөгдсөн болгож НӨАТ баримт авах →
              </button>
            </div>
          )}
        </div>
      </Shell>
    )
  }

  if (session) {
    return (
      <Shell site={site}>
        <button onClick={() => setSession(null)} className="flex items-center gap-1 text-sm text-slate-400 mb-4 cursor-pointer">
          <ArrowLeft size={15} /> Буцах
        </button>
        <div className="space-y-4">
          <div className="text-center">
            <div className="font-mono text-3xl font-bold tracking-wider">{session.plate_number}</div>
          </div>
          <div className="bg-surface-muted/40 rounded-xl p-4 grid grid-cols-2 gap-y-2.5 text-sm">
            <span className="text-slate-400 flex items-center gap-1.5"><Clock size={14} aria-hidden /> Орсон цаг</span>
            <span className="font-mono text-right">{fmtTime(session.entry_time)}</span>
            <span className="text-slate-400">Зогссон хугацаа</span>
            <span className="font-mono text-right">{fmtDur(session.duration_minutes)}</span>
            <span className="text-slate-400">Үндсэн дүн</span>
            <span className="font-mono text-right">{fmt(session.base_fee)}₮</span>
            {session.discount_amount > 0 && (<>
              <span className="text-slate-400">Хөнгөлөлт</span>
              <span className="font-mono text-right text-cyan-400">-{fmt(session.discount_amount)}₮</span>
            </>)}
            <span className="text-slate-400">НӨАТ (10%)</span>
            <span className="font-mono text-right">{fmt(session.vat_amount)}₮</span>
            <span className="font-semibold text-base pt-1 border-t border-surface-border/50">Нийт дүн</span>
            <span className="font-mono text-right text-2xl font-bold text-accent pt-1 border-t border-surface-border/50">{fmt(session.total_fee)}₮</span>
          </div>
          {session.is_free ? (
            <div className="text-center bg-accent/10 text-accent rounded-xl p-4">
              <CheckCircle2 className="mx-auto mb-1" aria-hidden />
              Төлбөргүй — {session.free_reason || 'үнэгүй хугацаанд байна'}. Шууд гарна уу!
            </div>
          ) : (
            <>
              {/* НӨАТ баримтын төрөл (easy-park UAT item 25) */}
              <div>
                <div className="text-sm text-slate-400 mb-2">НӨАТ-ийн баримт:</div>
                <div className="grid grid-cols-2 gap-2">
                  {[['PERSON', 'Хувь хүн'], ['ORG', 'Байгууллага']].map(([v, l]) => (
                    <button key={v} type="button" onClick={() => setVatType(v)}
                      className={`px-3 py-2.5 rounded-xl text-sm font-medium border transition-colors cursor-pointer
                        ${vatType === v ? 'bg-accent text-white border-accent' : 'bg-surface-muted/40 text-slate-300 border-surface-border'}`}>
                      {l}
                    </button>
                  ))}
                </div>
                {vatType === 'ORG' && (
                  <input className="input mt-2 font-mono text-center" inputMode="numeric"
                    placeholder="Байгууллагын ТТД (регистр)" value={orgTin} maxLength={14}
                    onChange={(e) => setOrgTin(e.target.value.replace(/\D/g, ''))} aria-label="Байгууллагын ТТД" />
                )}
              </div>
              <button onClick={payQpay} disabled={busy} className="btn-primary w-full justify-center text-base py-3.5">
                {busy ? <Loader2 className="animate-spin" size={18} /> : <CreditCard size={18} />} QPay-ээр төлөх
              </button>
            </>
          )}
        </div>
      </Shell>
    )
  }

  return (
    <Shell site={site}>
      <div className="space-y-5">
        {recent.length > 0 && (
          <div>
            <div className="text-sm text-slate-400 mb-2">Гарах хаалтан дээр уншигдсан машинууд:</div>
            <div className="space-y-2">
              {recent.map((r, i) => (
                <button key={i} onClick={() => { setPlate(r.plate_number); search(r.plate_number) }}
                  className="w-full flex items-center justify-between px-4 py-3.5 rounded-xl bg-surface-muted/50 border border-surface-border/60 hover:border-accent transition-colors cursor-pointer">
                  <span className="font-mono text-lg font-bold tracking-wider">{r.plate_number}</span>
                  <span className="font-mono text-amber-400 font-semibold">{fmt(r.total_fee)}₮</span>
                </button>
              ))}
            </div>
            <div className="text-xs text-slate-500 mt-2">Өөрийн машиныг сонгоод дугаараа бүрэн оруулна уу.</div>
          </div>
        )}
        <div>
          <label className="text-sm text-slate-400 block mb-2" htmlFor="plate">
            Машины улсын дугаараа оруулна уу <span className="text-slate-500">(эхний тоог бичихэд хайлт гарна)</span>:
          </label>
          <input id="plate" className="input text-center text-2xl font-mono tracking-widest py-3.5 uppercase"
            placeholder="1234 АБВ" value={plate} maxLength={10} autoComplete="off"
            onChange={(e) => onPlateChange(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()} />
        </div>
        {matches.length > 0 && (
          <div className="space-y-2" aria-live="polite">
            <div className="text-xs text-slate-500">Таарсан машинууд — өөрийнхөө дугаарыг сонгоно уу:</div>
            {matches.map((m, i) => (
              <button key={i} onClick={() => { setMatches([]); setPlate(m.plate_number); search(m.plate_number) }}
                className="w-full flex items-center justify-between px-4 py-3 rounded-xl bg-surface-muted/50 border border-surface-border/60 hover:border-accent transition-colors cursor-pointer">
                <span className="font-mono text-lg font-bold tracking-wider">{m.plate_number}</span>
                <span className={`font-mono font-semibold ${m.is_free ? 'text-accent' : 'text-amber-400'}`}>
                  {m.is_free ? 'Үнэгүй' : `${fmt(m.total_fee)}₮`}
                </span>
              </button>
            ))}
          </div>
        )}
        {error && <div role="alert" className="text-sm text-red-400 bg-red-500/10 rounded-xl px-4 py-3">{error}</div>}
        <button onClick={() => search()} disabled={busy || !plate.trim()} className="btn-primary w-full justify-center text-base py-3.5">
          {busy ? <Loader2 className="animate-spin" size={18} /> : <Car size={18} />} Төлбөр шалгах
        </button>
      </div>
    </Shell>
  )
}

function Shell({ site, children }) {
  return (
    <div className="min-h-dvh bg-surface flex flex-col items-center px-4 py-6">
      <div className="w-full max-w-md">
        <div className="text-center mb-6">
          <div className="inline-flex items-center gap-2.5">
            <LogoMark size={38} />
            <LogoText className="text-xl" />
          </div>
          <div className="text-sm text-slate-400 mt-2">Зогсоолын төлбөр{site && ` — ${site.name} · ${site.zone_code} бүс`}</div>
        </div>
        <div className="card">{children}</div>
        <div className="text-center text-xs text-slate-600 mt-4">Easy Parking</div>
      </div>
    </div>
  )
}
