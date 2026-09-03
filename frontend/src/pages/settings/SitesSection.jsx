// Зогсоолын жагсаалт — нэмэх wizard, засах, устгах, QR.
// QPay/банкны данс энд БАЙХГҮЙ — Тохиргоо → Холболт хэсэгт нэгдсэн.
import { Plus, QrCode, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { useAuth } from '../../auth'
import { Table, useToast } from '../../components/ui'
import { genDevices } from './shared'
import { clampNum, clampOrNull } from '../../validation'
import SiteEditModal from './SiteEditModal'
import SiteQrModal from './SiteQrModal'
import SiteWizardModal from './SiteWizardModal'

export default function SitesSection({ onGotoIntegrations }) {
  const toast = useToast()
  const { user } = useAuth()
  const isSuper = user?.role === 'SUPER_ADMIN'
  const [rows, setRows] = useState([])
  const [templates, setTemplates] = useState([])
  const [tenants, setTenants] = useState([])
  const [editing, setEditing] = useState(null)
  const [qrSite, setQrSite] = useState(null)
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
      const body = {
        ...editing,
        // Багтаамж 0 = хязгааргүй; сөрөг/утгагүй тоо ороход «сул зай» тооцоо эвдэрдэг
        capacity: editing.unlimited ? 0 : clampNum(editing.capacity, { min: 0, max: 100000, fallback: 0 }),
        tariff_template_id: editing.tariff_template_id || null,
        // '' = глобал default (72ц), 0 = унтраах, N = тухайн зогсоолын босго
        auto_close_hours: clampOrNull(editing.auto_close_hours, { min: 0, max: 720 }),
        entry_only_free_hours: clampOrNull(editing.entry_only_free_hours, { min: 0, max: 720 }),
        // '' = глобал default (унтраалттай), 0 = унтраах, N = N минут тутам хаах
        barrier_close_sweep_min: clampOrNull(editing.barrier_close_sweep_min, { min: 0, max: 1440 }),
        transit_max_hours: clampOrNull(editing.transit_max_hours, { min: 0, max: 720 }),
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
    site: { name: '', site_code: '', zone_code: 'A', address: '', capacity: 50, tariff_template_id: templates[0]?.id || '',
      registered_only: false, no_charge: false },
    // Зогсоолын төлбөрийн дүрэм — хоосон = ерөнхий утга (Төлбөрийн дүрэм таб)
    rules: { no_session_fee: '', min_stay_seconds: '', policy: '', block_exit_debt_count: '' },
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
            <td className="td font-medium">
              {s.name}
              {/* Ижил нэртэй өөр зогсоол байна — төхөөрөмж тохируулахад андуурахад амархан */}
              {s.name_conflict && (
                <span className="ml-1.5 text-[10px] text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded cursor-help whitespace-nowrap"
                  title={s.name_conflict}>⚠ ижил нэр</span>
              )}
              {/* Дамжин зогсоолын бүтэц нүдэнд шууд харагдана — буруу холбосон
                  эсэхийг тохиргоо нээхгүйгээр илрүүлнэ */}
              {s.parent_site_name && (
                <span className="ml-1.5 text-[10px] text-sky-400 bg-sky-500/10 px-1.5 py-0.5 rounded cursor-help whitespace-nowrap"
                  title={`«${s.parent_site_name}» зогсоолын ДОТОР. Машин энд байх хугацаанд `
                    + `тэр зогсоолын төлбөр гүйхгүй.`
                    + (s.no_charge ? ' Энэ зогсоол өөрөө төлбөр авахгүй.' : '')}>
                  ↳ {s.parent_site_name} дотор
                </span>
              )}
              {s.child_site_count > 0 && (
                <span className="ml-1.5 text-[10px] text-sky-400 bg-sky-500/10 px-1.5 py-0.5 rounded cursor-help whitespace-nowrap"
                  title={`Дотроо ${s.child_site_count} зогсоол агуулна. Тэдгээрт байх хугацаанд `
                    + `энэ зогсоолын төлбөрийн тоолуур зогсоно.`}>
                  дотроо {s.child_site_count} зогсоол
                </span>
              )}
              {s.no_charge && !s.parent_site_name && (
                <span className="ml-1.5 text-[10px] text-slate-400 bg-slate-500/10 px-1.5 py-0.5 rounded whitespace-nowrap">
                  төлбөргүй
                </span>
              )}
            </td>
            <td className="td font-mono">{s.site_code}</td>
            <td className="td">{s.zone_code}</td>
            {/* Оноогоогүй (өнчин) зогсоол хэний ч жагсаалтад харагдахгүй тул илт анхааруулна */}
            {isSuper && <td className="td text-xs">
              {s.tenant_name || <span className="text-red-400">Оноогоогүй!</span>}
            </td>}
            <td className="td font-mono">{s.capacity ? s.capacity : <span className="text-slate-500">Хязгааргүй</span>}</td>
            {/* Доторх зогсоолд байгаа машиныг «Зогсож буй»-д ДАВХАР тоолохгүй
                (гадна талын зайг эзлээгүй) — гэхдээ нуухгүй, хажууд нь харуулна */}
            <td className="td font-mono">
              {s.occupied}
              {s.inside_nested > 0 && (
                <span className="ml-1.5 text-[10px] text-sky-400 cursor-help font-sans"
                  title={s.inside_nested_excluded
                    ? `Нэмээд ${s.inside_nested} машин доторх зогсоолд байна. Гадна талын `
                      + `зайг эзлээгүй тул «Зогсож буй»-д тоологдоогүй, төлбөрийн тоолуур нь зогссон.`
                    : `${s.inside_nested} машин доторх (давхар) зогсоолд байна. Энэ зогсоолын `
                      + `талбайд байгаа тул «Зогсож буй»-д тоологдсон, зөвхөн төлбөрийн тоолуур нь зогссон.`}>
                  {s.inside_nested_excluded ? '+' : ''}{s.inside_nested} дотор
                </span>
              )}
            </td>
            <td className="td font-mono text-accent">{s.free_spaces ?? '—'}</td>
            <td className="td text-xs">{s.tariff_template_name || '-'}</td>
            <td className="td">
              <button className="btn-secondary py-1 text-xs" onClick={() => setQrSite(s)} aria-label="QR код харах">
                <QrCode size={14} />
              </button>
            </td>
            <td className="td text-right whitespace-nowrap">
              <button className="btn-secondary py-1 text-xs mr-1"
                onClick={() => setEditing({ ...s, unlimited: !s.capacity })}>Засах</button>
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
        tenants={isSuper ? tenants : null} sites={rows}
        onSubmit={save} onGotoIntegrations={onGotoIntegrations} />

      <SiteQrModal qrSite={qrSite} onClose={() => setQrSite(null)} />
    </>
  )
}
