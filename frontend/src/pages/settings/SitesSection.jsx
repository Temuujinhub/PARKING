// Зогсоолын жагсаалт — нэмэх wizard, засах, устгах, QR, QPay туршилт
import { Plus, QrCode, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { useAuth } from '../../auth'
import { Table, useToast } from '../../components/ui'
import QpayTestModal from './QpayTestModal'
import { genDevices } from './shared'
import SiteEditModal from './SiteEditModal'
import SiteQrModal from './SiteQrModal'
import SiteWizardModal from './SiteWizardModal'

export default function SitesSection() {
  const toast = useToast()
  const { user } = useAuth()
  const isSuper = user?.role === 'SUPER_ADMIN'
  const [rows, setRows] = useState([])
  const [templates, setTemplates] = useState([])
  const [tenants, setTenants] = useState([])
  const [editing, setEditing] = useState(null)
  const [qrSite, setQrSite] = useState(null)
  const [qpayTest, setQpayTest] = useState(null)
  const [wizard, setWizard] = useState(null)
  const load = () => api('/api/admin/sites').then(setRows)
  useEffect(() => {
    load()
    api('/api/admin/tariff-templates').then(setTemplates)
    if (isSuper) api('/api/admin/tenants').then(setTenants)
  }, [])

  const save = async (e) => {
    e.preventDefault()
    try {
      // QPay: "ерөнхий данс" сонгосон бол талбаруудыг цэвэрлэж илгээнэ,
      // "өөрийн данс" сонгосон бол нэр+нууц үг хоёулаа байх ёстой
      if (editing.qpay_mode === 'own') {
        if (!editing.qpay_username?.trim()) {
          toast('QPay нэвтрэх нэрээ бичнэ үү (эсвэл "Системийн ерөнхий данс"-ыг сонгоно уу)', 'error'); return
        }
        if (!editing.qpay_password?.trim() && !editing.qpay_password_set) {
          toast('QPay нууц үгээ бичнэ үү — нэр ганцаараа хангалтгүй', 'error'); return
        }
      }
      const qpayClear = editing.qpay_mode === 'global'
        ? { qpay_username: '', qpay_password: '', qpay_invoice_code: '',
            qpay_district_code: '', qpay_branch_code: '' }
        : {}
      const body = {
        ...editing,
        ...qpayClear,
        capacity: editing.unlimited ? 0 : +editing.capacity,
        tariff_template_id: editing.tariff_template_id || null,
        // '' = глобал default (72ц), 0 = унтраах, N = тухайн зогсоолын босго
        auto_close_hours: editing.auto_close_hours === '' || editing.auto_close_hours == null
          ? null : +editing.auto_close_hours,
        entry_only_free_hours: editing.entry_only_free_hours === '' || editing.entry_only_free_hours == null
          ? null : +editing.entry_only_free_hours,
      }
      await api(`/api/admin/sites/${editing.id}`, { method: 'PUT', body })
      toast('Хадгалагдлаа'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  // Зогсоол устгах — түүхтэй бол сервер 409 буцаана → force дахин баталгаажуулна
  const removeSite = async (s) => {
    if (!confirm(`"${s.name}" (${s.site_code}) зогсоолыг устгах уу?`)) return
    try {
      await api(`/api/admin/sites/${s.id}`, { method: 'DELETE' })
      toast('Устгагдлаа'); load()
    } catch (err) {
      if (/бүртгэл/.test(err.message)) {
        if (!confirm(`${err.message}\n\nБүх түүх, төлбөрийн бичлэгийн хамт БҮРМӨСӨН устгах уу? Буцаах боломжгүй!`)) return
        try {
          const r = await api(`/api/admin/sites/${s.id}?force=true`, { method: 'DELETE' })
          toast(`Устгагдлаа (${r.deleted_sessions} бүртгэлийн хамт)`); load()
        } catch (e2) { toast(e2.message, 'error') }
      } else toast(err.message, 'error')
    }
  }

  const openWizard = () => setWizard({
    step: 1,
    unlimited: false,
    site: { name: '', site_code: '', zone_code: 'A', address: '', capacity: 50, tariff_template_id: templates[0]?.id || '' },
    entryLanes: 1, exitLanes: 1,
    devices: Object.fromEntries(genDevices(1, 1).map((d) => [d.key, { enabled: true, ip_address: '' }])),
    created: null,
    createdDevices: [],
  })

  return (
    <>
      <div className="flex justify-end">
        <button className="btn-primary" onClick={openWizard}>
          <Plus size={16} /> Зогсоол нэмэх
        </button>
      </div>
      <Table headers={['Нэр', 'Код', 'Бүс', ...(isSuper ? ['Түрээслэгч'] : []), 'Багтаамж', 'Зогсож буй', 'Сул', 'Тариф', 'QR', '']} empty={rows.length === 0}>
        {rows.map((s) => (
          <tr key={s.id}>
            <td className="td font-medium">{s.name}</td>
            <td className="td font-mono">{s.site_code}</td>
            <td className="td">{s.zone_code}</td>
            {/* Оноогоогүй (өнчин) зогсоол хэний ч жагсаалтад харагдахгүй тул илт анхааруулна */}
            {isSuper && <td className="td text-xs">
              {s.tenant_name || <span className="text-red-400">Оноогоогүй!</span>}
            </td>}
            <td className="td font-mono">{s.capacity ? s.capacity : <span className="text-slate-500">Хязгааргүй</span>}</td>
            <td className="td font-mono">{s.occupied}</td>
            <td className="td font-mono text-accent">{s.free_spaces ?? '—'}</td>
            <td className="td text-xs">{s.tariff_template_name || '-'}</td>
            <td className="td">
              <button className="btn-secondary py-1 text-xs" onClick={() => setQrSite(s)} aria-label="QR код харах">
                <QrCode size={14} />
              </button>
            </td>
            <td className="td text-right whitespace-nowrap">
              <button className="btn-secondary py-1 text-xs mr-1"
                onClick={() => setEditing({ ...s, unlimited: !s.capacity,
                  qpay_mode: (s.qpay_username || s.qpay_password_set) ? 'own' : 'global' })}>Засах</button>
              <button className="btn-secondary py-1 text-xs text-red-400 hover:text-red-300"
                onClick={() => removeSite(s)} aria-label={`${s.name} зогсоолыг устгах`}>
                <Trash2 size={14} />
              </button>
            </td>
          </tr>
        ))}
      </Table>

      {/* ─── Зогсоол үүсгэх 3 алхамт wizard ─── */}
      <SiteWizardModal wizard={wizard} setWizard={setWizard} templates={templates} reload={load} />

      <SiteEditModal editing={editing} setEditing={setEditing} templates={templates}
        tenants={isSuper ? tenants : null}
        onSubmit={save} onTest={() => setQpayTest({ site: editing, amount: 10 })} />

      <QpayTestModal state={qpayTest} onClose={() => setQpayTest(null)} />

      <SiteQrModal qrSite={qrSite} onClose={() => setQrSite(null)} />
    </>
  )
}
