// Дундын UI компонентууд
import { X } from 'lucide-react'
import { useEffect } from 'react'

export function Modal({ open, onClose, title, children, wide }) {
  useEffect(() => {
    const h = (e) => e.key === 'Escape' && onClose()
    if (open) window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60" onClick={onClose}>
      <div className={`card w-full ${wide ? 'max-w-3xl' : 'max-w-lg'} max-h-[90vh] overflow-y-auto`}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button onClick={onClose} className="p-1 rounded hover:bg-surface-muted cursor-pointer" aria-label="Хаах">
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

const badgeColors = {
  OPEN: 'bg-blue-500/15 text-blue-400',
  AWAITING_PAYMENT: 'bg-amber-500/15 text-amber-400',
  PAID: 'bg-accent/15 text-accent',
  CLOSED: 'bg-slate-500/15 text-slate-400',
  FREE: 'bg-cyan-500/15 text-cyan-400',
  MANUAL_CLOSED: 'bg-purple-500/15 text-purple-400',
  PENDING: 'bg-amber-500/15 text-amber-400',
  FAILED: 'bg-red-500/15 text-red-400',
  SUCCESS: 'bg-accent/15 text-accent',
  SENT: 'bg-accent/15 text-accent',
  REVIEW: 'bg-red-500/15 text-red-400',
  active: 'bg-accent/15 text-accent',
}
const badgeLabels = {
  OPEN: 'Зогсож байна', AWAITING_PAYMENT: 'Төлбөр хүлээж буй', PAID: 'Төлсөн',
  CLOSED: 'Гарсан', FREE: 'Үнэгүй гарсан', MANUAL_CLOSED: 'Гараар хаасан',
  PENDING: 'Хүлээгдэж буй', FAILED: 'Амжилтгүй', SUCCESS: 'Амжилттай', SENT: 'Илгээсэн',
  REVIEW: 'Шалгах шаардлагатай', active: 'Идэвхтэй',
}

export function Badge({ value }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded-md text-xs font-medium ${badgeColors[value] || 'bg-slate-500/15 text-slate-300'}`}>
      {badgeLabels[value] || value}
    </span>
  )
}

export function StatCard({ icon: Icon, label, value, sub, color = 'text-accent' }) {
  return (
    <div className="card flex items-start gap-4">
      <div className={`p-2.5 rounded-lg bg-surface-muted ${color}`}><Icon size={22} /></div>
      <div className="min-w-0">
        <div className="text-xs text-slate-400">{label}</div>
        <div className="text-2xl font-bold font-mono tabular-nums">{value}</div>
        {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
      </div>
    </div>
  )
}

export function Table({ headers, children, empty }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-surface-border/60">
      <table className="w-full bg-surface-card">
        <thead className="bg-surface-muted/50 border-b border-surface-border/60">
          <tr>{headers.map((h, i) => <th key={i} className="th">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-surface-border/40">
          {children}
          {empty && (
            <tr><td colSpan={headers.length} className="td text-center text-slate-500 py-8">
              Мэдээлэл байхгүй байна
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export function Field({ label, children, required }) {
  return (
    <div>
      <label className="label">{label}{required && <span className="text-red-400"> *</span>}</label>
      {children}
    </div>
  )
}

export function useToast() { return window.__toast }

export function ToastHost() {
  // маш хөнгөн toast
  useEffect(() => {
    window.__toast = (msg, type = 'success') => {
      const el = document.createElement('div')
      el.textContent = msg
      el.setAttribute('role', 'status')
      el.className = `fixed bottom-5 right-5 z-[100] px-4 py-3 rounded-lg text-sm font-medium shadow-lg
        ${type === 'error' ? 'bg-red-600 text-white' : 'bg-accent text-slate-900'}`
      document.body.appendChild(el)
      setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s' }, 3200)
      setTimeout(() => el.remove(), 3600)
    }
  }, [])
  return null
}
