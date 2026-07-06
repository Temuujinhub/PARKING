import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

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

  const toggle = async (b) => {
    try {
      await api(`/api/admin/blacklist/${b.id}`, { method: 'PUT', body: { is_active: !b.is_active } })
      load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Хар жагсаалт</h1>
        <button className="btn-primary" onClick={() => setEditing({ plate_number: '', reason: '' })}>
          <Plus size={16} /> Нэмэх
        </button>
      </div>
      <div className="card py-3 text-sm text-slate-400">
        Хар жагсаалтад орсон дугаартай машин орох үед хаалт <b className="text-red-400">автоматаар нээгдэхгүй</b>,
        оператор луу сэрэмжлүүлэг очно.
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
