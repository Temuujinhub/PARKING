// Тохиргоо: Зогсоол / Тарифын загвар / Төхөөрөмж
import { Copy, Plus, QrCode, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt } from '../api'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

export default function Settings() {
  const [tab, setTab] = useState('sites')
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Тохиргоо</h1>
      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {[['sites', 'Зогсоол'], ['tariffs', 'Тарифын загвар'], ['devices', 'Төхөөрөмж']].map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>
      {tab === 'sites' && <Sites />}
      {tab === 'tariffs' && <Tariffs />}
      {tab === 'devices' && <Devices />}
    </div>
  )
}

function Sites() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [templates, setTemplates] = useState([])
  const [editing, setEditing] = useState(null)
  const [qrSite, setQrSite] = useState(null)
  const load = () => api('/api/admin/sites').then(setRows)
  useEffect(() => { load(); api('/api/admin/tariff-templates').then(setTemplates) }, [])

  const save = async (e) => {
    e.preventDefault()
    try {
      const body = { ...editing, capacity: +editing.capacity, tariff_template_id: editing.tariff_template_id || null }
      if (editing.id) await api(`/api/admin/sites/${editing.id}`, { method: 'PUT', body })
      else await api('/api/admin/sites', { method: 'POST', body })
      toast('Хадгалагдлаа'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  const payUrl = (code) => `${location.origin}/pay?site=${code}`
  const qrUrl = (code) => `/api/public/qr/${code}.png`

  return (
    <>
      <div className="flex justify-end">
        <button className="btn-primary" onClick={() => setEditing({ name: '', site_code: '', zone_code: 'A', address: '', capacity: 50, tariff_template_id: '' })}>
          <Plus size={16} /> Зогсоол нэмэх
        </button>
      </div>
      <Table headers={['Нэр', 'Код', 'Бүс', 'Багтаамж', 'Зогсож буй', 'Сул', 'Тариф', 'QR', '']} empty={rows.length === 0}>
        {rows.map((s) => (
          <tr key={s.id}>
            <td className="td font-medium">{s.name}</td>
            <td className="td font-mono">{s.site_code}</td>
            <td className="td">{s.zone_code}</td>
            <td className="td font-mono">{s.capacity}</td>
            <td className="td font-mono">{s.occupied}</td>
            <td className="td font-mono text-accent">{s.free_spaces}</td>
            <td className="td text-xs">{s.tariff_template_name || '-'}</td>
            <td className="td">
              <button className="btn-secondary py-1 text-xs" onClick={() => setQrSite(s)} aria-label="QR код харах">
                <QrCode size={14} />
              </button>
            </td>
            <td className="td text-right">
              <button className="btn-secondary py-1 text-xs" onClick={() => setEditing(s)}>Засах</button>
            </td>
          </tr>
        ))}
      </Table>

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? 'Зогсоол засах' : 'Зогсоол нэмэх'}>
        {editing && (
          <form onSubmit={save} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Нэр" required>
                <input className="input" value={editing.name} required
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
              </Field>
              <Field label="Код (QR URL-д)" required>
                <input className="input font-mono" value={editing.site_code} required
                  onChange={(e) => setEditing({ ...editing, site_code: e.target.value.toUpperCase() })} />
              </Field>
              <Field label="Бүс">
                <select className="input" value={editing.zone_code}
                  onChange={(e) => setEditing({ ...editing, zone_code: e.target.value })}>
                  {['A', 'B', 'C'].map((z) => <option key={z}>{z}</option>)}
                </select>
              </Field>
              <Field label="Багтаамж">
                <input className="input" type="number" min="0" value={editing.capacity}
                  onChange={(e) => setEditing({ ...editing, capacity: e.target.value })} />
              </Field>
            </div>
            <Field label="Хаяг">
              <input className="input" value={editing.address || ''}
                onChange={(e) => setEditing({ ...editing, address: e.target.value })} />
            </Field>
            <Field label="Тарифын загвар">
              <select className="input" value={editing.tariff_template_id || ''}
                onChange={(e) => setEditing({ ...editing, tariff_template_id: e.target.value })}>
                <option value="">Сонгоогүй</option>
                {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </Field>
            <button className="btn-primary w-full justify-center">Хадгалах</button>
          </form>
        )}
      </Modal>

      <Modal open={!!qrSite} onClose={() => setQrSite(null)} title={`${qrSite?.name} — Төлбөрийн QR`}>
        {qrSite && (
          <div className="text-center space-y-4">
            <img className="mx-auto rounded-xl bg-white p-3 w-64 h-64"
              src={qrUrl(qrSite.site_code)}
              alt={`${qrSite.name} зогсоолын төлбөрийн QR код`} />
            <a href={qrUrl(qrSite.site_code)} download={`${qrSite.site_code}-pay-qr.png`}
              className="btn-primary justify-center w-full">Хэвлэх PNG татах (өндөр нягтрал)</a>
            <div className="flex items-center gap-2 bg-surface-muted rounded-lg px-3 py-2">
              <code className="text-xs flex-1 text-left break-all">{payUrl(qrSite.site_code)}</code>
              <button className="btn-secondary py-1 px-2" aria-label="Хуулах"
                onClick={() => { navigator.clipboard.writeText(payUrl(qrSite.site_code)); useToast()('Хуулагдлаа') }}>
                <Copy size={13} />
              </button>
            </div>
            <p className="text-sm text-slate-400">
              Энэ QR кодыг хэвлэж гарах хаалтны дэргэд байрлуулна. Жолооч утасны камераар уншуулж төлбөрөө төлнө.
            </p>
          </div>
        )}
      </Modal>
    </>
  )
}

function Tariffs() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [editing, setEditing] = useState(null)
  const load = () => api('/api/admin/tariff-templates').then(setRows)
  useEffect(() => { load() }, [])

  const blank = {
    name: '', free_minutes: 30, grace_minutes: 15, prepaid_price: 0,
    extra_hour_price: 2000, daily_cap: '',
    tiers: [{ upto_minutes: 60, price: 1000 }, { upto_minutes: 120, price: 2000 }, { upto_minutes: 180, price: 5000 }],
  }

  const save = async (e) => {
    e.preventDefault()
    try {
      const body = {
        ...editing,
        daily_cap: editing.daily_cap === '' ? null : +editing.daily_cap,
        tiers: editing.tiers.map((t) => ({ upto_minutes: +t.upto_minutes, price: +t.price })),
      }
      if (editing.id) await api(`/api/admin/tariff-templates/${editing.id}`, { method: 'PUT', body })
      else await api('/api/admin/tariff-templates', { method: 'POST', body })
      toast('Хадгалагдлаа'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <>
      <div className="flex justify-end">
        <button className="btn-primary" onClick={() => setEditing(blank)}><Plus size={16} /> Загвар нэмэх</button>
      </div>
      <Table headers={['Нэр', 'Үнэгүй хугацаа', 'Гарах хугацаа', 'Шатлал', 'Нэмэлт цаг', 'Хоногийн дээд', '']}
        empty={rows.length === 0}>
        {rows.map((t) => (
          <tr key={t.id}>
            <td className="td font-medium">{t.name}</td>
            <td className="td font-mono">{t.free_minutes} мин</td>
            <td className="td font-mono">{t.grace_minutes} мин</td>
            <td className="td font-mono text-xs">
              {t.tiers.map((x) => `${x.upto_minutes}мин→${fmt(x.price)}₮`).join(' · ')}
            </td>
            <td className="td font-mono">{fmt(t.extra_hour_price)}₮/цаг</td>
            <td className="td font-mono">{t.daily_cap ? `${fmt(t.daily_cap)}₮` : '-'}</td>
            <td className="td text-right">
              <button className="btn-secondary py-1 text-xs"
                onClick={() => setEditing({ ...t, daily_cap: t.daily_cap ?? '' })}>Засах</button>
            </td>
          </tr>
        ))}
      </Table>

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? 'Тарифын загвар засах' : 'Тарифын загвар нэмэх'} wide>
        {editing && (
          <form onSubmit={save} className="space-y-4">
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
              <Field label="Нэр" required>
                <input className="input" value={editing.name} required
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
              </Field>
              <Field label="Үнэгүй байх хугацаа (мин)">
                <input className="input" type="number" min="0" value={editing.free_minutes}
                  onChange={(e) => setEditing({ ...editing, free_minutes: +e.target.value })} />
              </Field>
              <Field label="Төлбөрийн дараах гарах хугацаа (мин)">
                <input className="input" type="number" min="0" value={editing.grace_minutes}
                  onChange={(e) => setEditing({ ...editing, grace_minutes: +e.target.value })} />
              </Field>
              <Field label="Урьдчилсан захиалгын үнэ (₮)">
                <input className="input" type="number" min="0" value={editing.prepaid_price}
                  onChange={(e) => setEditing({ ...editing, prepaid_price: +e.target.value })} />
              </Field>
              <Field label="Шатлалаас хэтэрсэн цагийн үнэ (₮)">
                <input className="input" type="number" min="0" value={editing.extra_hour_price}
                  onChange={(e) => setEditing({ ...editing, extra_hour_price: +e.target.value })} />
              </Field>
              <Field label="Хоногийн дээд хязгаар (₮, хоосон=хязгааргүй)">
                <input className="input" type="number" min="0" value={editing.daily_cap}
                  onChange={(e) => setEditing({ ...editing, daily_cap: e.target.value })} />
              </Field>
            </div>

            <div>
              <div className="label mb-2">Шатлалын үнэ (хугацаа хүртэл → нийт үнэ)</div>
              <div className="space-y-2">
                {editing.tiers.map((t, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <input className="input w-32" type="number" min="1" value={t.upto_minutes} aria-label="Минут хүртэл"
                      onChange={(e) => {
                        const tiers = [...editing.tiers]; tiers[i] = { ...t, upto_minutes: e.target.value }
                        setEditing({ ...editing, tiers })
                      }} />
                    <span className="text-sm text-slate-400">мин хүртэл →</span>
                    <input className="input w-32" type="number" min="0" value={t.price} aria-label="Үнэ"
                      onChange={(e) => {
                        const tiers = [...editing.tiers]; tiers[i] = { ...t, price: e.target.value }
                        setEditing({ ...editing, tiers })
                      }} />
                    <span className="text-sm text-slate-400">₮</span>
                    <button type="button" className="p-1.5 rounded hover:bg-red-500/10 text-red-400 cursor-pointer"
                      aria-label="Шатлал устгах"
                      onClick={() => setEditing({ ...editing, tiers: editing.tiers.filter((_, j) => j !== i) })}>
                      <Trash2 size={15} />
                    </button>
                  </div>
                ))}
                <button type="button" className="btn-secondary py-1 text-xs"
                  onClick={() => {
                    const last = editing.tiers[editing.tiers.length - 1]
                    setEditing({
                      ...editing,
                      tiers: [...editing.tiers, { upto_minutes: (+last?.upto_minutes || 0) + 60, price: (+last?.price || 0) + 1000 }],
                    })
                  }}>
                  <Plus size={13} /> Шатлал нэмэх
                </button>
              </div>
            </div>
            <button className="btn-primary w-full justify-center">Хадгалах</button>
          </form>
        )}
      </Modal>
    </>
  )
}

function Devices() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [sites, setSites] = useState([])
  const [editing, setEditing] = useState(null)
  const load = () => api('/api/admin/devices').then((d) => setRows(d.filter((x) => x.status !== 'deleted')))
  useEffect(() => { load(); api('/api/admin/sites').then(setSites) }, [])

  const TYPES = { camera: 'LPR камер', barrier: 'Хаалт (barrier)', pax_terminal: 'PAX POS терминал', led: 'LED дэлгэц' }

  const save = async (e) => {
    e.preventDefault()
    try {
      const body = { ...editing, lane_no: +editing.lane_no }
      if (editing.id) await api(`/api/admin/devices/${editing.id}`, { method: 'PUT', body })
      else await api('/api/admin/devices', { method: 'POST', body })
      toast('Хадгалагдлаа'); setEditing(null); load()
    } catch (err) { toast(err.message, 'error') }
  }

  const remove = async (d) => {
    if (!confirm(`"${d.name}" төхөөрөмжийг устгах уу?`)) return
    await api(`/api/admin/devices/${d.id}`, { method: 'DELETE' })
    toast('Устгагдлаа'); load()
  }

  return (
    <>
      <div className="flex justify-end">
        <button className="btn-primary" onClick={() => setEditing({
          site_id: sites[0]?.id || '', name: '', device_type: 'camera', vendor: 'Dahua',
          model: '', ip_address: '', lane_no: 1, lane_dir: 'entry', auto_open: true,
        })}><Plus size={16} /> Төхөөрөмж нэмэх</button>
      </div>
      <Table headers={['Нэр', 'Төрөл', 'Зогсоол', 'Модел', 'IP', 'Эгнээ', 'Чиглэл', 'Callback түлхүүр', '']}
        empty={rows.length === 0}>
        {rows.map((d) => (
          <tr key={d.id}>
            <td className="td font-medium">{d.name}</td>
            <td className="td text-xs">{TYPES[d.device_type] || d.device_type}</td>
            <td className="td text-xs">{d.site_name}</td>
            <td className="td text-xs">{d.model}</td>
            <td className="td font-mono text-xs">{d.ip_address || '-'}</td>
            <td className="td font-mono">{d.lane_no}</td>
            <td className="td text-xs">{d.lane_dir === 'entry' ? 'Орох' : d.lane_dir === 'exit' ? 'Гарах' : 'Хоёулаа'}</td>
            <td className="td font-mono text-[10px] text-slate-500">{d.device_key}</td>
            <td className="td text-right whitespace-nowrap">
              <button className="btn-secondary py-1 text-xs mr-1" onClick={() => setEditing(d)}>Засах</button>
              <button className="btn-danger py-1 text-xs" onClick={() => remove(d)}>Устгах</button>
            </td>
          </tr>
        ))}
      </Table>

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing?.id ? 'Төхөөрөмж засах' : 'Төхөөрөмж нэмэх'}>
        {editing && (
          <form onSubmit={save} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Нэр" required>
                <input className="input" value={editing.name} required
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
              </Field>
              <Field label="Төрөл">
                <select className="input" value={editing.device_type}
                  onChange={(e) => setEditing({ ...editing, device_type: e.target.value })}>
                  {Object.entries(TYPES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Field>
              <Field label="Зогсоол" required>
                <select className="input" value={editing.site_id} required
                  onChange={(e) => setEditing({ ...editing, site_id: e.target.value })}>
                  {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </Field>
              <Field label="Модел">
                <input className="input" value={editing.model || ''} placeholder="ITC436 / DZBL-A / A9000"
                  onChange={(e) => setEditing({ ...editing, model: e.target.value })} />
              </Field>
              <Field label="IP хаяг">
                <input className="input font-mono" value={editing.ip_address || ''} placeholder="192.168.1.108"
                  onChange={(e) => setEditing({ ...editing, ip_address: e.target.value })} />
              </Field>
              <Field label="Эгнээ (lane)">
                <input className="input" type="number" min="1" value={editing.lane_no}
                  onChange={(e) => setEditing({ ...editing, lane_no: e.target.value })} />
              </Field>
              <Field label="Чиглэл">
                <select className="input" value={editing.lane_dir}
                  onChange={(e) => setEditing({ ...editing, lane_dir: e.target.value })}>
                  <option value="entry">Орох</option>
                  <option value="exit">Гарах</option>
                  <option value="both">Хоёулаа</option>
                </select>
              </Field>
            </div>
            {editing.device_type === 'camera' && editing.lane_dir === 'entry' && (
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={editing.auto_open}
                  onChange={(e) => setEditing({ ...editing, auto_open: e.target.checked })} />
                Дугаар уншмагц хаалтыг автоматаар нээх
              </label>
            )}
            <button className="btn-primary w-full justify-center">Хадгалах</button>
          </form>
        )}
      </Modal>
    </>
  )
}
