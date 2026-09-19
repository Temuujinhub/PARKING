// Public /wallet/:token — жолоочийн данс: үлдэгдэл, түүх, QPay цэнэглэлт (§8)
import { ArrowDownCircle, ArrowUpCircle, Loader2, Wallet } from 'lucide-react'
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
  if (!res.ok) { const error = new Error(data.detail || 'Алдаа гарлаа'); error.status = res.status; throw error }
  return data
}

const fmt = (n) => Number(n || 0).toLocaleString('mn-MN')

const KIND_LABELS = {
  TOPUP: 'Цэнэглэлт', CHARGE_HOLD: 'Цэнэглэлт (барьцаа)',
  CHARGE_RELEASE: 'Барьцаа буцаалт', CHARGE_SETTLE: 'Цэнэглэлт (бодит)',
  PARKING: 'Зогсоолын төлбөр', CASH_OUT: 'Бэлнээр буцаалт', ADJUST: 'Гар засвар',
}

export default function EvWallet() {
  const { token } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [amount, setAmount] = useState(10000)
  const [topup, setTopup] = useState(null)
  const [busy, setBusy] = useState(false)
  const requestRef = useRef(null)
  const sendingRef = useRef(false)
  const storageKey = `parking-topup:${token}`

  const load = () => publicApi(`/api/public/wallet/${token}`).then(d => {
    setData(d); setTopup(d.pending_topup || null)
  }).catch(e => setError(e.message))
  useEffect(() => {
    requestRef.current = null
    try { requestRef.current = JSON.parse(localStorage.getItem(storageKey)) } catch { /* storage unavailable */ }
    load()
  }, [token])
  useEffect(() => {
    if (!topup?.payment_id) return
    let stopped = false, timer
    const poll = async () => {
      try {
        const r = await publicApi(`/api/public/wallet/${token}/topup/${topup.payment_id}/check`, { method: 'POST' })
        if (!stopped && r.paid) {
          requestRef.current = null
          try { localStorage.removeItem(storageKey) } catch { /* storage unavailable */ }
          setError(''); load(); return
        }
        if (!stopped) setTopup(t => t ? { ...t, status: r.status } : t)
      } catch { /* A failed check does not authorize another invoice. */ }
      if (!stopped) timer = setTimeout(poll, 5000)
    }
    timer = setTimeout(poll, 3000)
    return () => { stopped = true; clearTimeout(timer) }
  }, [token, topup?.payment_id])

  const doTopup = async () => {
    if (sendingRef.current) return
    sendingRef.current = true; setError(''); setBusy(true)
    try {
      // Persist before sending; a lost HTTP response must reuse this identity.
      if (!requestRef.current) requestRef.current = { amount, request_key: crypto.randomUUID() }
      try { localStorage.setItem(storageKey, JSON.stringify(requestRef.current)) } catch { /* server also prevents concurrent attempts */ }
      const t = await publicApi(`/api/public/wallet/${token}/topup`, { method: 'POST', body: requestRef.current })
      if (t.status === 'PAID') {
        requestRef.current = null
        try { localStorage.removeItem(storageKey) } catch { /* storage unavailable */ }
        load()
      } else setTopup(t)
    } catch (e) {
      if (e.status === 422) {
        requestRef.current = null
        try { localStorage.removeItem(storageKey) } catch { /* storage unavailable */ }
      }
      setError(e.message); load()
    }
    finally { sendingRef.current = false; setBusy(false) }
  }

  return (
    <div className="min-h-dvh bg-slate-950 text-slate-100 flex flex-col items-center px-4 py-6">
      <div className="w-full max-w-md space-y-4">
        <div className="flex items-center gap-2 justify-center">
          <LogoMark className="h-8 w-8" /><LogoText className="h-5" />
        </div>
        {error && <div role="alert" className="text-sm text-red-400 bg-red-500/10 rounded-xl px-4 py-3">{error}</div>}
        {data && (
          <div className="bg-slate-900 rounded-2xl p-5 text-center space-y-1">
            <Wallet className="mx-auto text-emerald-400" />
            <div className="text-sm text-slate-400">{data.plate}</div>
            <div className="text-3xl font-bold">{fmt(data.balance)}₮</div>
            {data.status !== 'ACTIVE' && <div className="text-red-400 text-sm">Данс хаагдсан</div>}
          </div>
        )}
        {topup ? (
          <div className="bg-slate-900 rounded-2xl p-5 space-y-3 text-center">
            <div className="font-semibold">Цэнэглэх — {fmt(topup.amount)}₮</div>
            {topup.status === 'PENDING' && topup.qr_image && (
              <img src={`data:image/png;base64,${topup.qr_image}`} alt="QPay QR"
                className="mx-auto w-56 h-56 rounded-xl bg-white p-2" />
            )}
            {topup.status === 'PENDING' && topup.deep_link && (
              <a href={topup.deep_link} className="block w-full py-3 rounded-xl bg-blue-600 hover:bg-blue-500 focus-visible:ring-2 focus-visible:ring-blue-300 font-semibold">
                Банкны аппаар төлөх
              </a>
            )}
            <div role="status" className="text-sm text-slate-300">
              {topup.status === 'PENDING' ? 'Төлбөрийн төлөвийг шалгаж байна…' :
                'Цэнэглэлтийн үр дүнг тулгаж байна. Дахин төлөхгүй; санхүүгийн ажилтанд энэ оролдлогын дугаарыг өгнө үү.'}
            </div>
            <div className="text-xs text-slate-400 break-all">Оролдлого: {topup.payment_id}</div>
            <button onClick={load} className="min-h-11 text-sm text-blue-300 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-300 underline">Төлөв шинэчлэх</button>
          </div>
        ) : (
          <div className="bg-slate-900 rounded-2xl p-4 flex gap-2">
            <input aria-label="Цэнэглэх дүн (төгрөг)" type="number" min="1000" step="1000" value={amount} disabled={busy}
              onChange={(e) => setAmount(Number(e.target.value))}
              className="flex-1 bg-slate-800 rounded-xl px-3 py-2.5 text-center" />
            <button onClick={doTopup} disabled={busy || data?.status !== 'ACTIVE'}
              className="px-4 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 focus-visible:ring-2 focus-visible:ring-blue-300 font-semibold disabled:opacity-40 disabled:cursor-not-allowed">
              {busy ? <Loader2 className="animate-spin" size={18} /> : 'Цэнэглэх'}
            </button>
          </div>
        )}
        {data?.ledger?.length > 0 && (
          <div className="bg-slate-900 rounded-2xl divide-y divide-slate-800">
            {data.ledger.map((r, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-3">
                {r.direction === 'CREDIT'
                  ? <ArrowDownCircle className="text-emerald-400 shrink-0" size={20} />
                  : <ArrowUpCircle className="text-red-400 shrink-0" size={20} />}
                <div className="flex-1 min-w-0">
                  <div className="text-sm">{KIND_LABELS[r.kind] || r.kind}</div>
                  <div className="text-xs text-slate-500">
                    {new Date(r.created_at + 'Z').toLocaleString('mn-MN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}
                  </div>
                </div>
                <div className={`text-sm font-semibold ${r.kind === 'CHARGE_SETTLE' ? 'text-slate-500' : r.direction === 'CREDIT' ? 'text-emerald-400' : 'text-red-400'}`}>
                  {r.kind === 'CHARGE_SETTLE' ? '' : r.direction === 'CREDIT' ? '+' : '−'}{fmt(r.amount)}₮
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
