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
  if (!res.ok) throw new Error(data.detail || 'Алдаа гарлаа')
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
  const pollRef = useRef(null)

  const load = () => publicApi(`/api/public/wallet/${token}`).then(setData).catch((e) => setError(e.message))
  useEffect(() => { load() }, [token])
  useEffect(() => () => clearInterval(pollRef.current), [])

  const doTopup = async () => {
    setError(''); setBusy(true)
    try {
      const t = await publicApi(`/api/public/wallet/${token}/topup`, { method: 'POST', body: { amount } })
      setTopup(t)
      pollRef.current = setInterval(async () => {
        try {
          const r = await publicApi(`/api/public/wallet/${token}/topup/${t.payment_id}/check`, { method: 'POST' })
          if (r.paid) { clearInterval(pollRef.current); setTopup(null); load() }
        } catch { /* үргэлжилнэ */ }
      }, 3000)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="min-h-dvh bg-slate-950 text-slate-100 flex flex-col items-center px-4 py-6">
      <div className="w-full max-w-md space-y-4">
        <div className="flex items-center gap-2 justify-center">
          <LogoMark className="h-8 w-8" /><LogoText className="h-5" />
        </div>
        {error && <div className="text-sm text-red-400 bg-red-500/10 rounded-xl px-4 py-3">{error}</div>}
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
              className="text-sm text-slate-400 underline">Буцах</button>
          </div>
        ) : (
          <div className="bg-slate-900 rounded-2xl p-4 flex gap-2">
            <input type="number" min="1000" step="1000" value={amount}
              onChange={(e) => setAmount(Number(e.target.value))}
              className="flex-1 bg-slate-800 rounded-xl px-3 py-2.5 text-center" />
            <button onClick={doTopup} disabled={busy || data?.status !== 'ACTIVE'}
              className="px-4 py-2.5 rounded-xl bg-blue-600 font-semibold disabled:opacity-40">
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
