// Түрээслэгч (Tenant) — Monnis шиг бие даасан байгууллага бүртгэх/удирдах.
// Түрээслэгч бүр: өөрийн зогсоолууд, админ хэрэглэгч, тусдаа тооцоо/тайлан.
// Түрээслэгчийн хэрэглэгч зөвхөн өөрийн зогсоолуудын мэдээллийг харна
// (backend auth.operator_sites-ийн tenant fallback). Зөвхөн SUPER_ADMIN удирдана.
import { Building2, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { Field, Modal, PasswordInput, Table, useToast } from '../../components/ui'
import {
  PASSWORD_HINT, PHONE_HINT, REGISTER_HINT, USERNAME_HINT, isEmail, isPassword, isPhone,
  isRegister, isUsername, normalizeCode, normalizePhone, normalizeRegister, normalizeUsername,
} from '../../validation'
import QpayTestModal from './QpayTestModal'

const EMPTY = {
  name: '', code: '', register: '', contact_name: '', phone: '', email: '', note: '',
  admin_username: '', admin_password: '', admin_full_name: '', site_ids: [],
}

function TenantModal({ state, sites, onClose, onDone, onGotoIntegrations }) {
  const toast = useToast()
  const [f, setF] = useState(EMPTY)
  const isNew = state === 'new'
  useEffect(() => {
    if (state && state !== 'new') {
      setF({ ...EMPTY, ...state, qpay_password: '', phone: normalizePhone(state.phone),
             register: normalizeRegister(state.register), site_ids: (state.sites || []).map((s) => s.id) })
    } else setF(EMPTY)
  }, [state])
  if (!state) return null

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const toggleSite = (id) => setF({
    ...f, site_ids: f.site_ids.includes(id) ? f.site_ids.filter((x) => x !== id) : [...f.site_ids, id],
  })
  // Хадгалахын өмнөх формат шалгалт — бүгд заавал биш талбар тул зөвхөн
  // бөглөсөн үед нь шалгана (хоосон = алдаа биш).
  const errors = [
    !normalizeCode(f.code) && 'Богино код — латин үсэг/тоо (жишээ: MONNIS)',
    !isRegister(f.register) && `ТТД/Регистр — ${REGISTER_HINT}`,
    !isPhone(f.phone) && `Утас — ${PHONE_HINT}`,
    !isEmail(f.email) && 'И-мэйл формат буруу',
    isNew && f.admin_username && !isUsername(f.admin_username) && `Админы нэвтрэх нэр — ${USERNAME_HINT}`,
    isNew && f.admin_username && !isPassword(f.admin_password) && `Админы нууц үг — ${PASSWORD_HINT}`,
  ].filter(Boolean)

  const save = async (e) => {
    e.preventDefault()
    if (errors.length) { toast(errors[0], 'error'); return }
    try {
      if (isNew) {
        const body = { ...f }
        if (!body.admin_username) { delete body.admin_username; delete body.admin_password }
        await api('/api/admin/tenants', { method: 'POST', body })
        toast('Түрээслэгч бүртгэгдлээ')
      } else {
        // QPay данс энд илгээхгүй — Тохиргоо → Холболт хэсэгт удирдана
        const { id, name, code, register, contact_name, phone, email, note, is_active, site_ids } = f
        const body = { name, code, register, contact_name, phone, email, note, is_active, site_ids }
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
            <input className="input font-mono" value={f.code} placeholder="MONNIS" required maxLength={30}
              onChange={(e) => setF({ ...f, code: normalizeCode(e.target.value) })} /></Field>
          <Field label="ТТД / Регистр">
            <input className={`input font-mono${isRegister(f.register) ? '' : ' input-error'}`} value={f.register}
              placeholder="1234567" maxLength={14} aria-invalid={!isRegister(f.register)}
              onChange={(e) => setF({ ...f, register: normalizeRegister(e.target.value) })} />
            <div className={isRegister(f.register) ? 'hint' : 'hint-error'}>{REGISTER_HINT}</div></Field>
          <Field label="Холбоо барих хүн">
            <input className="input" value={f.contact_name} onChange={set('contact_name')} /></Field>
          <Field label="Утас">
            <input className={`input${isPhone(f.phone) ? '' : ' input-error'}`} type="tel" inputMode="numeric"
              maxLength={8} placeholder="99112233" value={f.phone} aria-invalid={!isPhone(f.phone)}
              onChange={(e) => setF({ ...f, phone: normalizePhone(e.target.value) })} /></Field>
          <Field label="И-мэйл (нэхэмжлэл илгээхэд ашиглагдана)">
            <input className={`input${isEmail(f.email) ? '' : ' input-error'}`} type="email"
              value={f.email} aria-invalid={!isEmail(f.email)} onChange={set('email')} /></Field>
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

        {/* QPay данс энд БАЙХГҮЙ — Тохиргоо → Холболт → Төлбөрийн данс хэсэгт
            нэгдсэн (өмнө нь 3 газар тарж будлиантуулдаг байсан) */}
        {!isNew && (
          <div className="rounded-lg border border-surface-border px-3 py-2.5 flex items-center justify-between gap-3 flex-wrap">
            <div className="text-sm">
              <span className="text-slate-400">QPay данс:</span>{' '}
              {state?.qpay_password_set || state?.qpay_username
                ? <span className="text-accent">тохируулсан ({state.qpay_username})</span>
                : <span className="text-amber-400">тохируулаагүй — системийн ерөнхий данс ашиглагдана</span>}
            </div>
            {onGotoIntegrations && (
              <button type="button" className="btn-secondary py-1 text-xs"
                onClick={() => { onClose(); onGotoIntegrations() }}>
                Холболт хэсэгт удирдах →
              </button>
            )}
          </div>
        )}

        {isNew && (
          <div className="border border-surface-border rounded-lg p-3 space-y-3">
            <div className="text-sm font-medium">Түрээслэгчийн админ хэрэглэгч (заавал биш)</div>
            <div className="grid sm:grid-cols-3 gap-3">
              <Field label="Нэвтрэх нэр">
                <input className={`input font-mono${!f.admin_username || isUsername(f.admin_username) ? '' : ' input-error'}`}
                  value={f.admin_username} maxLength={60}
                  onChange={(e) => setF({ ...f, admin_username: normalizeUsername(e.target.value) })} />
                <div className="hint">{USERNAME_HINT}</div></Field>
              <Field label="Нууц үг">
                <PasswordInput minLength={8} value={f.admin_password} onChange={set('admin_password')}
                  className={`input${!f.admin_password || isPassword(f.admin_password) ? '' : ' input-error'}`} />
                <div className={f.admin_password && !isPassword(f.admin_password) ? 'hint-error' : 'hint'}>{PASSWORD_HINT}</div></Field>
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

export default function TenantsSection({ onGotoIntegrations }) {
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
      <TenantModal state={modal} sites={sites} onClose={() => setModal(null)} onDone={load}
        onGotoIntegrations={onGotoIntegrations} />
      <QpayTestModal state={qpayTest} onClose={() => setQpayTest(null)} />
    </div>
  )
}
