import { Plus, Save, Settings2, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

// Хар жагсаалтад ЯМАР нөхцөлд машин ордог, орсон машиныг ХЭРХЭН харьцах дүрэм.
// Backend-ийн app_settings-д хадгалагдана — deploy шаардахгүй, админ өөрөө өөрчилнө.
function RulesCard({ toast }) {
  const [rules, setRules] = useState(null)
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)
  useEffect(() => { api('/api/admin/blacklist/rules').then(setRules).catch(() => {}) }, [])

  const save = async () => {
    setSaving(true)
    try {
      setRules(await api('/api/admin/blacklist/rules', { method: 'PUT', body: rules }))
      toast('Дүрэм хадгалагдлаа — шинэ event-үүдэд шууд үйлчилнэ')
    } catch (e) { toast(e.message, 'error') } finally { setSaving(false) }
  }
  if (!rules) return null
  const set = (k, v) => setRules({ ...rules, [k]: v })

  return (
    <div className="card space-y-3">
      <button className="flex items-center justify-between w-full text-left"
        onClick={() => setOpen((o) => !o)}>
        <span className="font-semibold flex items-center gap-2">
          <Settings2 size={16} className="text-accent" /> Дүрэм — хэзээ хар жагсаалтад орох, хэрхэн харьцах
        </span>
        <span className="text-xs text-slate-400">
          {rules.auto_enabled
            ? `авто: ${rules.debt_count || '—'} өр${rules.debt_amount ? ` эсвэл ${rules.debt_amount.toLocaleString()}₮` : ''}`
            : 'авто хориг унтраалттай'} · {open ? 'хаах' : 'нээх'}
        </span>
      </button>

      {open && (
        <div className="space-y-4 pt-1">
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={rules.auto_enabled}
              onChange={(e) => set('auto_enabled', e.target.checked)} />
            <span>Төлбөргүй гарсан машиныг <b>автоматаар</b> хар жагсаалтад оруулах
              <span className="block text-xs text-slate-500">Унтраавал зөвхөн гараар нэмсэн машин л жагсаалтад орно.</span>
            </span>
          </label>

          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Хэдэн удаагийн өр хурамагц (0 = хэрэглэхгүй)">
              <input className="input font-mono" type="number" min="0" value={rules.debt_count}
                disabled={!rules.auto_enabled}
                onChange={(e) => set('debt_count', Number(e.target.value))} />
            </Field>
            <Field label="Эсвэл нийт өр энэ дүнд хүрвэл (₮, 0 = хэрэглэхгүй)">
              <input className="input font-mono" type="number" min="0" step="1000" value={rules.debt_amount}
                disabled={!rules.auto_enabled}
                onChange={(e) => set('debt_amount', Number(e.target.value))} />
            </Field>
          </div>

          <div className="border-t border-surface-border/60 pt-3 space-y-3">
            <label className="flex items-start gap-2 text-sm cursor-pointer">
              <input type="checkbox" className="mt-0.5" checked={rules.block_entry}
                onChange={(e) => set('block_entry', e.target.checked)} />
              <span>Хар жагсаалтын машиныг <b>зогсоолд оруулахгүй</b> (орох хаалт нээгдэхгүй)
                <span className="block text-xs text-slate-500">
                  Унтраасан (зөвлөмж) үед машин ОРНО, харин операторт улаан анхааруулга гарч
                  өрийг нь гарахад авах боломжтой болно — гадаа үлдээвэл өр хэзээ ч төлөгддөггүй.
                </span>
              </span>
            </label>
            <Field label="Гарахад саатуулах: хэдэн өртэй бол хаалт автоматаар нээхгүй вэ (0 = саатуулахгүй)">
              <input className="input font-mono w-40" type="number" min="0"
                value={rules.block_exit_debt_count}
                onChange={(e) => set('block_exit_debt_count', Number(e.target.value))} />
            </Field>
          </div>

          <button className="btn-primary" onClick={save} disabled={saving}>
            <Save size={15} /> {saving ? 'Хадгалж байна…' : 'Дүрмийг хадгалах'}
          </button>
        </div>
      )}
    </div>
  )
}

export default function Blacklist() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [editing, setEditing] = useState(null)
  const load = () => api('/api/admin/blacklist').then(setRows)
  useEffect(() => { load() }, [])

  const save = async (e) => {
    e.preventDefault()
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
          <button className="btn-primary" onClick={() => setEditing({ plate_number: '', reason: '' })}>
            <Plus size={16} /> Нэмэх
          </button>
        </div>
      </div>
      <RulesCard toast={toast} />

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
              <input className="input font-mono" value={editing.plate_number} required
                onChange={(e) => setEditing({ ...editing, plate_number: e.target.value.toUpperCase() })} />
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
