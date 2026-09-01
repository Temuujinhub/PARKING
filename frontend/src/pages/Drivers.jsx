// Бүртгэлтэй машин — гэрээт/сарын эрхтэй машинууд
import { Plus, Search, Trash2, Upload } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { useAuth } from '../auth'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'
import {
  PHONE_HINT, PLATE_HINT, clampNum, dateRangeError, isPhone, isPlate,
  normalizePhone, normalizePlate, timeWindowError,
} from '../validation'

const CONTRACT_TYPES = {
  MONTHLY: 'Сарын эрх', CONTRACT: 'Гэрээт', VIP: 'VIP', STAFF: 'Ажилтан',
  // ХБИ (хөгжлийн бэрхшээлтэй иргэн), түргэн, онцгой байдал — «Бүх зогсоол»
  // сонговол ӨӨРИЙН ТҮРЭЭСЛЭГЧИЙН бүх зогсоолд үнэгүй нэвтэрнэ (түрээслэгч
  // дамнахгүй — өөр түрээслэгчид эрх өгөх бол тэнд тусдаа бүртгэнэ).
  SPECIAL: 'Тусгай хэрэгцээт (ХБИ, түргэн г.м — түрээслэгчийн бүх зогсоолд)',
  // Том зогсоол доторх жижиг зогсоолын машин — гадна зогсоолоор ТӨЛБӨРГҮЙ
  // дамжин өнгөрч, тайланд «Дамжин» гэж ялгарна.
  TRANSIT: 'Дамжин (доторх зогсоолын машин)',
  // Зөвхөн ШӨНИЙН цагт үнэгүй — цонхыг глобал тохиргооноос (🌙) авна, жолооч
  // бүр дээр цаг бөглөх шаардлагагүй тул Excel импортод тохиромжтой. Тухайн
  // жолоочид free_from/free_until тавьбал глобал цонхыг дарна.
  NIGHT: 'Шөнө үнэгүй (глобал цонхоор)',
}

// Excel импортын цонх — эхлээд УРЬДЧИЛАН ХАРНА (dry-run), дараа нь баталгаажуулж оруулна.
// Ингэснээр буруу файл шууд DB рүү орохгүй.
function ImportModal({ open, onClose, sites, onDone }) {
  const toast = useToast()
  const [file, setFile] = useState(null)
  const [siteId, setSiteId] = useState('')
  const [contractType, setContractType] = useState('CONTRACT')
  const [replace, setReplace] = useState(false)
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { setFile(null); setPreview(null); setReplace(false); setContractType('CONTRACT') }, [open])

  const send = async (dryRun) => {
    if (!file) { toast('Excel файлаа сонгоно уу', 'error'); return }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('site_id', siteId)
      fd.append('contract_type', contractType)
      fd.append('replace', replace ? 'true' : 'false')
      fd.append('dry_run', dryRun ? 'true' : 'false')
      const data = await api('/api/admin/drivers/import', { method: 'POST', formData: fd })
      if (dryRun) setPreview(data)
      else {
        toast(`${data.created} шинэ, ${data.updated} шинэчлэв`)
        onDone(); onClose()
      }
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  return (
    <Modal open={open} onClose={onClose} title="Гэрээт машины жагсаалт — Excel импорт">
      <div className="space-y-3 text-sm">
        <div className="text-xs text-slate-400">
          Excel-ийн <b className="text-slate-200">бүх хуудсыг</b> уншина — хуудас бүрийг
          нэг байгууллага гэж үзнэ. Хуудсанд «Улсын дугаар» гэсэн гарчигтай багана
          байх шаардлагатай. Ижил дугаар давхардвал шинэчилнэ (давхар бүртгэл үүсэхгүй).
        </div>
        <button type="button" className="text-accent text-xs underline"
          onClick={async () => {
            try {
              const blob = await api('/api/admin/drivers/import-template', { blob: true })
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url; a.download = 'drivers_import_template.xlsx'; a.click()
              URL.revokeObjectURL(url)
            } catch (e) { toast(e.message, 'error') }
          }}>
          ⬇ Загвар файл татах (.xlsx)
        </button>
        <Field label="Excel файл (.xlsx)" required>
          <input type="file" accept=".xlsx,.xlsm" className="input"
            onChange={(e) => { setFile(e.target.files?.[0] || null); setPreview(null) }} />
        </Field>
        <Field label="Аль зогсоолд хүчинтэй вэ">
          <select className="input" value={siteId} onChange={(e) => setSiteId(e.target.value)}>
            <option value="">Бүх зогсоол</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        {/* Доторх жижиг зогсоолын жагсаалтыг том зогсоолд «Дамжин» төрлөөр
            оруулбал тэдгээр машин гадна зогсоолоор төлбөргүй нэвтэрнэ. */}
        <Field label="Бүртгэлийн төрөл">
          <select className="input" value={contractType} onChange={(e) => setContractType(e.target.value)}>
            {Object.entries(CONTRACT_TYPES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
        <label className="flex items-start gap-2 text-xs cursor-pointer">
          <input type="checkbox" className="mt-0.5 cursor-pointer" checked={replace}
            onChange={(e) => setReplace(e.target.checked)} />
          <span>
            Жагсаалтыг бүрэн солих — файлд БАЙХГҮЙ хуучин бүртгэлийг идэвхгүй болгоно
            <span className="block text-slate-500">Устгахгүй, зөвхөн идэвхгүй болгоно.</span>
          </span>
        </label>

        {preview && (
          <div className="rounded-lg border border-accent/40 bg-accent/5 p-3 space-y-2 text-xs">
            <div className="text-accent font-medium">
              Уншсан: {preview.total} машин · {Object.keys(preview.companies).length} байгууллага
            </div>
            <div className="max-h-40 overflow-y-auto space-y-0.5">
              {Object.entries(preview.companies).map(([c, n]) => (
                <div key={c} className="flex justify-between gap-3">
                  <span className="truncate">{c}</span><span className="font-mono">{n}</span>
                </div>
              ))}
            </div>
            {preview.warnings?.length > 0 && (
              <details>
                <summary className="cursor-pointer text-amber-400">
                  Анхааруулга ({preview.warnings.length})
                </summary>
                <div className="max-h-32 overflow-y-auto mt-1 space-y-0.5 text-amber-300/80">
                  {preview.warnings.map((w, i) => <div key={i}>{w}</div>)}
                </div>
              </details>
            )}
          </div>
        )}

        {!preview
          ? <button className="btn-secondary w-full justify-center" disabled={busy} onClick={() => send(true)}>
              {busy ? 'Уншиж байна…' : 'Файлыг унших (урьдчилан харах)'}
            </button>
          : <button className="btn-primary w-full justify-center" disabled={busy} onClick={() => send(false)}>
              {busy ? 'Оруулж байна…' : `${preview.total} машиныг бүртгэх`}
            </button>}
      </div>
    </Modal>
  )
}

export default function Drivers() {
  const toast = useToast()
  const { user } = useAuth()
  const isAdmin = ['ADMIN', 'SUPER_ADMIN'].includes(user?.role)
  const [rows, setRows] = useState([])
  const [sites, setSites] = useState([])
  const [q, setQ] = useState('')
  const [company, setCompany] = useState('')
  const [siteFilter, setSiteFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [companies, setCompanies] = useState([])
  const [editing, setEditing] = useState(null)
  const [importing, setImporting] = useState(false)
  // «Шөнө үнэгүй» төрлийн глобал цонх (Тохиргооны эрхтэй хүн 🌙-оор өөрчилнө)
  const [nightRules, setNightRules] = useState(null)
  const [nightEdit, setNightEdit] = useState(null)   // {night_from, night_until}
  const loadNight = () => api('/api/admin/driver-type/rules').then(setNightRules).catch(() => {})
  const nightLabel = nightRules
    ? `${nightRules.effective_from}–${nightRules.effective_until}` : '…'

  const saveNight = async () => {
    try {
      setNightRules(await api('/api/admin/driver-type/rules', { method: 'PUT', body: nightEdit }))
      setNightEdit(null)
      toast('Шөнийн цонх хадгалагдлаа — дараагийн төлбөрийн тооцооноос үйлчилнэ')
    } catch (e) { toast(e.message, 'error') }
  }

  const load = () => {
    const p = new URLSearchParams()
    if (q) p.set('q', q)
    if (company) p.set('company', company)
    if (siteFilter) p.set('site_id', siteFilter)
    if (typeFilter) p.set('contract_type', typeFilter)
    api(`/api/admin/drivers${p.toString() ? `?${p}` : ''}`).then(setRows)
    api('/api/admin/drivers/companies').then(setCompanies).catch(() => {})
  }
  useEffect(() => { load(); loadNight(); api('/api/admin/sites').then(setSites) }, [])
  useEffect(() => { load() }, [company, siteFilter, typeFilter])

  const remove = async (d) => {
    if (!window.confirm(`${d.plate_number} (${d.full_name || d.company || '-'}) бүртгэлийг БҮРМӨСӨН устгах уу?`)) return
    try {
      await api(`/api/admin/drivers/${d.id}`, { method: 'DELETE' })
      toast(`${d.plate_number} устгагдлаа`)
      load()
    } catch (err) { toast(err.message, 'error') }
  }

  const blank = {
    plate_number: '', full_name: '', phone: '', contract_type: 'MONTHLY',
    site_id: '', monthly_fee: 0, company: '', note: '',
    valid_from: new Date().toISOString().slice(0, 10),
    valid_to: new Date(Date.now() + 365 * 864e5).toISOString().slice(0, 10),
  }

  // Формын бүх алдаа (хоосон = бүх зүйл зөв). Хадгалахын өмнө шалгана.
  const formErrors = (d) => [
    !!d.plate_number && !isPlate(d.plate_number) && `Улсын дугаар — ${PLATE_HINT}`,
    !isPhone(d.phone) && `Утас — ${PHONE_HINT}`,
    dateRangeError(d.valid_from, d.valid_to),
    timeWindowError(d.free_from, d.free_until),
  ].filter(Boolean)

  const save = async (e) => {
    e.preventDefault()
    const errs = formErrors(editing)
    // Дугаарын форматын алдааг л (дипломат/тусгай дугаар байж болзошгүй тул)
    // операторт баталгаажуулах эрх өгнө; бусад нь хатуу зогсоно.
    const hard = errs.filter((m) => !m.startsWith('Улсын дугаар'))
    if (hard.length) { toast(hard[0], 'error'); return }
    if (errs.length && !confirm(`${errs[0]}\n\nТусгай дугаар мөн бол OK дарж хадгална уу.`)) return
    try {
      const body = {
        ...editing,
        site_id: editing.site_id || null,
        monthly_fee: clampNum(editing.monthly_fee, { min: 0, max: 100_000_000 }),
      }
      if (editing.id) await api(`/api/admin/drivers/${editing.id}`, { method: 'PUT', body })
      else await api('/api/admin/drivers', { method: 'POST', body })
      toast('Хадгалагдлаа'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Бүртгэлтэй машин</h1>
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={() => setImporting(true)}>
            <Upload size={16} /> Excel-ээс импортлох
          </button>
          <button className="btn-primary" onClick={() => setEditing(blank)}><Plus size={16} /> Бүртгэх</button>
        </div>
      </div>
      <div className="card flex flex-wrap gap-2 py-3 items-center">
        <input className="input font-mono flex-1 min-w-48" placeholder="Дугаар, нэр, байгууллагаар хайх…"
          value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && load()} />
        <button className="btn-secondary" onClick={load}><Search size={15} /></button>
        <select className="input w-auto min-w-44" value={siteFilter}
          aria-label="Зогсоолоор шүүх"
          onChange={(e) => setSiteFilter(e.target.value)}>
          <option value="">Бүх зогсоол (шүүлтгүй)</option>
          <option value="global">Бүх зогсоолын эрхтэй (ажилтан/албаны)</option>
          {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select className="input w-auto min-w-40" value={typeFilter} aria-label="Төрлөөр шүүх"
          onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">Бүх төрөл</option>
          {Object.entries(CONTRACT_TYPES).map(([v, l]) => (
            <option key={v} value={v}>{l.split(' (')[0]}</option>
          ))}
        </select>
        <select className="input w-auto min-w-56" value={company} onChange={(e) => setCompany(e.target.value)}>
          <option value="">Бүх байгууллага ({companies.reduce((a, c) => a + c.count, 0)})</option>
          {companies.map((c) => (
            <option key={c.company} value={c.company}>{c.company} ({c.count})</option>
          ))}
        </select>
        <span className="text-xs text-slate-400">{rows.length} машин</span>
        {/* «Шөнө үнэгүй» төрлийн глобал цонх — импортоор орсон NIGHT машинд
            ЯГ ЭНЭ цаг үйлчилж байгааг ил харуулна */}
        <button type="button"
          className="text-xs text-slate-400 hover:text-accent cursor-pointer whitespace-nowrap"
          title="«Шөнө үнэгүй» төрлийн машинд үйлчлэх цагийн цонх — дарж өөрчилнө (тохиргооны эрх шаардана)"
          onClick={() => setNightEdit({
            night_from: nightRules?.effective_from || '21:00',
            night_until: nightRules?.effective_until || '08:00',
          })}>
          🌙 Шөнө: {nightLabel}
        </button>
      </div>

      <Modal open={!!nightEdit} onClose={() => setNightEdit(null)} title="«Шөнө үнэгүй» төрлийн цонх">
        {nightEdit && (
          <div className="space-y-3">
            <p className="text-xs text-slate-400">
              «Шөнө үнэгүй» төрөлтэй БҮХ машинд (өөр дээр нь цонх заагаагүй бол)
              энэ цонх үйлчилнэ. Дуусах цаг эхлэхээсээ БАГА бол шөнө дамнана
              (ж: 21:00 → 08:00). Цонхны гаднах зогссон хугацаа энгийн тарифаар бодогдоно.
            </p>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Эхлэх цаг" required>
                <input className="input" type="time" value={nightEdit.night_from}
                  onChange={(e) => setNightEdit({ ...nightEdit, night_from: e.target.value })} />
              </Field>
              <Field label="Дуусах цаг" required>
                <input className="input" type="time" value={nightEdit.night_until}
                  onChange={(e) => setNightEdit({ ...nightEdit, night_until: e.target.value })} />
              </Field>
            </div>
            {nightEdit.night_from && nightEdit.night_from === nightEdit.night_until && (
              <div className="text-xs text-amber-400">⚠ Эхлэх, дуусах цаг ижил байж болохгүй</div>
            )}
            <button className="btn-primary w-full justify-center" onClick={saveNight}
              disabled={!nightEdit.night_from || !nightEdit.night_until
                || nightEdit.night_from === nightEdit.night_until}>
              Хадгалах
            </button>
          </div>
        )}
      </Modal>
      <Table headers={['№', 'Дугаар', 'Эзэмшигч', 'Байгууллага', 'Албан тушаал', 'Төрөл', 'Зогсоол', 'Хүчинтэй хугацаа', 'Төлөв', '']}
        empty={rows.length === 0} maxH="68vh">
        {rows.map((d, i) => (
          <tr key={d.id}>
            <td className="td text-xs text-slate-500 font-mono">{i + 1}</td>
            <td className="td font-mono font-bold">{d.plate_number}</td>
            <td className="td">{d.full_name}</td>
            <td className="td text-xs">{d.company}</td>
            <td className="td text-xs text-slate-400">{d.note}</td>
            <td className="td">
              {d.contract_type === 'SPECIAL'
                ? <span className="text-cyan-300">Тусгай хэрэгцээт</span>
                : (CONTRACT_TYPES[d.contract_type]?.split(' (')[0] || d.contract_type)}
              {d.free_from && d.free_until && (
                <div className="text-[10px] text-amber-300"
                  title="Зөвхөн энэ цонхонд үнэгүй — гаднах цаг төлбөртэй">
                  ⏱ {d.free_from}–{d.free_until} үнэгүй
                </div>
              )}
              {d.contract_type === 'NIGHT' && !(d.free_from && d.free_until) && (
                <div className="text-[10px] text-sky-300"
                  title="Глобал шөнийн цонх үйлчилнэ — 🌙 товчоор өөрчилнө">
                  🌙 {nightLabel} үнэгүй
                </div>
              )}
              {!!d.free_first_minutes && (
                <div className="text-[10px] text-amber-300"
                  title="Гэрээний нөхцөл: эхний хугацаа үнэгүй, илүү гарсныг энгийн тарифаар бодно">
                  ⏱ эхний {d.free_first_minutes / 60}ц үнэгүй
                </div>
              )}
            </td>
            <td className="td">{d.site_name}</td>
            <td className="td font-mono text-xs">{fmtDate(d.valid_to).split(' ')[0]} хүртэл</td>
            <td className="td"><Badge value={d.is_active ? 'active' : 'FAILED'} /></td>
            <td className="td text-right whitespace-nowrap">
              <button className="btn-secondary py-1 text-xs"
                onClick={() => setEditing({
                  ...d, phone: normalizePhone(d.phone),
                  valid_from: d.valid_from?.slice(0, 10), valid_to: d.valid_to?.slice(0, 10),
                })}>
                Засах
              </button>
              {isAdmin && (
                <button className="btn-secondary py-1 text-xs text-red-400 ml-1" title="Бүрмөсөн устгах (зөвхөн админ)"
                  onClick={() => remove(d)}>
                  <Trash2 size={13} />
                </button>
              )}
            </td>
          </tr>
        ))}
      </Table>

      <ImportModal open={importing} onClose={() => setImporting(false)} sites={sites} onDone={load} />

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? 'Жолооч засах' : 'Жолооч бүртгэх'}>
        {editing && (
          <form onSubmit={save} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Улсын дугаар" required>
                <input className={`input font-mono uppercase${!editing.plate_number || isPlate(editing.plate_number) ? '' : ' input-error'}`}
                  value={editing.plate_number} required maxLength={7} placeholder="1234УБА"
                  aria-invalid={!!editing.plate_number && !isPlate(editing.plate_number)}
                  onChange={(e) => setEditing({ ...editing, plate_number: normalizePlate(e.target.value) })} />
              </Field>
              <Field label="Нэр">
                <input className="input" value={editing.full_name}
                  onChange={(e) => setEditing({ ...editing, full_name: e.target.value })} />
              </Field>
              <Field label="Утас">
                <input className={`input${isPhone(editing.phone) ? '' : ' input-error'}`} type="tel"
                  inputMode="numeric" maxLength={8} placeholder="99112233" value={editing.phone || ''}
                  aria-invalid={!isPhone(editing.phone)}
                  onChange={(e) => setEditing({ ...editing, phone: normalizePhone(e.target.value) })} />
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
                <input className="input" type="number" min="0" max="100000000" step="1000" value={editing.monthly_fee}
                  onChange={(e) => setEditing({ ...editing, monthly_fee: e.target.value })} />
              </Field>
              <Field label="Эхлэх огноо" required>
                <input className="input" type="date" value={editing.valid_from} required
                  onChange={(e) => setEditing({ ...editing, valid_from: e.target.value })} />
              </Field>
              <Field label="Дуусах огноо" required>
                <input className={`input${dateRangeError(editing.valid_from, editing.valid_to) ? ' input-error' : ''}`}
                  type="date" value={editing.valid_to} required min={editing.valid_from || undefined}
                  onChange={(e) => setEditing({ ...editing, valid_to: e.target.value })} />
              </Field>
              {/* Үнэгүй цагийн цонх: хоёуланг нь тохируулбал ЗӨВХӨН энэ цонхонд
                  үнэгүй (ж: сургуулийн гэрээт 08:00-18:00), гаднах цаг төлбөртэй.
                  Хоосон = бүх цагт үнэгүй (хуучин зан). */}
              <Field label={editing.contract_type === 'NIGHT'
                ? `Үнэгүй эхлэх цаг (хоосон = глобал 🌙 ${nightLabel})`
                : 'Үнэгүй эхлэх цаг (хоосон = бүх цагт)'}>
                <input className="input" type="time" value={editing.free_from || ''}
                  onChange={(e) => setEditing({ ...editing, free_from: e.target.value || null })} />
              </Field>
              <Field label="Үнэгүй дуусах цаг">
                <input className="input" type="time" value={editing.free_until || ''}
                  onChange={(e) => setEditing({ ...editing, free_until: e.target.value || null })} />
              </Field>
              {/* Гэрээний нөхцөл: зогсолт бүрийн эхний 1-2 цаг үнэгүй, илүү
                  гарсан хугацаа энгийн тарифаар. Хоосон = бүрэн үнэгүй. */}
              <Field label="Гэрээний нөхцөл (үнэгүй хугацаа)">
                <select className="input" value={editing.free_first_minutes || ''}
                  onChange={(e) => setEditing({ ...editing, free_first_minutes: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">Бүх цагт үнэгүй</option>
                  <option value="60">Эхний 1 цаг үнэгүй, илүүг тарифаар</option>
                  <option value="120">Эхний 2 цаг үнэгүй, илүүг тарифаар</option>
                  <option value="180">Эхний 3 цаг үнэгүй, илүүг тарифаар</option>
                </select>
              </Field>
            </div>
            {formErrors(editing).length > 0 && (
              <div className="text-xs text-amber-400" aria-live="polite">
                {formErrors(editing).map((m) => <div key={m}>⚠ {m}</div>)}
              </div>
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
