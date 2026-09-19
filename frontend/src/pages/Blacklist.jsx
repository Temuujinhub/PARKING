import { Plus, Save, Settings2, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'
import { PLATE_HINT, clampNum, isPlate, normalizePlate } from '../validation'

// Хар жагсаалтад ЯМАР нөхцөлд машин ордог, орсон машиныг ХЭРХЭН харьцах дүрэм.
// Backend-ийн app_settings-д хадгалагдана — deploy шаардахгүй, админ өөрөө өөрчилнө.
export default function Blacklist() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [editing, setEditing] = useState(null)
  const load = () => api('/api/admin/blacklist').then(setRows)
  useEffect(() => { load() }, [])

  const save = async (e) => {
    e.preventDefault()
    // Стандарт бус дугаарыг (дипломат/тусгай) операторын баталгаажуулалттайгаар л оруулна —
    // алдаатай бичсэн дугаар хар жагсаалтад орвол огт хамаагүй машин хоригдоно.
    if (!isPlate(editing.plate_number)
      && !confirm(`«${editing.plate_number}» стандарт форматад тохирохгүй байна.\n${PLATE_HINT}\n\nТусгай дугаар мөн бол OK дарна уу.`)) return
    try {
      await api('/api/admin/blacklist', { method: 'POST', body: editing })
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
          {rows.some((b) => b.is_active && /автомат хориг/.test(b.reason || '')) && (
            <button className="btn-secondary flex items-center gap-1.5" onClick={clearAuto}>
              <Trash2 size={15} /> Автомат хоригийг цэвэрлэх
            </button>
          )}
          {rows.some((b) => b.is_active) && (
            <button className="btn-secondary flex items-center gap-1.5 border-red-500/40 text-red-400"
              onClick={clearAll} title="Гараар нэмсэн хоригийг ч чөлөөлнө">
              <Trash2 size={15} /> Бүгдийг цэвэрлэх
            </button>
          )}
          <button className="btn-primary" onClick={() => setEditing({ plate_number: '', reason: '' })}>
            <Plus size={16} /> Нэмэх
          </button>
        </div>
      </div>
      <p className="text-sm text-slate-400">Хар жагсаалтын дүрмийг Тохиргоо → Төлбөрийн дүрэм хэсэгт зогсоолоо сонгож өөрчилнө.</p>

      <div className="card py-3 text-sm text-slate-400">
        Хар жагсаалтын машин орох үед операторт <b className="text-red-400">улаан анхааруулга</b> очиж,
        өрийн дүн харагдана — Кассаас өрийг барагдуулна. Хаалтыг бүрмөсөн хаах эсэхийг
        дээрх <b className="text-slate-300">Дүрэм</b> хэсгээс тохируулна.
      </div>
      <Table headers={['Дугаар', 'Шалтгаан', 'Нэмсэн', 'Огноо', 'Төлөв', '']} empty={rows.length === 0}>
        {rows.map((b) => (
          <tr key={b.id}>
            <td className="td font-mono font-bold">{b.plate_number}</td>
            <td className="td">{b.reason}</td>
            <td className="td text-xs">{b.created_by}</td>
            <td className="td font-mono text-xs">{fmtDate(b.created_at)}</td>
            <td className="td"><Badge value={b.is_active ? 'FAILED' : 'CLOSED'} /></td>
            <td className="td text-right">
              <button className="btn-secondary py-1 text-xs" onClick={() => toggle(b)}>
                {b.is_active ? 'Идэвхгүй болгох' : 'Идэвхжүүлэх'}
              </button>
            </td>
          </tr>
        ))}
      </Table>

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
            <Field label="Шалтгаан">
              <textarea className="input" rows="3" value={editing.reason}
                onChange={(e) => setEditing({ ...editing, reason: e.target.value })} />
            </Field>
            <button className="btn-danger w-full justify-center">Нэмэх</button>
          </form>
        )}
      </Modal>
    </div>
  )
}
