// Public /ev/:key — цэнэглэгчийн QR хуудас (нэвтрэлтгүй, mobile-first)
// Урсгал (EV_CHARGING_PLAN.md §6.1): QR уншина → дугаар+утас → данс/үлдэгдэл
// → дүн сонгоно (хүрэлцэхгүй бол QPay-ээр цэнэглэнэ) → эхлүүлнэ → амьд явц
import { ArrowLeft, BatteryCharging, CheckCircle2, Loader2, Wallet, Zap } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { LogoMark, LogoText } from '../../components/Logo'

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

const fmt = (n) => Number(n || 0).toLocaleString('mn-MN')
const AMOUNTS = [5000, 10000, 20000, 30000, 50000]

const ErrorBox = ({ error }) => error
  ? <div role="alert" className="text-sm text-red-400 bg-red-500/10 rounded-xl px-4 py-3">{error}</div>
  : null

export default function EvCharge() {
  const { key } = useParams()
  const [info, setInfo] = useState(null)
  const [plate, setPlate] = useState('')
  const [phone, setPhone] = useState('')
  const [wallet, setWallet] = useState(null)     // {plate, balance, wallet_token}
  const [amount, setAmount] = useState(10000)
  const [topup, setTopup] = useState(null)       // QPay invoice
  const [session, setSession] = useState(null)   // амьд явц
  const [sessionToken, setSessionToken] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const pollRef = useRef(null)

  const loadInfo = () => publicApi(`/api/public/ev/${key}`).then(setInfo).catch((e) => setError(e.message))
  useEffect(() => { loadInfo() }, [key])
  useEffect(() => () => clearInterval(pollRef.current), [])

  const lookup = async () => {
    setError(''); setBusy(true)
    try {
      const w = await publicApi(`/api/public/ev/${key}/lookup`, { method: 'POST', body: { plate, phone } })
      setWallet(w)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const refreshBalance = async () => {
    if (!wallet) return
    try {
      const w = await publicApi(`/api/public/wallet/${wallet.wallet_token}`)
      setWallet((old) => ({ ...old, balance: w.balance }))
      return w.balance
    } catch { return wallet.balance }
  }

  const doTopup = async () => {
    setError(''); setBusy(true)
    const need = Math.max(1000, amount - wallet.balance)
    try {
      const t = await publicApi(`/api/public/wallet/${wallet.wallet_token}/topup`,
        { method: 'POST', body: { amount: need } })
      setTopup(t)
      clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        try {
          const r = await publicApi(`/api/public/wallet/${wallet.wallet_token}/topup/${t.payment_id}/check`, { method: 'POST' })
          if (r.paid) {
            clearInterval(pollRef.current)
            setTopup(null)
            setWallet((old) => ({ ...old, balance: r.balance }))
          }
        } catch { /* поллинг үргэлжилнэ */ }
      }, 3000)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const start = async () => {
    setError(''); setBusy(true)
    try {
      const r = await publicApi(`/api/public/ev/${key}/start`,
        { method: 'POST', body: { plate, phone, amount } })
      setSessionToken(r.session_token)
      clearInterval(pollRef.current)
      pollRef.current = setInterval(async () => {
        try {
          const s = await publicApi(`/api/public/ev/session/${r.session_token}`)
          setSession(s)
          if (['SETTLED', 'CANCELLED'].includes(s.status)) clearInterval(pollRef.current)
        } catch { /* поллинг үргэлжилнэ */ }
      }, 3000)
      setSession({ status: 'PENDING_START', energy_wh: 0 })
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const stop = async () => {
    setBusy(true)
    try { await publicApi(`/api/public/ev/session/${sessionToken}/stop`, { method: 'POST' }) }
    catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const pct = session && session.wh_limit ? Math.min(100, Math.round((session.energy_wh / session.wh_limit) * 100)) : 0

  return (
    <div className="min-h-dvh bg-slate-950 text-slate-100 flex flex-col items-center px-4 py-6">
      <div className="w-full max-w-md space-y-4">
        <div className="flex items-center gap-2 justify-center">
          <LogoMark className="h-8 w-8" /><LogoText className="h-5" />
        </div>

        {info && (
          <div className="bg-slate-900 rounded-2xl p-4 flex items-center gap-3">
            <Zap className="text-amber-400 shrink-0" />
            <div className="flex-1">
              <div className="font-semibold">{info.name} · {info.connector_id}-р бууц</div>
              <div className="text-sm text-slate-400">{info.site} · {fmt(info.price_per_kwh)}₮/кВт.ц</div>
            </div>
            <span className={`text-xs px-2 py-1 rounded-full ${info.online ? (info.plugged ? 'bg-emerald-500/20 text-emerald-300' : 'bg-slate-700 text-slate-300') : 'bg-red-500/20 text-red-300'}`}>
              {!info.online ? 'Офлайн' : info.busy ? 'Цэнэглэж байна' : info.plugged ? 'Залгаастай' : 'Сул'}
            </span>
          </div>
        )}
        <ErrorBox error={error} />

        {/* ── Амьд явц ── */}
        {session ? (
          <div className="bg-slate-900 rounded-2xl p-5 space-y-4">
            {session.status === 'SETTLED' ? (
              <div className="text-center space-y-2">
                <CheckCircle2 className="mx-auto text-emerald-400" size={44} />
                <div className="text-lg font-semibold">Цэнэглэлт дууслаа</div>
                <div className="text-slate-300">{fmt(session.energy_wh)} Wh · {fmt(session.total_amount)}₮</div>
                <div className="text-sm text-slate-400">Дансны үлдэгдэл: {fmt(session.balance)}₮</div>
              </div>
            ) : (
              <>
                <div className="flex items-center gap-2">
                  <BatteryCharging className="text-emerald-400 animate-pulse" />
                  <div className="font-semibold">
                    {session.status === 'PENDING_START' ? 'Эхлүүлж байна…' : 'Цэнэглэж байна'}
                  </div>
                </div>
                <div className="h-3 bg-slate-800 rounded-full overflow-hidden">
                  <div className="h-full bg-emerald-500 transition-all" style={{ width: `${pct}%` }} />
                </div>
                <div className="grid grid-cols-3 gap-2 text-center text-sm">
                  <div><div className="text-slate-400">Энерги</div><div className="font-semibold">{fmt(session.energy_wh)} Wh</div></div>
                  <div><div className="text-slate-400">Зарцуулалт</div><div className="font-semibold">{fmt(session.spent)}₮</div></div>
                  <div><div className="text-slate-400">SOC</div><div className="font-semibold">{session.soc != null ? `${session.soc}%` : '—'}</div></div>
                </div>
                {session.status === 'RUNNING' && (
                  <button onClick={stop} disabled={busy}
                    className="w-full py-3 rounded-xl bg-red-500/20 text-red-300 font-semibold">
                    Зогсоох
                  </button>
                )}
              </>
            )}
          </div>
        ) : !wallet ? (
          /* ── Алхам 1: дугаар + утас ── */
          <div className="bg-slate-900 rounded-2xl p-5 space-y-3">
            <label className="block text-sm text-slate-400">Машины дугаар</label>
            <input value={plate} onChange={(e) => setPlate(e.target.value.toUpperCase())}
              placeholder="1234 УБА" autoComplete="off"
              className="w-full bg-slate-800 rounded-xl px-4 py-3 text-lg tracking-widest text-center" />
            <label className="block text-sm text-slate-400">Утасны дугаар</label>
            <input value={phone} onChange={(e) => setPhone(e.target.value)} inputMode="tel"
              placeholder="9911 2233"
              className="w-full bg-slate-800 rounded-xl px-4 py-3 text-lg text-center" />
            <button onClick={lookup} disabled={busy || !plate || !phone}
              className="w-full py-3 rounded-xl bg-emerald-600 font-semibold disabled:opacity-40 flex items-center justify-center gap-2">
              {busy && <Loader2 className="animate-spin" size={18} />}Үргэлжлүүлэх
            </button>
          </div>
        ) : topup ? (
          /* ── QPay цэнэглэлт ── */
          <div className="bg-slate-900 rounded-2xl p-5 space-y-3 text-center">
            <div className="font-semibold">Данс цэнэглэх — {fmt(topup.amount)}₮</div>
            {topup.qr_image && (
              <img src={`data:image/png;base64,${topup.qr_image}`} alt="QPay QR"
                className="mx-auto w-56 h-56 rounded-xl bg-white p-2" />
            )}
            {topup.deep_link && (
              <a href={topup.deep_link} className="block w-full py-3 rounded-xl bg-blue-600 font-semibold">
                Банкны аппаар төлөх
              </a>
            )}
            <div className="text-sm text-slate-400 flex items-center justify-center gap-2">
              <Loader2 className="animate-spin" size={14} />Төлбөр хүлээж байна…
            </div>
            <button onClick={() => { clearInterval(pollRef.current); setTopup(null) }}
              className="text-sm text-slate-400 flex items-center gap-1 mx-auto">
              <ArrowLeft size={14} />Буцах
            </button>
          </div>
        ) : (
          /* ── Алхам 2: үлдэгдэл + дүн сонгох ── */
          <div className="bg-slate-900 rounded-2xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-slate-300"><Wallet size={18} />{wallet.plate}</div>
              <div className="font-semibold text-emerald-400">{fmt(wallet.balance)}₮</div>
            </div>
            <div>
              <div className="text-sm text-slate-400 mb-2">Цэнэглэх дүн</div>
              <div className="grid grid-cols-3 gap-2">
                {AMOUNTS.map((a) => (
                  <button key={a} onClick={() => setAmount(a)}
                    className={`py-2.5 rounded-xl text-sm font-semibold ${amount === a ? 'bg-emerald-600' : 'bg-slate-800 text-slate-300'}`}>
                    {fmt(a)}₮
                  </button>
                ))}
                <input type="number" min="1000" step="1000" value={amount}
                  onChange={(e) => setAmount(Number(e.target.value))}
                  className="py-2.5 rounded-xl bg-slate-800 text-center text-sm col-span-1" />
              </div>
              <div className="text-xs text-slate-500 mt-2">≈ {fmt(amount)} Wh ({(amount / 1000).toFixed(1)} кВт.ц)</div>
            </div>
            {wallet.balance >= amount ? (
              <button onClick={start} disabled={busy || !info?.plugged}
                className="w-full py-3 rounded-xl bg-emerald-600 font-semibold disabled:opacity-40 flex items-center justify-center gap-2">
                {busy && <Loader2 className="animate-spin" size={18} />}
                {info?.plugged ? 'Цэнэглэж эхлэх' : 'Эхлээд буужаа залгана уу'}
              </button>
            ) : (
              <button onClick={doTopup} disabled={busy}
                className="w-full py-3 rounded-xl bg-blue-600 font-semibold flex items-center justify-center gap-2">
                {busy && <Loader2 className="animate-spin" size={18} />}
                QPay-ээр {fmt(amount - wallet.balance)}₮ цэнэглэх
              </button>
            )}
            <a href={`/wallet/${wallet.wallet_token}`} className="block text-center text-sm text-slate-400 underline">
              Дансны түүх харах
            </a>
          </div>
        )}
      </div>
    </div>
  )
}
