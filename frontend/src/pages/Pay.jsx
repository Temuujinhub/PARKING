// Public /pay — жолоочийн QR төлбөрийн хуудас (нэвтрэлтгүй, mobile-first)
// Урсгал: QR уншина → дугаараа оруулна (эсвэл сүүлд уншигдсанаас сонгоно) → QPay → нээгдэнэ
import { ArrowLeft, Car, CheckCircle2, Clock, CreditCard, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { fmt, fmtDur } from '../api'

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
  const pollRef = useRef(null)

  useEffect(() => {
    if (!siteCode) { setError('QR код буруу байна — зогсоолын код олдсонгүй.'); return }
    publicApi(`/api/public/site/${siteCode}`).then(setSite).catch((e) => setError(e.message))
    publicApi(`/api/public/recent-exits/${siteCode}`).then(setRecent).catch(() => {})
  }, [siteCode])

  useEffect(() => () => clearInterval(pollRef.current), [])

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
    setBusy(true); setError('')
    try {
      const inv = await publicApi('/api/payments/qpay/invoice', { method: 'POST', body: { session_id: session.session_id } })
      setPayment(inv)
      // Утасны QPay апп руу шилжүүлнэ
      if (inv.deep_link && !inv.mock) window.location.href = inv.deep_link
      // Төлөлт шалгах polling (5 сек тутам)
      pollRef.current = setInterval(async () => {
        try {
          const st = await publicApi(`/api/payments/qpay/check/${inv.payment_id}`, { method: 'POST' })
          if (st.status === 'PAID') { clearInterval(pollRef.current); setPaid(true) }
        } catch {}
      }, 5000)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  // ─── Дэлгэцүүд ───
  if (paid) {
    return (
      <Shell site={site}>
        <div className="text-center py-10 space-y-4">
          <CheckCircle2 size={72} className="mx-auto text-accent" aria-hidden />
          <h2 className="text-2xl font-bold">Төлбөр төлөгдлөө!</h2>
          <p className="text-slate-300">
            Хаалт нээгдэнэ — <b>сайн замаараа яваарай!</b>
          </p>
          {session && (
            <p className="text-sm text-slate-400">
              {site?.grace_minutes || 15} минутын дотор гарна уу. Хаалт нээгдэхгүй бол дугаараа гарах камерт дахин уншуулаарай.
            </p>
          )}
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
          {payment.mock && <div className="text-xs text-amber-400">Туршилтын горим (QPay холбогдоогүй)</div>}
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
            <button onClick={payQpay} disabled={busy} className="btn-primary w-full justify-center text-base py-3.5">
              {busy ? <Loader2 className="animate-spin" size={18} /> : <CreditCard size={18} />} QPay-ээр төлөх
            </button>
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
          <label className="text-sm text-slate-400 block mb-2" htmlFor="plate">Машины улсын дугаараа оруулна уу:</label>
          <input id="plate" className="input text-center text-2xl font-mono tracking-widest py-3.5 uppercase"
            placeholder="1234 АБВ" value={plate} maxLength={10} autoComplete="off"
            onChange={(e) => setPlate(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === 'Enter' && search()} />
        </div>
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
          <div className="inline-flex items-center gap-2 text-xl font-bold">
            <span className="w-8 h-8 rounded-lg bg-accent text-slate-900 flex items-center justify-center font-black">P</span>
            Зогсоолын төлбөр
          </div>
          {site && <div className="text-sm text-slate-400 mt-1">{site.name} · {site.zone_code} бүс</div>}
        </div>
        <div className="card">{children}</div>
        <div className="text-center text-xs text-slate-600 mt-4">Smart Parking MN</div>
      </div>
    </div>
  )
}
