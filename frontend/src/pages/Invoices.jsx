// Байгууллагын сарын нэхэмжлэл — авто/гар үүсгэлт, Excel, и-мэйл илгээлт, төлөлт хөтлөлт.
// Урсгал: сар бүрийн 1-нд өмнөх сарынх автоматаар DRAFT үүснэ (эсвэл «Үүсгэх» товч)
// → Excel-ээ хянаад «Илгээх» (и-мэйл хавсралттай) → төлөгдмөгц «Төлөгдсөн» тэмдэглэнэ.
import { Download, FileText, Mail, RefreshCw, Settings2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt } from '../api'
import { Field, Modal, Table, useToast } from '../components/ui'
import { useDownload } from '../hooks/useDownload'

const STATUS = {
  DRAFT: ['Ноорог', 'bg-slate-500/20 text-slate-300'],
  SENT: ['Илгээсэн', 'bg-blue-500/15 text-blue-400'],
  PAID: ['Төлөгдсөн', 'bg-accent/15 text-accent'],
  CANCELLED: ['Цуцалсан', 'bg-red-500/15 text-red-400'],
}
const hm = (m) => m ? `${Math.floor(m / 60)}ц ${String(m % 60).padStart(2, '0')}м` : '0м'
const MODES = { POSTPAID: 'Сарын эцэст', PREPAID: 'Урьдчилгаа', NONE: 'Нэхэмжлэхгүй' }
const prevMonth = () => {
  const d = new Date(); d.setDate(1); d.setDate(0)
  return d.toISOString().slice(0, 7)
}

function ContactModal({ inv, onClose, onDone }) {
  const toast = useToast()
  const [f, setF] = useState({ email: '', billing_mode: 'POSTPAID' })
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (inv) setF({ email: inv.company_email || '', billing_mode: inv.billing_mode || 'POSTPAID' })
  }, [inv])
  if (!inv) return null
  const save = async (e) => {
    e.preventDefault(); setBusy(true)
    try {
      await api('/api/invoices/contacts', { method: 'POST',
        body: { company: inv.company, email: f.email, billing_mode: f.billing_mode } })
      toast('Хадгалагдлаа'); onClose(); onDone()
    } catch (err) { toast(err.message, 'error') } finally { setBusy(false) }
  }
  return (
    <Modal open title={`${inv.company} — төлбөрийн тохиргоо`} onClose={onClose}>
      <form onSubmit={save} className="space-y-4">
        <Field label="Төлбөрийн горим">
          <select className="input" value={f.billing_mode}
            onChange={(e) => setF({ ...f, billing_mode: e.target.value })}>
            <option value="POSTPAID">Сарын эцэст — өмнөх сарын нэхэмжлэл 1-нд авто үүснэ</option>
            <option value="PREPAID">Урьдчилгаа — тухайн сарын нэхэмжлэл 1-нд авто үүснэ</option>
            <option value="NONE">Нэхэмжлэхгүй — зөвхөн бүртгэл (нэхэмжлэл огт үүсгэхгүй)</option>
          </select>
        </Field>
        <Field label="И-мэйл">
          <input className="input" type="email" value={f.email}
            onChange={(e) => setF({ ...f, email: e.target.value })} placeholder="finance@company.mn" />
        </Field>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={onClose}>Болих</button>
          <button className="btn-primary" disabled={busy}>Хадгалах</button>
        </div>
      </form>
    </Modal>
  )
}

function SendModal({ inv, onClose, onDone }) {
  const toast = useToast()
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { setEmail(inv?.company_email || inv?.sent_to || '') }, [inv])
  if (!inv) return null
  const send = async (e) => {
    e.preventDefault(); setBusy(true)
    try {
      await api(`/api/invoices/${inv.id}/send`, { method: 'POST', body: { email } })
      toast(`${inv.company} руу илгээгдлээ`)
      onClose(); onDone()
    } catch (err) { toast(err.message, 'error') } finally { setBusy(false) }
  }
  return (
    <Modal open title={`${inv.company} — нэхэмжлэл илгээх`} onClose={onClose}>
      <form onSubmit={send} className="space-y-4">
        <div className="text-sm text-slate-400">
          {inv.invoice_no} · {inv.period} · <b className="text-accent font-mono">{fmt(inv.amount)}₮</b> ·
          Excel задаргаа хавсаргагдана. И-мэйл нь байгууллагад хадгалагдаж дараагийн сард автоматаар бөглөгдөнө.
        </div>
        <Field label="Хүлээн авах и-мэйл" required>
          <input className="input" type="email" value={email}
            onChange={(e) => setEmail(e.target.value)} required placeholder="finance@company.mn" />
        </Field>
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={onClose}>Болих</button>
          <button className="btn-primary flex items-center gap-1.5" disabled={busy}>
            <Mail size={15} /> {busy ? 'Илгээж байна…' : 'Илгээх'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default function Invoices() {
  const toast = useToast()
  const download = useDownload()
  const [period, setPeriod] = useState(prevMonth())
  const [rows, setRows] = useState([])
  const [sending, setSending] = useState(null)
  const [editing, setEditing] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = () => api(`/api/invoices?period=${period}`).then(setRows).catch((e) => toast(e.message, 'error'))
  useEffect(() => { load() }, [period])

  const generate = async () => {
    setBusy(true)
    try {
      const r = await api('/api/invoices/generate', { method: 'POST', body: { period } })
      toast(r.created ? `${r.created} байгууллагад нэхэмжлэл үүслээ` : 'Энэ сард бүгд үүсчихсэн байна')
      load()
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }
  const markPaid = async (inv) => {
    if (!confirm(`${inv.company} — ${fmt(inv.amount)}₮ төлөгдсөн гэж тэмдэглэх үү?`)) return
    try { await api(`/api/invoices/${inv.id}/paid`, { method: 'POST', body: {} }); load() } catch (e) { toast(e.message, 'error') }
  }
  const cancel = async (inv) => {
    if (!confirm(`${inv.invoice_no}-ийг цуцлах уу?`)) return
    try { await api(`/api/invoices/${inv.id}/cancel`, { method: 'POST', body: {} }); load() } catch (e) { toast(e.message, 'error') }
  }

  const total = rows.filter((r) => r.status !== 'CANCELLED').reduce((a, r) => a + +r.amount, 0)
  const paid = rows.filter((r) => r.status === 'PAID').reduce((a, r) => a + +r.amount, 0)

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold flex items-center gap-2"><FileText className="text-accent" /> Нэхэмжлэл</h1>
        <div className="flex items-center gap-2">
          <input type="month" className="input w-44" value={period}
            onChange={(e) => setPeriod(e.target.value)} aria-label="Сар сонгох" />
          <button className="btn-primary flex items-center gap-1.5" onClick={generate} disabled={busy}>
            <RefreshCw size={15} className={busy ? 'animate-spin' : ''} /> Үүсгэх
          </button>
        </div>
      </div>
      <p className="text-sm text-slate-400 max-w-3xl">
        Гэрээт байгууллага бүрд сарын хураамжийн нэхэмжлэл (машин тус бүрийн задаргаа + тухайн
        сарын бодит ашиглалттай). Сар бүрийн 1-нд өмнөх сарынх автоматаар үүснэ.
      </p>
      <div className="flex flex-wrap gap-6 text-sm text-slate-400">
        <span>Нийт: <b className="font-mono text-slate-200">{fmt(total)}₮</b></span>
        <span>Төлөгдсөн: <b className="font-mono text-accent">{fmt(paid)}₮</b></span>
        <span>Үлдэгдэл: <b className="font-mono text-amber-400">{fmt(total - paid)}₮</b></span>
      </div>
      <div className="card p-0 overflow-hidden">
        <Table headers={['№', 'Байгууллага', 'Машин', 'Дүн (₮)', 'Ашиглалт', 'Төлөв', 'Илгээсэн', '']}
          empty={rows.length === 0}>
          {rows.map((r) => {
            const [label, cls] = STATUS[r.status] || [r.status, '']
            return (
              <tr key={r.id} className="hover:bg-surface-muted/40 transition-colors">
                <td className="td font-mono text-xs">{r.invoice_no}</td>
                <td className="td font-medium">
                  {r.company}
                  <div className="text-[10px] text-slate-500">{MODES[r.billing_mode] || r.billing_mode}</div>
                </td>
                <td className="td font-mono">{r.car_count}</td>
                <td className="td font-mono text-accent font-semibold">{fmt(r.amount)}</td>
                <td className="td font-mono text-xs">{r.sessions} удаа · {hm(r.minutes)}</td>
                <td className="td"><span className={`px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}>{label}</span></td>
                <td className="td text-xs text-slate-400">{r.sent_to || '—'}</td>
                <td className="td">
                  <div className="flex gap-1.5 justify-end">
                    <button className="btn-secondary text-xs py-1 flex items-center gap-1" title="Excel татах"
                      onClick={() => download(`/api/invoices/${r.id}/excel`, `${r.invoice_no}.xlsx`)}>
                      <Download size={13} /></button>
                    <button className="btn-secondary text-xs py-1" title="Горим/и-мэйл тохируулах"
                      onClick={() => setEditing(r)}><Settings2 size={13} /></button>
                    {r.status !== 'CANCELLED' && (
                      <button className="btn-secondary text-xs py-1 flex items-center gap-1"
                        onClick={() => setSending(r)}><Mail size={13} /> Илгээх</button>
                    )}
                    {(r.status === 'DRAFT' || r.status === 'SENT') && (
                      <button className="btn-primary text-xs py-1" onClick={() => markPaid(r)}>Төлөгдсөн</button>
                    )}
                    {r.status !== 'PAID' && r.status !== 'CANCELLED' && (
                      <button className="text-xs text-slate-500 hover:text-red-400 px-1 cursor-pointer"
                        onClick={() => cancel(r)}>Цуцлах</button>
                    )}
                  </div>
                </td>
              </tr>
            )
          })}
        </Table>
      </div>
      <SendModal inv={sending} onClose={() => setSending(null)} onDone={load} />
      <ContactModal inv={editing} onClose={() => setEditing(null)} onDone={load} />
    </div>
  )
}
