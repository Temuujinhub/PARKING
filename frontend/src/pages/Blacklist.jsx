import { Ban, Pencil, Plus, Save, Settings2, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { useAuth } from '../auth'
import { BlockModal, POLICY_LABELS, POLICY_SHORT } from '../components/BlockVehicle'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'
import { PLATE_HINT, clampNum, isPlate, normalizePlate } from '../validation'

// Хар жагсаалтад ЯМАР нөхцөлд машин ордог, орсон машиныг ХЭРХЭН харьцах дүрэм.
// Backend-ийн app_settings-д хадгалагдана — deploy шаардахгүй, админ өөрөө өөрчилнө.
export default function Blacklist() {
  const toast = useToast()
  const { can } = useAuth()
  const canManage = can('blacklist')   // бүгдийг/автоматыг цэвэрлэх — зөвхөн хар жагсаалтын эрхтэй
  const [rows, setRows] = useState([])
  const [editing, setEditing] = useState(null)
  const [blockEdit, setBlockEdit] = useState(null)   // засах: мөр (id, reason, entry_policy…)
  const load = () => api('/api/admin/blacklist').then(setRows)
  useEffect(() => { load() }, [])

  const save = async (e) => {
    e.preventDefault()
    // Стандарт бус дугаарыг (дипломат/тусгай) операторын баталгаажуулалттайгаар л оруулна —
    // алдаатай бичсэн дугаар хар жагсаалтад орвол огт хамаагүй машин хоригдоно.
    if (!isPlate(editing.plate_number)
      && !confirm(`«${editing.plate_number}» стандарт форматад тохирохгүй байна.\n${PLATE_HINT}\n\nТусгай дугаар мөн бол OK дарна уу.`)) return
    if ((editing.reason || '').trim().length < 3) return toast('Шалтгааныг бичнэ үү — дараагийн ажилтанд харагдана', 'error')
    try {
      await api('/api/admin/blacklist', { method: 'POST',
        body: { ...editing, reason: editing.reason.trim(), entry_policy: editing.entry_policy || null } })
      toast('Нэмэгдлээ'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  const clearAuto = async () => {
    const cancelDebts = confirm(
      'Автомат хоригийг цэвэрлэх үү?\n\n«OK» — хориг + доорх төлөгдөөгүй өрийг цуцлах (дахин хар жагсаалтад орохгүй, phantom/тест өрд тохиромжтой).\n«Cancel» — зөвхөн хоригийг авах (өр хэвээр).\n\nБолих бол дараагийн цонхонд Escape дарна.')
    try {
      const r = await api('/api/admin/blacklist/clear', { method: 'POST',
        body: { auto_only: true, cancel_debts: cancelDebts } })
      toast(`${r.deactivated} хориг цэвэрлэв${r.canceled_debts ? `, ${r.canceled_debts} өр цуцлав` : ''}`)
      load()
    } catch (err) { toast(err.message, 'error') }
  }

  // БҮГДИЙГ цэвэрлэх — гараар нэмсэн хоригийг ч. Аудитын дараа «шинээр эхлэх»
  // үед хэрэглэнэ. Гараар нэмсэн хоригийг устгах тул давхар баталгаажуулна.
  const clearAll = async () => {
    const active = rows.filter((b) => b.is_active)
    const manual = active.filter((b) => !/автомат хориг/.test(b.reason || '')).length
    if (!confirm(`Хар жагсаалтыг БҮХЭЛД НЬ цэвэрлэх үү?\n\n`
      + `Идэвхтэй хориг: ${active.length}, үүнээс ГАРААР нэмсэн: ${manual}\n\n`
      + `Гараар нэмсэн хориг нь ихэвчлэн жинхэнэ шалтгаантай байдаг — тэдгээр ч `
      + `чөлөөлөгдөнө.`)) return
    if (manual && !confirm(`Гараар нэмсэн ${manual} хоригийг үнэхээр авах уу?`)) return
    try {
      const r = await api('/api/admin/blacklist/clear', { method: 'POST',
        body: { auto_only: false, cancel_debts: true } })
      toast(`${r.deactivated} хориг цэвэрлэв${r.canceled_debts ? `, ${r.canceled_debts} өр цуцлав` : ''}`)
      load()
    } catch (err) { toast(err.message, 'error') }
  }

  const toggle = async (b) => {
    try {
      await api(`/api/admin/blacklist/${b.id}`, { method: 'PUT', body: { is_active: !b.is_active } })
      load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-2xl font-bold">Хар жагсаалт</h1>
        <div className="flex gap-2">
          {canManage && rows.some((b) => b.is_active && /автомат хориг/.test(b.reason || '')) && (
            <button className="btn-secondary flex items-center gap-1.5" onClick={clearAuto}>
              <Trash2 size={15} /> Автомат хоригийг цэвэрлэх
            </button>
          )}
          {canManage && rows.some((b) => b.is_active) && (
            <button className="btn-secondary flex items-center gap-1.5 border-red-500/40 text-red-400"
              onClick={clearAll} title="Гараар нэмсэн хоригийг ч чөлөөлнө">
              <Trash2 size={15} /> Бүгдийг цэвэрлэх
            </button>
          )}
          <button className="btn-primary" onClick={() => setEditing({ plate_number: '', reason: '', entry_policy: 'DENY' })}>
            <Plus size={16} /> Нэмэх
          </button>
        </div>
      </div>
      <p className="text-sm text-slate-400">Хар жагсаалтын дүрмийг Тохиргоо → Төлбөрийн дүрэм хэсэгт зогсоолоо сонгож өөрчилнө.</p>

      <div className="card py-3 text-sm text-slate-400">
        Хар жагсаалтын машин орох үед операторт <b className="text-red-400">улаан анхааруулга</b> очиж,
        шалтгаан (хэн, хэзээ) ба өрийн дүн харагдана — Кассаас өрийг барагдуулна. Машин тус бүрд
        <b className="text-slate-300"> «Зогсоолд»</b>: нэвтрүүлэхгүй (орох хаалт нээгдэхгүй) / нэвтрүүлж
        анхааруулах / дүрмээр (Тохиргоо → Төлбөрийн дүрэм). Оператор ч Касс, Шалгах хуудаснаас шалтгаантай блоклоно.
      </div>
      <Table headers={['Дугаар', 'Шалтгаан', 'Зогсоолд', 'Нэмсэн', 'Огноо', 'Төлөв', '']} empty={rows.length === 0}>
        {rows.map((b) => (
          <tr key={b.id} className={b.is_active ? '' : 'opacity-60'}>
            <td className="td font-mono font-bold">{b.plate_number}</td>
            <td className="td">
              {b.reason}
              {b.updated_by && (
                <div className="text-[10px] text-slate-500">зассан: {b.updated_by} · {fmtDate(b.updated_at)}</div>
              )}
            </td>
            <td className={`td text-xs ${b.entry_policy === 'DENY' ? 'text-red-300' : b.entry_policy === 'ALLOW' ? 'text-amber-200' : 'text-slate-400'}`}>
              {POLICY_SHORT[b.entry_policy || '']}
            </td>
            <td className="td text-xs">{b.created_by}</td>
            <td className="td font-mono text-xs">{fmtDate(b.created_at)}</td>
            <td className="td"><Badge value={b.is_active ? 'FAILED' : 'CLOSED'} /></td>
            <td className="td text-right whitespace-nowrap">
              {b.is_active && (
                <button className="btn-secondary py-1 text-xs mr-1" title="Шалтгаан / зогсоолд нэвтрүүлэх эсэхийг засах"
                  onClick={() => setBlockEdit(b)}>
                  <Pencil size={13} />
                </button>
              )}
              <button className="btn-secondary py-1 text-xs" onClick={() => toggle(b)}>
                {b.is_active ? 'Идэвхгүй болгох' : 'Идэвхжүүлэх'}
              </button>
            </td>
          </tr>
        ))}
      </Table>

      <BlockModal plate={blockEdit?.plate_number} existing={blockEdit}
        onClose={() => setBlockEdit(null)} onSaved={load} />

      <Modal open={!!editing} onClose={() => setEditing(null)} title="Хар жагсаалтад нэмэх">
        {editing && (
          <form onSubmit={save} className="space-y-3">
            <Field label="Улсын дугаар" required>
              <input className={`input font-mono uppercase${isPlate(editing.plate_number) || !editing.plate_number ? '' : ' input-error'}`}
                value={editing.plate_number} required maxLength={7} placeholder="1234УБА"
                aria-invalid={!!editing.plate_number && !isPlate(editing.plate_number)}
                onChange={(e) => setEditing({ ...editing, plate_number: normalizePlate(e.target.value) })} />
              <div className={editing.plate_number && !isPlate(editing.plate_number) ? 'hint-error' : 'hint'}>
                {editing.plate_number && !isPlate(editing.plate_number) ? `Формат буруу — ${PLATE_HINT}` : PLATE_HINT}
              </div>
            </Field>
            <Field label="Зогсоолд">
              <div className="space-y-1.5">
                {Object.entries(POLICY_LABELS).map(([v, l]) => (
                  <label key={v || 'rule'} className="flex items-center gap-2 text-sm cursor-pointer">
                    <input type="radio" name="bl_policy" checked={(editing.entry_policy || '') === v}
                      onChange={() => setEditing({ ...editing, entry_policy: v })} />
                    <span className={v === 'DENY' ? 'text-red-300' : ''}>{l}</span>
                  </label>
                ))}
              </div>
            </Field>
            <Field label="Шалтгаан (заавал — Касс/Шалгах/POS дээр харагдана)" required>
              <textarea className="input" rows="3" value={editing.reason} required maxLength={500}
                onChange={(e) => setEditing({ ...editing, reason: e.target.value })} />
            </Field>
            <button className="btn-danger w-full justify-center"><Ban size={15} /> Блоклох</button>
          </form>
        )}
      </Modal>
    </div>
  )
}
