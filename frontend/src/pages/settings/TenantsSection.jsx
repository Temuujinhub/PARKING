// Түрээслэгч (Tenant) — Monnis шиг бие даасан байгууллага бүртгэх/удирдах.
// Түрээслэгч бүр: өөрийн зогсоолууд, админ хэрэглэгч, тусдаа тооцоо/тайлан.
// Түрээслэгчийн хэрэглэгч зөвхөн өөрийн зогсоолуудын мэдээллийг харна
// (backend auth.operator_sites-ийн tenant fallback). Зөвхөн SUPER_ADMIN удирдана.
import { Building2, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { Field, Modal, PasswordInput, Table, useToast } from '../../components/ui'
import QpayTestModal from './QpayTestModal'

const EMPTY = {
  name: '', code: '', register: '', contact_name: '', phone: '', email: '', note: '',
  admin_username: '', admin_password: '', admin_full_name: '', site_ids: [],
  qpay_username: '', qpay_password: '', qpay_invoice_code: '', qpay_branch_code: '', qpay_district_code: '',
}

function TenantModal({ state, sites, onClose, onDone }) {
  const toast = useToast()
  const [f, setF] = useState(EMPTY)
  const isNew = state === 'new'
  useEffect(() => {
    if (state && state !== 'new') {
      setF({ ...EMPTY, ...state, qpay_password: '', site_ids: (state.sites || []).map((s) => s.id) })
    } else setF(EMPTY)
  }, [state])
  if (!state) return null

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const toggleSite = (id) => setF({
    ...f, site_ids: f.site_ids.includes(id) ? f.site_ids.filter((x) => x !== id) : [...f.site_ids, id],
  })
  const save = async (e) => {
    e.preventDefault()
    try {
      if (isNew) {
        const body = { ...f }
        if (!body.admin_username) { delete body.admin_username; delete body.admin_password }
        if (!body.qpay_password) delete body.qpay_password
        await api('/api/admin/tenants', { method: 'POST', body })
        toast('Түрээслэгч бүртгэгдлээ')
      } else {
        const { id, name, code, register, contact_name, phone, email, note, is_active, site_ids,
          qpay_username, qpay_password, qpay_invoice_code, qpay_branch_code, qpay_district_code } = f
        const body = { name, code, register, contact_name, phone, email, note, is_active, site_ids,
          qpay_username, qpay_invoice_code, qpay_branch_code, qpay_district_code }
        if (qpay_password) body.qpay_password = qpay_password  // хөндөөгүй бол хэвээр
        await api(`/api/admin/tenants/${id}`, { method: 'PUT', body })
        toast('Хадгалагдлаа')
      }
      onClose(); onDone()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <Modal open title={isNew ? 'Шинэ түрээслэгч бүртгэх' : `${state.name} — засах`} onClose={onClose} wide>
      <form onSubmit={save} className="space-y-4">
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Байгууллагын нэр" required>
            <input className="input" value={f.name} onChange={set('name')} required /></Field>
          <Field label="Богино код (латин)" required>
            <input className="input font-mono uppercase" value={f.code} onChange={set('code')}
              placeholder="MONNIS" required /></Field>
          <Field label="ТТД / Регистр">
            <input className="input font-mono" value={f.register} onChange={set('register')} /></Field>
          <Field label="Холбоо барих хүн">
            <input className="input" value={f.contact_name} onChange={set('contact_name')} /></Field>
          <Field label="Утас">
            <input className="input" value={f.phone} onChange={set('phone')} /></Field>
          <Field label="И-мэйл (нэхэмжлэл илгээхэд ашиглагдана)">
            <input className="input" type="email" value={f.email} onChange={set('email')} /></Field>
        </div>
        <Field label="Тэмдэглэл">
          <input className="input" value={f.note} onChange={set('note')} /></Field>

        <div>
          <div className="label mb-1.5">Хамаарах зогсоолууд</div>
          <div className="flex flex-wrap gap-2">
            {sites.map((s) => (
              <label key={s.id} className={`px-3 py-1.5 rounded-lg border text-sm cursor-pointer transition-colors
                  ${f.site_ids.includes(s.id) ? 'border-accent bg-accent/10 text-accent' : 'border-surface-border text-slate-300 hover:border-accent/40'}`}>
                <input type="checkbox" className="hidden" checked={f.site_ids.includes(s.id)}
                  onChange={() => toggleSite(s.id)} />
                {s.name} ({s.site_code})
              </label>
            ))}
          </div>
          <p className="text-xs text-slate-500 mt-1.5">
            Түрээслэгчийн хэрэглэгчид энд сонгосон зогсоолуудын мэдээллийг Л харна —
            дараа нь шинэ зогсоол нэмбэл автоматаар хамрагдана.
          </p>
        </div>

        <div className="border border-surface-border rounded-lg p-3 space-y-3">
          <div className="text-sm font-medium">QPay данс — түрээслэгчийн БҮХ зогсоолд үйлчилнэ</div>
          <div className="grid sm:grid-cols-2 gap-3">
            <Field label="Нэвтрэх нэр (client_id)">
              <input className="input font-mono" value={f.qpay_username || ''} onChange={set('qpay_username')}
                placeholder="MONNIS_PROPERTIES" /></Field>
            <Field label={state?.qpay_password_set ? 'Нууц үг (тохируулсан — солих бол бичнэ)' : 'Нууц үг'}>
              <PasswordInput className="input" value={f.qpay_password || ''} onChange={set('qpay_password')}
                placeholder={state?.qpay_password_set ? '••••••••' : ''} /></Field>
            <Field label="Нэхэмжлэхийн код (ЗААВАЛ EB_-ээр эхэлнэ)">
              <input className="input font-mono" value={f.qpay_invoice_code || ''} onChange={set('qpay_invoice_code')}
                placeholder="EB_MONNIS_PROPERTIES_INVOICE" /></Field>
            <Field label="НӨАТ дүүрэг+хороо (4 орон)">
              <input className="input font-mono" value={f.qpay_district_code || ''} onChange={set('qpay_district_code')}
                placeholder="2318" /></Field>
          </div>
          <p className="text-xs text-slate-500">
            Хоосон орхивол системийн ерөнхий данс (EasyParking) ашиглагдана. EB_ угтваргүй код
            НӨАТ давхар нэмэгдэж илүү дүн авах + e-Barimt үүсэхгүй алдаа өгдөг тул анхаараарай.
            Тохируулсны дараа аль нэг зогсоол дээр нь «Дансыг турших»-аар шалгана.
          </p>
        </div>

        {isNew && (
          <div className="border border-surface-border rounded-lg p-3 space-y-3">
            <div className="text-sm font-medium">Түрээслэгчийн админ хэрэглэгч (заавал биш)</div>
            <div className="grid sm:grid-cols-3 gap-3">
              <Field label="Нэвтрэх нэр">
                <input className="input" value={f.admin_username} onChange={set('admin_username')} /></Field>
              <Field label="Нууц үг (8+ тэмдэгт)">
                <PasswordInput className="input" value={f.admin_password} onChange={set('admin_password')} /></Field>
              <Field label="Бүтэн нэр">
                <input className="input" value={f.admin_full_name} onChange={set('admin_full_name')} /></Field>
            </div>
            <p className="text-xs text-slate-500">
              Энэ хэрэглэгч зөвхөн дээр сонгосон зогсоолуудын самбар, касс, тайлан,
              жолооч, ажилтныг удирдана. Дараа нь Ажилтан хуудаснаас өөр хэрэглэгч нэмж болно.
            </p>
          </div>
        )}
        {!isNew && (
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={f.is_active !== false}
              onChange={(e) => setF({ ...f, is_active: e.target.checked })} />
            Идэвхтэй (унтраавал хэрэглэгчид нь нэвтэрсэн ч зогсоолын мэдээлэл харагдахгүй)
          </label>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-secondary" onClick={onClose}>Болих</button>
          <button className="btn-primary">{isNew ? 'Бүртгэх' : 'Хадгалах'}</button>
        </div>
      </form>
    </Modal>
  )
}

export default function TenantsSection() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [sites, setSites] = useState([])
  const [modal, setModal] = useState(null)
  const [qpayTest, setQpayTest] = useState(null)
  // Түрээслэгчийн эхний зогсоолоор дансыг турших (данс tenant-аас урсдаг)
  const testTenant = (t) => {
    const first = (t.sites || [])[0]
    if (!first) return toast('Эхлээд зогсоол оноогоод хадгална уу', 'error')
    setQpayTest({ site: { id: first.id, name: `${t.name} · ${first.name}` } })
  }
  const load = () => {
    api('/api/admin/tenants').then(setRows).catch((e) => toast(e.message, 'error'))
    api('/api/admin/sites').then(setSites)
  }
  useEffect(load, [])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <p className="text-sm text-slate-400 max-w-2xl">
          Түрээслэгч бүр өөрийн зогсоол, хэрэглэгч, QPay данс (зогсоолын тохиргоонд),
          тусдаа тайлан/тооцоотой бие даасан нэгж. Шинэ байр гэрээлмэгц эндээс бүртгэнэ.
        </p>
        <button className="btn-primary flex items-center gap-1.5" onClick={() => setModal('new')}>
          <Plus size={16} /> Түрээслэгч нэмэх
        </button>
      </div>
      <Table headers={['Нэр', 'Код', 'ТТД', 'Зогсоолууд', 'Хэрэглэгч', 'Холбоо барих', 'Төлөв', '']}
        empty={rows.length === 0}>
        {rows.map((t) => (
          <tr key={t.id} className="hover:bg-surface-muted/40 transition-colors">
            <td className="td font-medium"><span className="flex items-center gap-2">
              <Building2 size={15} className="text-accent" />{t.name}</span></td>
            <td className="td font-mono">{t.code}</td>
            <td className="td font-mono">{t.register || '—'}</td>
            <td className="td text-xs">{(t.sites || []).map((s) => s.name).join(', ') || '—'}</td>
            <td className="td font-mono">{t.user_count}</td>
            <td className="td text-xs">{t.contact_name}{t.phone ? ` · ${t.phone}` : ''}</td>
            <td className="td">{t.is_active
              ? <span className="text-accent text-xs">Идэвхтэй</span>
              : <span className="text-red-400 text-xs">Идэвхгүй</span>}</td>
            <td className="td">
              <div className="flex gap-1.5 justify-end">
                {t.sites?.length > 0 && (
                  <button className="btn-secondary text-xs py-1" title="QPay дансыг турших"
                    onClick={() => testTenant(t)}>Данс турших</button>
                )}
                <button className="btn-secondary text-xs py-1" onClick={() => setModal(t)}>Засах</button>
              </div>
            </td>
          </tr>
        ))}
      </Table>
      <TenantModal state={modal} sites={sites} onClose={() => setModal(null)} onDone={load} />
      <QpayTestModal state={qpayTest} onClose={() => setQpayTest(null)} />
    </div>
  )
}
