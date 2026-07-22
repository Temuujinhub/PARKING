// Хэрэглэгчид — зөвхөн SUPER_ADMIN
import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { Badge, Field, Modal, PasswordInput, Table, useToast } from '../components/ui'

const ROLES = {
  SUPER_ADMIN: 'Супер админ (бүх эрх)',
  ADMIN: 'Админ (тохиргоо)',
  FINANCE: 'Санхүү (тайлан, төлбөр)',
  HR: 'Хүний нөөц (ажилтан)',
  OPERATOR: 'Оператор (касс, зогсоол)',
}
// UI-аас үүсгэж болох эрхүүд — SUPER_ADMIN-ыг оруулахгүй (зөвхөн DB-ээр)
const CREATABLE_ROLES = {
  ADMIN: ROLES.ADMIN, FINANCE: ROLES.FINANCE, HR: ROLES.HR, OPERATOR: ROLES.OPERATOR,
}

// Хуудас/модулийн эрхийн матриц — backend auth.ALL_MODULES-тай ижил түлхүүрүүд
const MODULE_GROUPS = [
  ['Үйл ажиллагаа', [
    ['dashboard', 'Хянах самбар'], ['cashier', 'Касс'], ['check', 'Шалгах'],
    ['history', 'Түүх'], ['compensations', 'Ээлж хаах / Нөхөн төлбөр'],
  ]],
  ['Санхүү', [
    ['reports', 'Тайлан + Мөнгөн тооцоо'], ['discounts', 'Хөнгөлөлт + Тариф'],
    ['drivers', 'Бүртгэлтэй жолооч'], ['vat', 'Ибаримт'], ['payments', 'Төлбөрийн үйлдэл'],
    ['blacklist', 'Хар жагсаалт'],
  ]],
  ['Админ', [
    ['settings', 'Тохиргоо'], ['devices', 'Төхөөрөмж'], ['barriers', 'Хаалтны удирдлага'],
    ['users', 'Ажилтан'], ['logs', 'Лог'], ['health', 'Системийн эрүүл мэнд'],
  ]],
]
// Role бүрийн default эрх — backend auth.ROLE_PERMISSIONS-той ижил
const ROLE_DEFAULTS = {
  ADMIN: ['dashboard', 'cashier', 'check', 'history', 'discounts', 'settings', 'reports',
    'drivers', 'vat', 'barriers', 'blacklist', 'logs', 'devices', 'compensations', 'users', 'health'],
  FINANCE: ['dashboard', 'history', 'reports', 'vat', 'payments', 'logs',
    'compensations', 'discounts', 'blacklist'],
  HR: ['users'],
  OPERATOR: ['cashier', 'check', 'history', 'compensations'],
}
const isDefaultPerms = (role, perms) => {
  const d = new Set(ROLE_DEFAULTS[role] || [])
  return perms.length === d.size && perms.every((p) => d.has(p))
}

export default function Users() {
  const toast = useToast()
  const [tab, setTab] = useState('staff')
  const [rows, setRows] = useState([])
  const [sites, setSites] = useState([])
  const [editing, setEditing] = useState(null)
  const load = () => api('/api/admin/users').then(setRows)
  useEffect(() => { load(); api('/api/admin/sites').then(setSites) }, [])
  // Супер админ мөрийг жагсаалтад харуулахгүй (зөвхөн DB-ээр удирддаг)
  const visibleRows = rows.filter((u) => u.role !== 'SUPER_ADMIN')

  // Засах/нэмэх modal нээхэд эрхийн матриц + зогсоолуудыг бэлдэнэ
  const openEdit = (u) => setEditing(u.id
    ? { ...u, password: '', perms: u.permissions || ROLE_DEFAULTS[u.role] || [], site_ids: u.site_ids || (u.site_id ? [u.site_id] : []) }
    : { username: '', password: '', full_name: '', phone: '', role: 'OPERATOR', site_id: '', perms: ROLE_DEFAULTS.OPERATOR, site_ids: [] })

  const save = async (e) => {
    e.preventDefault()
    try {
      const siteIds = editing.role === 'OPERATOR' ? editing.site_ids : []
      const primary = editing.role === 'OPERATOR'
        ? (siteIds.includes(editing.site_id) ? editing.site_id : siteIds[0] || null) : null
      const body = {
        ...editing, perms: undefined,
        site_id: primary,
        site_ids: siteIds.length ? siteIds : null,
        // default-той ижил бол null — role-ийн эрх өөрчлөгдвөл автоматаар дагана
        permissions: isDefaultPerms(editing.role, editing.perms) ? null : editing.perms,
      }
      if (editing.id) await api(`/api/admin/users/${editing.id}`, { method: 'PUT', body })
      else await api('/api/admin/users', { method: 'POST', body })
      toast('Хадгалагдлаа'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Ажилтан</h1>
      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {[['staff', 'Ажилтан ба эрх'], ['hr', 'Хүний нөөц (ажилласан өдөр)']].map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>

      {tab === 'hr' && <HR />}

      {tab === 'staff' && (<>
      <div className="flex justify-end">
        <button className="btn-primary" onClick={() => openEdit({})}>
          <Plus size={16} /> Ажилтан нэмэх
        </button>
      </div>

      <div className="card py-3 text-sm text-slate-400 grid gap-1">
        <span><b className="text-slate-200">Админ</b> — зогсоол, төхөөрөмжийн тохиргоо + бүх үйл ажиллагаа</span>
        <span><b className="text-slate-200">Санхүү</b> — тайлан, төлбөр, НӨАТ, хөнгөлөлт, тариф, хар жагсаалт</span>
        <span><b className="text-slate-200">Хүний нөөц</b> — зөвхөн ажилтан нэмж/хасах, ажилласан өдрийн тайлан</span>
        <span><b className="text-slate-200">Оператор</b> — өөрийн хариуцах зогсоолын касс, шалгах, түүх</span>
      </div>

      <Table headers={['Нэвтрэх нэр', 'Нэр', 'Утас', 'Эрх', 'Зогсоол', 'Бүртгэсэн', 'Төлөв', '']} empty={visibleRows.length === 0}>
        {visibleRows.map((u) => (
          <tr key={u.id}>
            <td className="td font-mono font-semibold">{u.username}</td>
            <td className="td">{u.full_name}</td>
            <td className="td font-mono">{u.phone}</td>
            <td className="td text-xs">
              {ROLES[u.role]?.split(' (')[0] || u.role}
              {u.permissions && <span className="ml-1 text-[10px] text-amber-400" title="Тусгай эрхийн матриц тохируулсан">тусгай</span>}
            </td>
            <td className="td text-xs">
              {u.role !== 'OPERATOR' ? 'Бүгд'
                : (u.site_ids?.length ? u.site_ids : (u.site_id ? [u.site_id] : []))
                  .map((id) => sites.find((s) => s.id === id)?.name || '?').join(', ') || 'Бүгд'}
            </td>
            <td className="td font-mono text-xs">{fmtDate(u.created_at).split(' ')[0]}</td>
            <td className="td"><Badge value={u.is_active ? 'active' : 'FAILED'} /></td>
            <td className="td text-right">
              <button className="btn-secondary py-1 text-xs" onClick={() => openEdit(u)}>Засах</button>
            </td>
          </tr>
        ))}
      </Table>
      </>)}

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? 'Ажилтан засах' : 'Ажилтан нэмэх'}>
        {editing && (
          <form onSubmit={save} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Нэвтрэх нэр" required>
                <input className="input font-mono" value={editing.username} required disabled={!!editing.id}
                  onChange={(e) => setEditing({ ...editing, username: e.target.value })} autoComplete="off" />
              </Field>
              <Field label={editing.id ? 'Шинэ нууц үг (хоосон=өөрчлөхгүй)' : 'Нууц үг'} required={!editing.id}>
                <PasswordInput value={editing.password} required={!editing.id}
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
            <Field label="Эрхийн түвшин (загвар)" required>
              <select className="input" value={editing.role}
                onChange={(e) => setEditing({ ...editing, role: e.target.value, perms: ROLE_DEFAULTS[e.target.value] || [] })}>
                {Object.entries(CREATABLE_ROLES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </Field>

            {/* Эрхийн чекбокс матриц — түвшин сонгоход default-оор бөглөгдөж, чөлөөтэй өөрчилж болно */}
            <Field label="Хандах хуудсууд (чекбокс матриц)">
              <div className="space-y-2 max-h-56 overflow-y-auto border border-surface-border/60 rounded-lg p-3">
                {MODULE_GROUPS.map(([group, mods]) => (
                  <div key={group}>
                    <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">{group}</div>
                    <div className="grid grid-cols-2 gap-x-3 gap-y-1">
                      {mods.map(([key, label]) => (
                        <label key={key} className="flex items-center gap-2 text-sm cursor-pointer">
                          <input type="checkbox" checked={editing.perms.includes(key)}
                            onChange={(e) => setEditing({
                              ...editing,
                              perms: e.target.checked
                                ? [...editing.perms, key]
                                : editing.perms.filter((p) => p !== key),
                            })} />
                          {label}
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              {!isDefaultPerms(editing.role, editing.perms) && (
                <div className="text-[11px] text-amber-400 mt-1">
                  Түвшний default-оос өөр (тусгай) эрх — хадгалахад энэ матриц үйлчилнэ
                </div>
              )}
            </Field>

            {editing.role === 'OPERATOR' && (
              <Field label="Хариуцах зогсоолууд (олон сонгож болно)">
                <div className="grid grid-cols-2 gap-1 border border-surface-border/60 rounded-lg p-3">
                  {sites.map((s) => (
                    <label key={s.id} className="flex items-center gap-2 text-sm cursor-pointer">
                      <input type="checkbox" checked={editing.site_ids.includes(s.id)}
                        onChange={(e) => setEditing({
                          ...editing,
                          site_ids: e.target.checked
                            ? [...editing.site_ids, s.id]
                            : editing.site_ids.filter((x) => x !== s.id),
                        })} />
                      {s.name}
                    </label>
                  ))}
                  {sites.length === 0 && <span className="text-xs text-slate-500">Зогсоол бүртгэгдээгүй</span>}
                </div>
                {editing.site_ids.length === 0 && (
                  <div className="text-[11px] text-slate-500 mt-1">Юу ч сонгоогүй бол бүх зогсоолд хандана</div>
                )}
                {editing.site_ids.length > 1 && (
                  <div className="mt-2">
                    <span className="text-xs text-slate-400">Үндсэн зогсоол (ээлж энд нээгдэнэ):</span>
                    <select className="input mt-1" value={editing.site_ids.includes(editing.site_id) ? editing.site_id : editing.site_ids[0]}
                      onChange={(e) => setEditing({ ...editing, site_id: e.target.value })}>
                      {editing.site_ids.map((id) => (
                        <option key={id} value={id}>{sites.find((s) => s.id === id)?.name || id}</option>
                      ))}
                    </select>
                  </div>
                )}
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

// Хүний нөөц — сар бүрийн оператор бүрийн ажилласан өдрүүд (календар)
function HR() {
  const now = new Date()
  const [month, setMonth] = useState(`${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)
  const [ops, setOps] = useState([])
  const [selected, setSelected] = useState(null)
  useEffect(() => {
    api(`/api/cashier/hr/worked-days?month=${month}`)
      .then((d) => { setOps(d.operators); setSelected(null) }).catch(() => setOps([]))
  }, [month])

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-sm text-slate-400">Сар:</span>
        <input type="month" className="input w-auto" value={month} onChange={(e) => setMonth(e.target.value)} />
        <span className="text-xs text-slate-500">Зөвхөн оператор ажилтнууд харагдана</span>
      </div>
      <div className="grid lg:grid-cols-2 gap-6 items-start">
        <div className="card">
          <h3 className="font-semibold mb-3">Операторууд</h3>
          <Table headers={['Ажилтан', 'Ажилласан өдөр', '']} empty={ops.length === 0}>
            {ops.map((o) => (
              <tr key={o.user_id} onClick={() => setSelected(o)}
                className={`cursor-pointer hover:bg-surface-muted/40 ${selected?.user_id === o.user_id ? 'bg-accent/5' : ''}`}>
                <td className="td font-medium">{o.name}</td>
                <td className="td font-mono">{o.days_count} өдөр</td>
                <td className="td text-right text-xs text-accent">Календар →</td>
              </tr>
            ))}
          </Table>
        </div>
        <div className="card">
          {selected
            ? <Calendar month={month} name={selected.name} days={selected.days} />
            : <div className="text-sm text-slate-500 py-10 text-center">Ажилтан дээр дарж ажилласан өдрүүдийг календараар харна уу</div>}
        </div>
      </div>
    </div>
  )
}

function Calendar({ month, name, days }) {
  const [y, m] = month.split('-').map(Number)
  const daysInMonth = new Date(y, m, 0).getDate()
  const startWeekday = (new Date(y, m - 1, 1).getDay() + 6) % 7 // Даваа=0
  const worked = new Set(days)
  const cells = [...Array(startWeekday).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => i + 1)]
  const WD = ['Да', 'Мя', 'Лх', 'Пү', 'Ба', 'Бя', 'Ня']
  return (
    <div>
      <h3 className="font-semibold mb-1">{name}</h3>
      <div className="text-xs text-slate-400 mb-3">{month} — <b className="text-accent">{days.length}</b> өдөр ажилласан</div>
      <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-slate-500 mb-1">
        {WD.map((w) => <div key={w}>{w}</div>)}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((d, i) => {
          const ds = d ? `${month}-${String(d).padStart(2, '0')}` : null
          const on = ds && worked.has(ds)
          return (
            <div key={i} title={on ? `${ds}: ажилласан` : ds || ''}
              className={`aspect-square flex items-center justify-center rounded text-sm
                ${!d ? '' : on ? 'bg-accent text-white font-bold' : 'bg-surface-muted/30 text-slate-500'}`}>
              {d || ''}
            </div>
          )
        })}
      </div>
    </div>
  )
}
