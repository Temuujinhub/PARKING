// Хэрэглэгчид — зөвхөн SUPER_ADMIN
import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

const ROLES = {
  SUPER_ADMIN: 'Супер админ (бүх эрх)',
  ADMIN: 'Админ (тохиргоо)',
  FINANCE: 'Санхүү (тайлан, төлбөр)',
  OPERATOR: 'Оператор (касс, зогсоол)',
}

export default function Users() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [sites, setSites] = useState([])
  const [editing, setEditing] = useState(null)
  const load = () => api('/api/admin/users').then(setRows)
  useEffect(() => { load(); api('/api/admin/sites').then(setSites) }, [])

  const save = async (e) => {
    e.preventDefault()
    try {
      const body = { ...editing, site_id: editing.site_id || null }
      if (editing.id) await api(`/api/admin/users/${editing.id}`, { method: 'PUT', body })
      else await api('/api/admin/users', { method: 'POST', body })
      toast('Хадгалагдлаа'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Хэрэглэгчид ба эрх</h1>
        <button className="btn-primary" onClick={() => setEditing({ username: '', password: '', full_name: '', phone: '', role: 'OPERATOR', site_id: '' })}>
          <Plus size={16} /> Хэрэглэгч нэмэх
        </button>
      </div>

      <div className="card py-3 text-sm text-slate-400 grid gap-1">
        <span><b className="text-slate-200">Супер админ</b> — бүх эрх, хэрэглэгчийн удирдлага</span>
        <span><b className="text-slate-200">Админ</b> — зогсоол, тариф, төхөөрөмжийн тохиргоо + бүх үйл ажиллагаа</span>
        <span><b className="text-slate-200">Санхүү</b> — тайлан, төлбөр, НӨАТ баримт харах</span>
        <span><b className="text-slate-200">Оператор</b> — касс, шалгах, түүх, хаалт нээх</span>
      </div>

      <Table headers={['Нэвтрэх нэр', 'Нэр', 'Утас', 'Эрх', 'Зогсоол', 'Бүртгэсэн', 'Төлөв', '']} empty={rows.length === 0}>
        {rows.map((u) => (
          <tr key={u.id}>
            <td className="td font-mono font-semibold">{u.username}</td>
            <td className="td">{u.full_name}</td>
            <td className="td font-mono">{u.phone}</td>
            <td className="td text-xs">{ROLES[u.role]?.split(' (')[0] || u.role}</td>
            <td className="td text-xs">{sites.find((s) => s.id === u.site_id)?.name || 'Бүгд'}</td>
            <td className="td font-mono text-xs">{fmtDate(u.created_at).split(' ')[0]}</td>
            <td className="td"><Badge value={u.is_active ? 'active' : 'FAILED'} /></td>
            <td className="td text-right">
              <button className="btn-secondary py-1 text-xs" onClick={() => setEditing({ ...u, password: '' })}>Засах</button>
            </td>
          </tr>
        ))}
      </Table>

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? 'Хэрэглэгч засах' : 'Хэрэглэгч нэмэх'}>
        {editing && (
          <form onSubmit={save} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Нэвтрэх нэр" required>
                <input className="input font-mono" value={editing.username} required disabled={!!editing.id}
                  onChange={(e) => setEditing({ ...editing, username: e.target.value })} autoComplete="off" />
              </Field>
              <Field label={editing.id ? 'Шинэ нууц үг (хоосон=өөрчлөхгүй)' : 'Нууц үг'} required={!editing.id}>
                <input className="input" type="password" value={editing.password} required={!editing.id}
                  onChange={(e) => setEditing({ ...editing, password: e.target.value })} autoComplete="new-password" />
              </Field>
              <Field label="Бүтэн нэр">
                <input className="input" value={editing.full_name}
                  onChange={(e) => setEditing({ ...editing, full_name: e.target.value })} />
              </Field>
              <Field label="Утас">
                <input className="input" type="tel" value={editing.phone || ''}
                  onChange={(e) => setEditing({ ...editing, phone: e.target.value })} />
              </Field>
            </div>
            <Field label="Эрхийн түвшин" required>
              <select className="input" value={editing.role}
                onChange={(e) => setEditing({ ...editing, role: e.target.value })}>
                {Object.entries(ROLES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>
            {editing.role === 'OPERATOR' && (
              <Field label="Хариуцах зогсоол">
                <select className="input" value={editing.site_id || ''}
                  onChange={(e) => setEditing({ ...editing, site_id: e.target.value })}>
                  <option value="">Бүх зогсоол</option>
                  {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </Field>
            )}
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
