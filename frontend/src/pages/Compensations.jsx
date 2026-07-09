// Нөхөн төлбөр — төлбөргүй гарсан машины нэхэмжлэл (JGA спек)
// 3+ төлөгдөөгүй нэхэмжлэлтэй дугаар автоматаар хар жагсаалтад орно.
import { Banknote, CreditCard, MoonStar, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate } from '../api'
import { useAuth } from '../auth'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

const REASONS = { unpaid_exit: 'Төлбөргүй гаргасан', night_close: 'Шөнийн хаалт', shift_close: 'Ээлж хаалт', manual: 'Гараар' }
const AGE_LABEL = { '0-7': '0–7 хоног', '8-30': '8–30 хоног', '31-90': '31–90 хоног', '90+': '90+ хоног' }

export default function Compensations() {
  const toast = useToast()
  const { can } = useAuth()
  const [data, setData] = useState({ rows: [], total_pending: 0, total_collected: 0, aging: {} })
  const [status, setStatus] = useState('PENDING')
  const [plate, setPlate] = useState('')
  const [payModal, setPayModal] = useState(null) // {comp, method, customer_tin}

  const load = () => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (plate.trim()) params.set('plate', plate.trim())
    api(`/api/compensations?${params}`).then(setData).catch(() => {})
  }
  useEffect(load, [status])

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

  const nightClose = async () => {
    if (!confirm('ШӨНИЙН ХААЛТ: Зогсоолд байгаа БҮХ машиныг гаргаж, төлбөртэй нь нөхөн төлбөрийн нэхэмжлэлтэй болно.\n\nЭнэ үйлдлийг БУЦААХ БОЛОМЖГҮЙ. Үргэлжлүүлэх үү?')) return
    try {
      const r = await api('/api/compensations/night-close', { method: 'POST', body: {} })
      toast(`${r.closed_sessions} машин гаргаж, ${r.compensations_created} нэхэмжлэл үүслээ`)
      load()
    } catch (e) { toast(e.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Нөхөн төлбөр</h1>
        {can('settings') && (
          <button className="btn-danger" onClick={nightClose}
            title="00 цагийн хаалт — бүх машиныг гаргаж нэхэмжлэл үүсгэнэ">
            <MoonStar size={15} /> Шөнийн хаалт
          </button>
        )}
      </div>

      <div className="card py-3 text-sm text-slate-400">
        Төлбөргүй гарсан машинд нэхэмжлэл үүсдэг (Касс → Гараар гаргах → "нөхөн төлбөр үүсгэх").
        Дараагийн ирэлтэд нь касс дээр <b className="text-red-400">улаанаар</b> тэмдэглэгдэж,
        <b className="text-slate-200"> 3+ төлөгдөөгүй</b> нэхэмжлэлтэй дугаар автоматаар хар жагсаалтад орно.
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
