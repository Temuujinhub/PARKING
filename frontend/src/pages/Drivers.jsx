// Бүртгэлтэй машин — гэрээт/сарын эрхтэй машинууд
import { Plus, Search, Trash2, Upload } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmtDate } from '../api'
import { useAuth } from '../auth'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

const CONTRACT_TYPES = {
  MONTHLY: 'Сарын эрх', CONTRACT: 'Гэрээт', VIP: 'VIP', STAFF: 'Ажилтан',
  // ХБИ (хөгжлийн бэрхшээлтэй иргэн), түргэн, онцгой байдал — «Бүх зогсоол»
  // сонговол СИСТЕМ ДАЯАР (бүх түрээслэгчийн бүх зогсоолд) үнэгүй нэвтэрнэ.
  SPECIAL: 'Тусгай хэрэгцээт (ХБИ, түргэн г.м — бүх зогсоолд үнэгүй)',
  // Том зогсоол доторх жижиг зогсоолын машин — гадна зогсоолоор ТӨЛБӨРГҮЙ
  // дамжин өнгөрч, тайланд «Дамжин» гэж ялгарна.
  TRANSIT: 'Дамжин (доторх зогсоолын машин)',
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

  const load = () => {
    const p = new URLSearchParams()
    if (q) p.set('q', q)
    if (company) p.set('company', company)
    if (siteFilter) p.set('site_id', siteFilter)
    if (typeFilter) p.set('contract_type', typeFilter)
    api(`/api/admin/drivers${p.toString() ? `?${p}` : ''}`).then(setRows)
    api('/api/admin/drivers/companies').then(setCompanies).catch(() => {})
  }
  useEffect(() => { load(); api('/api/admin/sites').then(setSites) }, [])
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
      </div>
      <Table headers={['Дугаар', 'Эзэмшигч', 'Байгууллага', 'Албан тушаал', 'Төрөл', 'Зогсоол', 'Хүчинтэй хугацаа', 'Төлөв', '']}
        empty={rows.length === 0}>
        {rows.map((d) => (
          <tr key={d.id}>
            <td className="td font-mono font-bold">{d.plate_number}</td>
            <td className="td">{d.full_name}</td>
            <td className="td text-xs">{d.company}</td>
            <td className="td text-xs text-slate-400">{d.note}</td>
            <td className="td">
              {d.contract_type === 'SPECIAL'
                ? <span className="text-cyan-300">Тусгай хэрэгцээт</span>
                : (CONTRACT_TYPES[d.contract_type]?.split(' (')[0] || d.contract_type)}
            </td>
            <td className="td">{d.site_name}</td>
            <td className="td font-mono text-xs">{fmtDate(d.valid_to).split(' ')[0]} хүртэл</td>
            <td className="td"><Badge value={d.is_active ? 'active' : 'FAILED'} /></td>
            <td className="td text-right whitespace-nowrap">
              <button className="btn-secondary py-1 text-xs"
                onClick={() => setEditing({ ...d, valid_from: d.valid_from?.slice(0, 10), valid_to: d.valid_to?.slice(0, 10) })}>
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
