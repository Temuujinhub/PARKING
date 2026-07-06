// Бүртгэлтэй жолооч — гэрээт/сарын эрхтэй машинууд
import { Plus, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate } from '../api'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

const CONTRACT_TYPES = {
  MONTHLY: 'Сарын эрх', CONTRACT: 'Гэрээт', VIP: 'VIP', STAFF: 'Ажилтан',
  SPECIAL: 'Тусгай хэрэгцээт (түргэн, онцгой байдал г.м)',
}

export default function Drivers() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [sites, setSites] = useState([])
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState(null)

  const load = () => api(`/api/admin/drivers${q ? `?q=${encodeURIComponent(q)}` : ''}`).then(setRows)
  useEffect(() => { load(); api('/api/admin/sites').then(setSites) }, [])

  const blank = {
    plate_number: '', full_name: '', phone: '', contract_type: 'MONTHLY',
    site_id: '', monthly_fee: 0,
    valid_from: new Date().toISOString().slice(0, 10),
    valid_to: new Date(Date.now() + 365 * 864e5).toISOString().slice(0, 10),
  }

  const save = async (e) => {
    e.preventDefault()
    try {
      const body = { ...editing, site_id: editing.site_id || null }
      if (editing.id) await api(`/api/admin/drivers/${editing.id}`, { method: 'PUT', body })
      else await api('/api/admin/drivers', { method: 'POST', body })
      toast('Хадгалагдлаа'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Бүртгэлтэй жолооч</h1>
        <button className="btn-primary" onClick={() => setEditing(blank)}><Plus size={16} /> Бүртгэх</button>
      </div>
      <div className="card flex gap-2 py-3">
        <input className="input font-mono" placeholder="Дугаараар хайх…" value={q}
          onChange={(e) => setQ(e.target.value.toUpperCase())} onKeyDown={(e) => e.key === 'Enter' && load()} />
        <button className="btn-secondary" onClick={load}><Search size={15} /></button>
      </div>
      <Table headers={['Дугаар', 'Нэр', 'Утас', 'Төрөл', 'Зогсоол', 'Сарын төлбөр', 'Хүчинтэй хугацаа', 'Төлөв', '']}
        empty={rows.length === 0}>
        {rows.map((d) => (
          <tr key={d.id}>
            <td className="td font-mono font-bold">{d.plate_number}</td>
            <td className="td">{d.full_name}</td>
            <td className="td font-mono">{d.phone}</td>
            <td className="td">{CONTRACT_TYPES[d.contract_type] || d.contract_type}</td>
            <td className="td">{d.site_name}</td>
            <td className="td font-mono">{fmt(d.monthly_fee)}₮</td>
            <td className="td font-mono text-xs">{fmtDate(d.valid_to).split(' ')[0]} хүртэл</td>
            <td className="td"><Badge value={d.is_active ? 'active' : 'FAILED'} /></td>
            <td className="td text-right">
              <button className="btn-secondary py-1 text-xs"
                onClick={() => setEditing({ ...d, valid_from: d.valid_from?.slice(0, 10), valid_to: d.valid_to?.slice(0, 10) })}>
                Засах
              </button>
            </td>
          </tr>
        ))}
      </Table>

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? 'Жолооч засах' : 'Жолооч бүртгэх'}>
        {editing && (
          <form onSubmit={save} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Улсын дугаар" required>
                <input className="input font-mono" value={editing.plate_number} required
                  onChange={(e) => setEditing({ ...editing, plate_number: e.target.value.toUpperCase() })} />
              </Field>
              <Field label="Нэр">
                <input className="input" value={editing.full_name}
                  onChange={(e) => setEditing({ ...editing, full_name: e.target.value })} />
              </Field>
              <Field label="Утас">
                <input className="input" type="tel" value={editing.phone}
                  onChange={(e) => setEditing({ ...editing, phone: e.target.value })} />
              </Field>
              <Field label="Төрөл">
                <select className="input" value={editing.contract_type}
                  onChange={(e) => setEditing({ ...editing, contract_type: e.target.value })}>
                  {Object.entries(CONTRACT_TYPES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Field>
              <Field label="Зогсоол">
                <select className="input" value={editing.site_id || ''}
                  onChange={(e) => setEditing({ ...editing, site_id: e.target.value })}>
                  <option value="">Бүх зогсоол</option>
                  {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </Field>
              <Field label="Сарын төлбөр (₮)">
                <input className="input" type="number" min="0" value={editing.monthly_fee}
                  onChange={(e) => setEditing({ ...editing, monthly_fee: e.target.value })} />
              </Field>
              <Field label="Эхлэх огноо" required>
                <input className="input" type="date" value={editing.valid_from} required
                  onChange={(e) => setEditing({ ...editing, valid_from: e.target.value })} />
              </Field>
              <Field label="Дуусах огноо" required>
                <input className="input" type="date" value={editing.valid_to} required
                  onChange={(e) => setEditing({ ...editing, valid_to: e.target.value })} />
              </Field>
            </div>
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
