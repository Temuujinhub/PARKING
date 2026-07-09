// Ээлж хаах — ажилтны ээлжийн орлого + хаагдах машин + шөнийн хаалт (ээлж хамт хаана)
// + нөхөн төлбөр (өр) хэсэг. 3+ төлөгдөөгүй өртэй дугаар автоматаар хар жагсаалтад орно.
import { Banknote, CreditCard, LogOut, MoonStar, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate, fmtDur } from '../api'
import { useAuth } from '../auth'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

const REASONS = { unpaid_exit: 'Төлбөргүй гаргасан', night_close: 'Шөнийн хаалт', shift_close: 'Ээлж хаалт', manual: 'Гараар' }
const AGE_LABEL = { '0-7': '0–7 хоног', '8-30': '8–30 хоног', '31-90': '31–90 хоног', '90+': '90+ хоног' }
const ROLE_LABEL = { SUPER_ADMIN: 'Супер админ', ADMIN: 'Админ', FINANCE: 'Санхүү', OPERATOR: 'Оператор' }

export default function Compensations() {
  const toast = useToast()
  const { can, user } = useAuth()
  const [data, setData] = useState({ rows: [], total_pending: 0, total_collected: 0, aging: {} })
  const [status, setStatus] = useState('PENDING')
  const [plate, setPlate] = useState('')
  const [payModal, setPayModal] = useState(null) // {comp, method, customer_tin}
  const [shift, setShift] = useState(null)   // одоогийн ээлж
  const [parked, setParked] = useState([])   // зогсоолд байгаа (хаагдах) машинууд
  const [busy, setBusy] = useState(false)
  const canAct = ['OPERATOR', 'SUPER_ADMIN'].includes(user?.role)

  const load = () => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (plate.trim()) params.set('plate', plate.trim())
    api(`/api/compensations?${params}`).then(setData).catch(() => {})
  }
  const loadShift = () => api('/api/cashier/shift/current').then(setShift).catch(() => setShift(null))
  const loadParked = () => api('/api/sessions?status=OPEN,AWAITING_PAYMENT&with_fee=true&limit=200')
    .then((d) => setParked(d.rows || [])).catch(() => {})
  useEffect(load, [status])
  useEffect(() => { loadShift(); loadParked() }, [])

  const doPay = async () => {
    try {
      await api(`/api/compensations/${payModal.comp.id}/pay`, {
        method: 'POST',
        body: { method: payModal.method, customer_tin: payModal.customer_tin || undefined },
      })
      toast('Төлөгдөж, e-Barimt үүслээ'); setPayModal(null); load()
    } catch (e) { toast(e.message, 'error') }
  }

  const cancel = async (c) => {
    const reason = prompt(`${c.plate_number} нэхэмжлэлийг цуцлах шалтгаан:`)
    if (!reason) return
    try { await api(`/api/compensations/${c.id}/cancel`, { method: 'POST', body: { reason } }); toast('Цуцлагдлаа'); load() }
    catch (e) { toast(e.message, 'error') }
  }

  // Шөнийн хаалт = үлдсэн машиныг гаргаж өр үүсгэх + ЭЭЛЖ ХААХ (нэг үйлдэл)
  const nightCloseShift = async () => {
    if (!confirm(`ШӨНИЙН ХААЛТ + ЭЭЛЖ ХААХ:\n\n• Зогсоолд үлдсэн ${parked.length} машиныг гаргаж, төлбөртэйд нь өр (нөхөн төлбөр) үүснэ.\n• Таны ээлж хаагдана.\n\nҮргэлжлүүлэх үү?`)) return
    setBusy(true)
    try {
      const r = await api('/api/cashier/shift/close', {
        method: 'POST',
        body: { close_cars: true, confirmed_cash: shift?.by_provider?.CASH?.amount || 0 },
      })
      toast(`Ээлж хаагдлаа · ${r.closed_cars || 0} машин гаргаж өр үүслээ`)
      loadShift(); loadParked(); load()
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Ээлж хаах</h1>

      {/* 1. Ээлжийн мэдээлэл — ажилтан, өдөр, орлого хэрэгслээр */}
      {shift?.open ? (
        <div className="card space-y-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div>
              <div className="text-lg font-semibold">{shift.shift?.cashier || user?.full_name || user?.username}
                <span className="text-sm text-slate-400 font-normal ml-2">{ROLE_LABEL[user?.role] || ''}</span>
              </div>
              <div className="text-xs text-slate-500">Ээлж нээсэн: {fmtDate(shift.shift?.opened_at)}</div>
            </div>
            <div className="text-right">
              <div className="text-2xl font-bold font-mono text-accent">{fmt(shift.total)}₮</div>
              <div className="text-xs text-slate-500">Ээлжийн нийт орлого · {shift.count} гүйлгээ</div>
            </div>
          </div>
          {/* Орлого төлбөрийн хэрэгслээр — системд бүртгэгдсэнээр */}
          <div className="grid grid-cols-3 gap-3">
            {[['Бэлэн', 'CASH'], ['QPay', 'QPAY'], ['Банкны карт', 'POS']].map(([label, k]) => (
              <div key={k} className="bg-surface-muted/30 rounded-lg p-3 text-center">
                <div className="font-mono font-bold">{fmt(shift.by_provider?.[k]?.amount || 0)}₮</div>
                <div className="text-[11px] text-slate-500">{label} · {shift.by_provider?.[k]?.count || 0}</div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="card text-sm text-slate-500 py-4 text-center">
          Танд нээлттэй ээлж алга. Ээлж нь Касс хуудсанд эсвэл нэвтрэхэд автоматаар нээгддэг.
        </div>
      )}

      {/* 2. Хаагдах машинууд — шөнийн хаалтаар өр болох */}
      <div className="card">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-semibold">Зогсоолд үлдсэн машин (шөнийн хаалтаар өр болно)</h2>
          <span className="text-sm text-slate-400">{parked.length} машин</span>
        </div>
        <Table headers={['Дугаар', 'Зогсоол', 'Орсон', 'Хугацаа', 'Төлбөр', 'Төрөл', 'Төлөв']} empty={parked.length === 0}>
          {parked.map((s) => (
            <tr key={s.id}>
              <td className="td font-mono font-bold">{s.plate_number}</td>
              <td className="td text-xs">{s.site_name || <span className="text-slate-600">—</span>}</td>
              <td className="td font-mono text-xs">{fmtDate(s.entry_time).slice(5, 16)}</td>
              <td className="td font-mono text-xs">{fmtDur(s.fee?.duration_minutes ?? s.duration_minutes)}</td>
              <td className="td font-mono">{s.fee?.is_free ? <span className="text-slate-500">Үнэгүй</span> : `${fmt(s.fee?.total_fee ?? s.total_fee)}₮`}</td>
              <td className="td text-xs">{s.is_registered ? <span className="text-cyan-400">Гэрээт</span> : s.discount_name ? <span className="text-amber-400">{s.discount_name}</span> : 'Энгийн'}</td>
              <td className="td"><Badge value={s.status} /></td>
            </tr>
          ))}
        </Table>
        {canAct && shift?.open && (
          <button className="btn-danger w-full justify-center mt-3 py-3" onClick={nightCloseShift} disabled={busy}>
            <MoonStar size={16} /> {busy ? 'Хааж байна…' : `Шөнийн хаалт — ${parked.length} машин гаргаж, ЭЭЛЖ ХААХ`}
          </button>
        )}
      </div>

      {/* 3. Нөхөн төлбөр (өр) хэсэг */}
      <div className="flex items-center gap-2 pt-2">
        <LogOut size={16} className="text-slate-400" />
        <h2 className="text-lg font-semibold">Нөхөн төлбөр (өр цуглуулах)</h2>
      </div>
      <div className="card py-3 text-sm text-slate-400">
        Төлбөргүй гарсан/хаагдсан машинд өр үүсдэг. Дараагийн ирэлтэд нь касс дээр
        <b className="text-red-400"> улаанаар</b> тэмдэглэгдэж, <b className="text-slate-200">3+ төлөгдөөгүй</b> дугаар
        автоматаар хар жагсаалтад орно.
      </div>

      <div className="card grid grid-cols-2 md:grid-cols-4 gap-3 items-end">
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Төлөв">
          <option value="PENDING">Төлөгдөөгүй</option>
          <option value="PAID">Төлөгдсөн</option>
          <option value="CANCELLED">Цуцлагдсан</option>
          <option value="">Бүгд</option>
        </select>
        <div className="flex gap-2 col-span-2">
          <input className="input font-mono" placeholder="Дугаараар хайх…" value={plate}
            onChange={(e) => setPlate(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === 'Enter' && load()} />
          <button className="btn-secondary" onClick={load} aria-label="Хайх"><Search size={15} /></button>
        </div>
        <div className="text-sm text-right">
          Нийт авлага: <b className="font-mono text-red-400">{fmt(data.total_pending)}₮</b>
          <div className="text-xs text-slate-500">Цугларсан: {fmt(data.total_collected)}₮</div>
        </div>
      </div>

      {/* Өрийн настжуулалт (aging) — хугацаагаар */}
      {data.aging && Object.values(data.aging).some((v) => v > 0) && (
        <div className="card py-3 grid grid-cols-2 md:grid-cols-4 gap-3">
          {['0-7', '8-30', '31-90', '90+'].map((b) => (
            <div key={b} className="text-center">
              <div className={`font-mono font-bold ${b === '90+' ? 'text-red-400' : b === '31-90' ? 'text-amber-400' : 'text-slate-200'}`}>
                {fmt(data.aging[b] || 0)}₮
              </div>
              <div className="text-[11px] text-slate-500">{AGE_LABEL[b]}</div>
            </div>
          ))}
        </div>
      )}

      <Table headers={['Дугаар', 'Зогсоол', 'Дүн', 'Шалтгаан', 'Нас', 'Огноо', 'Төлөв', '']}
        empty={data.rows.length === 0}>
        {data.rows.map((c) => (
          <tr key={c.id} className={c.status === 'PENDING' ? 'bg-red-500/5' : ''}>
            <td className="td font-mono font-bold text-red-400">
              {c.plate_number}{c.pending_count >= 3 && <span className="ml-1 text-[10px] bg-red-500/20 text-red-400 px-1 rounded">хориг</span>}
            </td>
            <td className="td">{c.site_name}</td>
            <td className="td font-mono font-semibold">{fmt(c.amount)}₮</td>
            <td className="td text-xs">{REASONS[c.reason] || c.reason}</td>
            <td className={`td text-xs font-mono ${c.age_bucket === '90+' ? 'text-red-400' : c.age_bucket === '31-90' ? 'text-amber-400' : 'text-slate-400'}`}>
              {c.days_old}х
            </td>
            <td className="td font-mono text-xs">{fmtDate(c.created_at)}</td>
            <td className="td"><Badge value={c.status === 'PENDING' ? 'FAILED' : c.status === 'PAID' ? 'PAID' : 'CLOSED'} /></td>
            <td className="td text-right whitespace-nowrap">
              {c.status === 'PENDING' && (
                <>
                  <button className="btn-primary py-1 text-xs mr-1"
                    onClick={() => setPayModal({ comp: c, method: 'CASH', customer_tin: '' })}>
                    <Banknote size={13} /> Төлүүлэх
                  </button>
                  {can('settings') && (
                    <button className="btn-secondary py-1 text-xs" onClick={() => cancel(c)}>Цуцлах</button>
                  )}
                </>
              )}
            </td>
          </tr>
        ))}
      </Table>

      {/* Төлүүлэх — хэрэгсэл сонгож e-Barimt өгнө */}
      <Modal open={!!payModal} onClose={() => setPayModal(null)} title="Нөхөн төлбөр төлүүлэх">
        {payModal && (
          <div className="space-y-4">
            <div className="text-center">
              <div className="font-mono text-2xl font-bold">{payModal.comp.plate_number}</div>
              <div className="text-3xl font-bold text-accent mt-1">{fmt(payModal.comp.amount)}₮</div>
            </div>
            <Field label="Төлбөрийн хэрэгсэл">
              <div className="grid grid-cols-2 gap-2">
                {[['CASH', 'Бэлэн', Banknote], ['CARD', 'Банкны карт', CreditCard]].map(([v, l, Icon]) => (
                  <button key={v} type="button" onClick={() => setPayModal({ ...payModal, method: v })}
                    className={`px-3 py-2.5 rounded-xl text-sm font-medium border flex items-center justify-center gap-2 cursor-pointer
                      ${payModal.method === v ? 'bg-accent text-white border-accent' : 'bg-surface-muted/40 text-slate-300 border-surface-border'}`}>
                    <Icon size={16} /> {l}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Байгууллагын ТТД (сонголт — ААН баримт)">
              <input className="input font-mono" inputMode="numeric" placeholder="Хоосон = иргэн"
                value={payModal.customer_tin} maxLength={14}
                onChange={(e) => setPayModal({ ...payModal, customer_tin: e.target.value.replace(/\D/g, '') })} />
            </Field>
            <button onClick={doPay} className="btn-primary w-full justify-center py-3">
              Төлүүлж хаах ({payModal.method === 'CASH' ? 'бэлэн' : 'карт'}) + e-Barimt
            </button>
          </div>
        )}
      </Modal>
    </div>
  )
}
