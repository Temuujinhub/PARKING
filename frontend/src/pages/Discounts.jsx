import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt } from '../api'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

const TYPES = { PERCENT: 'Хувиар (%)', FIXED: 'Тогтмол дүн (₮)', FREE_MINUTES: 'Үнэгүй минут' }

export default function Discounts() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [editing, setEditing] = useState(null)
  const load = () => api('/api/admin/discounts').then(setRows)
  useEffect(() => { load() }, [])

  const save = async (e) => {
    e.preventDefault()
    try {
      if (editing.id) await api(`/api/admin/discounts/${editing.id}`, { method: 'PUT', body: editing })
      else await api('/api/admin/discounts', { method: 'POST', body: editing })
      toast('Хадгалагдлаа'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Хөнгөлөлт</h1>
        <button className="btn-primary" onClick={() => setEditing({ name: '', discount_type: 'PERCENT', value: 10, is_active: true })}>
          <Plus size={16} /> Нэмэх
        </button>
      </div>

      {/* Хөнгөлөлтийн логикийн тайлбар */}
      <div className="card text-sm space-y-2">
        <div className="font-semibold text-slate-200">Хөнгөлөлт хэрхэн ажилладаг вэ?</div>
        <div className="grid md:grid-cols-3 gap-3 text-slate-400">
          <div className="bg-surface-muted/40 rounded-lg p-3">
            <div className="text-slate-200 font-medium mb-1">1. Хэн үүсгэх вэ</div>
            Энэ хуудсан дээр <b className="text-slate-300">Админ / Супер админ / Санхүү</b> хөнгөлөлтийн
            төрлүүдийг урьдчилан бүртгэнэ (жишээ: байгууллагын 20%, 1 цаг үнэгүй купон).
          </div>
          <div className="bg-surface-muted/40 rounded-lg p-3">
            <div className="text-slate-200 font-medium mb-1">2. Хэн, хаана хэрэглэх вэ</div>
            <b className="text-slate-300">Кассын ажилтан</b> Касс хуудсан дээр гарах машиныг сонгоод
            «Хөнгөлөлт хэрэглэх» цэснээс сонгоно. Жишээ: дэлгүүрийн купон үзүүлсэн жолоочид
            кассир 1 цаг үнэгүйг нь хасаж тооцно.
          </div>
          <div className="bg-surface-muted/40 rounded-lg p-3">
            <div className="text-slate-200 font-medium mb-1">3. Хэрхэн тооцогдох вэ</div>
            <b className="text-slate-300">Хувиар</b>: дүнгээс % хасна (4000₮, 50% → 2000₮).{' '}
            <b className="text-slate-300">Тогтмол дүн</b>: ₮ хасна.{' '}
            <b className="text-slate-300">Үнэгүй минут</b>: хугацаанаас хасч дараа нь үнэ бодно
            (3ц зогссон, 60 мин үнэгүй → 2ц-ийн үнэ). Хөнгөлөлт Түүх/Тайланд тусдаа баганаар харагдана.
          </div>
        </div>
      </div>
      <Table headers={['Нэр', 'Төрөл', 'Утга', 'Төлөв', '']} empty={rows.length === 0}>
        {rows.map((d) => (
          <tr key={d.id}>
            <td className="td font-medium">{d.name}</td>
            <td className="td">{TYPES[d.discount_type]}</td>
            <td className="td font-mono">{fmt(d.value)}{d.discount_type === 'PERCENT' ? '%' : d.discount_type === 'FIXED' ? '₮' : ' мин'}</td>
            <td className="td"><Badge value={d.is_active ? 'active' : 'FAILED'} /></td>
            <td className="td text-right">
              <button className="btn-secondary py-1 text-xs" onClick={() => setEditing(d)}>Засах</button>
            </td>
          </tr>
        ))}
      </Table>

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? 'Хөнгөлөлт засах' : 'Хөнгөлөлт нэмэх'}>
        {editing && (
          <form onSubmit={save} className="space-y-3">
            <Field label="Нэр" required>
              <input className="input" value={editing.name} required
                onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </Field>
            <Field label="Төрөл">
              <select className="input" value={editing.discount_type}
                onChange={(e) => setEditing({ ...editing, discount_type: e.target.value })}>
                {Object.entries(TYPES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
            <Field label="Утга" required>
              <input type="number" step="1" min="0" className="input" value={editing.value} required
                onChange={(e) => setEditing({ ...editing, value: e.target.value })} />
            </Field>
            {editing.id && (
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={editing.is_active}
                  onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} /> Идэвхтэй
              </label>
            )}
            <button className="btn-primary w-full justify-center">Хадгалах</button>
          </form>
        )}
      </Modal>
    </div>
  )
}
