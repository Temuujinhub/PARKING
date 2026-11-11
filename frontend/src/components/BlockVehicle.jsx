// Машин блоклох (2026-11-11): машин тус бүрд «нэвтрүүлэх / нэвтрүүлэхгүй» + ШАЛТГААН (заавал).
// Шалтгаан нь Касс/Шалгах/POS-ын мөрөнд харагдана — дараагийн ээлжийн ажилтан ЯАГААД гэдгийг мэднэ.
// Оператор ч блоклоно (backend: blacklist | cashier | check эрх), хар жагсаалт бүх зогсоолд үйлчилнэ.
import { Ban } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { Field, Modal, useToast } from './ui'

export const POLICY_LABELS = {
  DENY: 'Нэвтрүүлэхгүй (орох хаалт нээгдэхгүй)',
  ALLOW: 'Нэвтрүүлж операторт анхааруулна',
  '': 'Хар жагсаалтын дүрмээр (Тохиргоо → Төлбөрийн дүрэм)',
}
export const POLICY_SHORT = { DENY: 'нэвтрүүлэхгүй', ALLOW: 'нэвтрүүлнэ', '': 'дүрмээр' }

export function blockTitle(b) {
  if (!b) return ''
  const who = [b.created_by, b.created_at && fmtDate(b.created_at)].filter(Boolean).join(', ')
  const upd = b.updated_by ? ` · зассан: ${b.updated_by}${b.updated_at ? `, ${fmtDate(b.updated_at)}` : ''}` : ''
  return `Блок (${POLICY_SHORT[b.entry_policy || ''] || 'дүрмээр'}): ${b.reason || '—'}${who ? ` — ${who}` : ''}${upd}`
}

// Мөрөнд: улаан «Блок» тэмдэг + шалтгаан (бүтэн текст нь title-д)
export function BlockBadge({ blacklist, inline = true }) {
  if (!blacklist) return null
  return (
    <div className="mt-0.5 font-sans font-normal" title={blockTitle(blacklist)}>
      <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded ${blacklist.entry_policy === 'DENY'
        ? 'bg-red-500/25 text-red-300' : 'bg-amber-500/20 text-amber-200'}`}>
        <Ban size={10} /> Блок{blacklist.entry_policy === 'DENY' ? ' · нэвтрүүлэхгүй' : ''}
      </span>
      {inline && blacklist.reason && (
        <span className="ml-1 text-[11px] text-red-300/90 break-words">{blacklist.reason}</span>
      )}
    </div>
  )
}

// Блоклох / засах / цуцлах цонх. existing = мөрийн `blacklist` (id-тай) эсвэл null.
export function BlockModal({ plate, existing, onClose, onSaved }) {
  const toast = useToast()
  const [form, setForm] = useState({ reason: '', entry_policy: 'DENY', note: '' })
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (plate) setForm({ reason: existing?.reason || '', entry_policy: existing ? (existing.entry_policy || '') : 'DENY', note: '' })
  }, [plate, existing?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (e) => {
    e.preventDefault()
    if (form.reason.trim().length < 3) return toast('Шалтгааныг бичнэ үү — дараагийн ажилтанд харагдана', 'error')
    setBusy(true)
    try {
      await api('/api/admin/blacklist', { method: 'POST',
        body: { plate_number: plate, reason: form.reason.trim(), entry_policy: form.entry_policy || null } })
      toast(existing ? 'Блок шинэчлэгдлээ' : `${plate} блоклогдлоо`)
      onSaved?.(); onClose()
    } catch (err) { toast(err.message, 'error') } finally { setBusy(false) }
  }
  const unblock = async () => {
    if (!existing?.id) return
    if (!confirm(`${plate} машины блокийг цуцлах уу?`)) return
    setBusy(true)
    try {
      await api(`/api/admin/blacklist/${existing.id}`, { method: 'PUT',
        body: { is_active: false, note: form.note.trim() || undefined } })
      toast('Блок цуцлагдлаа')
      onSaved?.(); onClose()
    } catch (err) { toast(err.message, 'error') } finally { setBusy(false) }
  }

  return (
    <Modal open={!!plate} onClose={onClose} title={existing ? `Блок — ${plate}` : `Машин блоклох — ${plate}`}>
      {plate && (
        <form onSubmit={save} className="space-y-3">
          {existing && (
            <div className="text-xs text-slate-400">{blockTitle(existing)}</div>
          )}
          <Field label="Зогсоолд">
            <div className="space-y-1.5">
              {Object.entries(POLICY_LABELS).map(([v, l]) => (
                <label key={v || 'rule'} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input type="radio" name="entry_policy" checked={form.entry_policy === v}
                    onChange={() => setForm({ ...form, entry_policy: v })} />
                  <span className={v === 'DENY' ? 'text-red-300' : ''}>{l}</span>
                </label>
              ))}
            </div>
          </Field>
          <Field label="Шалтгаан (заавал — дараагийн ээлжийн ажилтанд харагдана)" required>
            <textarea className="input" rows="3" maxLength={500} value={form.reason} required
              placeholder="ж: төлбөр төлөхгүй хаалт мөргөж гарсан — менежертэй уулзуулна"
              onChange={(e) => setForm({ ...form, reason: e.target.value })} />
          </Field>
          <button className="btn-danger w-full justify-center" disabled={busy}>
            <Ban size={15} /> {existing ? 'Хадгалах' : 'Блоклох'}
          </button>
          {existing && (
            <div className="pt-2 border-t border-surface-border/60 space-y-2">
              <input className="input" value={form.note} maxLength={300}
                placeholder="Блок цуцлах шалтгаан (заавал биш, аудитад үлдэнэ)"
                onChange={(e) => setForm({ ...form, note: e.target.value })} />
              <button type="button" className="btn-secondary w-full justify-center" disabled={busy} onClick={unblock}>
                Блок цуцлах
              </button>
            </div>
          )}
        </form>
      )}
    </Modal>
  )
}
