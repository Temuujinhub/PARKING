// Касс/админ: жолоочийн данс — хайлт, түүх, гар засвар, бэлнээр буцаах (§8)
import { Loader2, Search, Wallet as WalletIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Field, Modal, useToast } from '../components/ui'

const fmt = (n) => Number(n || 0).toLocaleString('mn-MN')

const KIND_LABELS = {
  TOPUP: 'Цэнэглэлт', CHARGE_HOLD: 'EV барьцаа', CHARGE_RELEASE: 'Барьцаа буцаалт',
  CHARGE_SETTLE: 'EV бодит', PARKING: 'Зогсоол', CASH_OUT: 'Бэлнээр буцаалт', ADJUST: 'Гар засвар',
}

export default function Wallets() {
  const toast = useToast()
  const [q, setQ] = useState('')
  const [rows, setRows] = useState([])
  const [sel, setSel] = useState(null)      // сонгосон дансны дэлгэрэнгүй
  const [action, setAction] = useState(null) // {type: 'adjust'|'cashout'}
  const [busy, setBusy] = useState(false)
  const debounceRef = useRef(null)

  const search = async (query) => {
    try { setRows(await api(`/api/admin/wallets?q=${encodeURIComponent(query)}`)) }
    catch (e) { toast.error(e.message) }
  }
  useEffect(() => { search('') }, [])
  const onQ = (v) => {
    setQ(v)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => search(v), 300)
  }

  const open = async (id) => {
    try { setSel(await api(`/api/admin/wallets/${id}`)) }
    catch (e) { toast.error(e.message) }
  }

  const doAction = async (form) => {
    setBusy(true)
    try {
      const path = action.type === 'adjust'
        ? `/api/admin/wallets/${sel.id}/adjust`
        : `/api/admin/wallets/${sel.id}/cash-out`
      await api(path, { method: 'POST', body: form })
      toast.success('Амжилттай')
      setAction(null)
      await open(sel.id)
      search(q)
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="p-4 sm:p-6 space-y-4">
      <h1 className="text-xl font-bold flex items-center gap-2"><WalletIcon />Жолоочийн данс</h1>
      <div className="relative max-w-md">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <input className="input pl-9" placeholder="Дугаар эсвэл утсаар хайх…"
          value={q} onChange={(e) => onQ(e.target.value)} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="bg-slate-900 rounded-2xl divide-y divide-slate-800 max-h-[70vh] overflow-y-auto">
          {rows.map((w) => (
            <button key={w.id} onClick={() => open(w.id)}
              className={`w-full flex items-center justify-between px-4 py-3 text-left hover:bg-slate-800/60 ${sel?.id === w.id ? 'bg-slate-800/80' : ''}`}>
              <div>
                <div className="font-semibold">{w.plate}</div>
                <div className="text-xs text-slate-500">{w.phone || '—'} {w.status !== 'ACTIVE' && '· ХААГДСАН'}</div>
              </div>
              <div className="font-semibold text-emerald-400">{fmt(w.balance)}₮</div>
            </button>
          ))}
          {rows.length === 0 && <div className="px-4 py-8 text-center text-slate-600 text-sm">Данс олдсонгүй</div>}
        </div>

        {sel && (
          <div className="bg-slate-900 rounded-2xl p-4 space-y-3 max-h-[70vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-lg font-bold">{sel.plate_number}</div>
                <div className="text-sm text-slate-500">{sel.phone || '—'}</div>
              </div>
              <div className="text-2xl font-bold text-emerald-400">{fmt(sel.balance)}₮</div>
            </div>
            <div className="flex gap-2">
              <button onClick={() => setAction({ type: 'adjust' })} className="btn-secondary text-sm">Гар засвар</button>
              <button onClick={() => setAction({ type: 'cashout' })} className="btn-secondary text-sm">Бэлнээр буцаах</button>
            </div>
            <div className="divide-y divide-slate-800">
              {(sel.ledger || []).map((r, i) => (
                <div key={i} className="flex items-center justify-between py-2 text-sm">
                  <div>
                    <div>{KIND_LABELS[r.kind] || r.kind}</div>
                    <div className="text-xs text-slate-500">
                      {new Date(r.created_at + 'Z').toLocaleString('mn-MN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}
                      {r.note && ` · ${r.note}`}
                    </div>
                  </div>
                  <div className={r.kind === 'CHARGE_SETTLE' ? 'text-slate-500' : r.direction === 'CREDIT' ? 'text-emerald-400' : 'text-red-400'}>
                    {r.kind === 'CHARGE_SETTLE' ? '' : r.direction === 'CREDIT' ? '+' : '−'}{fmt(r.amount)}₮
                    <span className="text-xs text-slate-500 ml-2">→{fmt(r.balance_after)}₮</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {action && <ActionModal type={action.type} busy={busy}
        onClose={() => setAction(null)} onSave={doAction} />}
    </div>
  )
}

function ActionModal({ type, busy, onClose, onSave }) {
  const [amount, setAmount] = useState('')
  const [direction, setDirection] = useState('CREDIT')
  const [note, setNote] = useState('')
  const isAdjust = type === 'adjust'
  return (
    <Modal open title={isAdjust ? 'Гар засвар' : 'Бэлнээр буцаах'} onClose={onClose}>
      <div className="space-y-3">
        {isAdjust && (
          <Field label="Чиглэл">
            <select className="input" value={direction} onChange={(e) => setDirection(e.target.value)}>
              <option value="CREDIT">Нэмэх (CREDIT)</option>
              <option value="DEBIT">Хасах (DEBIT)</option>
            </select>
          </Field>
        )}
        <Field label="Дүн (₮)" required>
          <input type="number" min="1" className="input" value={amount}
            onChange={(e) => setAmount(e.target.value)} />
        </Field>
        <Field label={isAdjust ? 'Тайлбар (заавал)' : 'Тайлбар'} required={isAdjust}>
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        <button disabled={busy || !amount || (isAdjust && !note)}
          onClick={() => onSave(isAdjust ? { direction, amount: Number(amount), note } : { amount: Number(amount), note })}
          className="btn-primary w-full justify-center">
          {busy ? <Loader2 className="animate-spin" size={18} /> : 'Баталгаажуулах'}
        </button>
      </div>
    </Modal>
  )
}
