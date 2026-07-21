// Тохиргоо: Зогсоол / Тарифын загвар / Төхөөрөмж
import { Camera, Check, Copy, DoorOpen, Download, Plus, QrCode, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt } from '../api'
import { Badge, Field, Modal, Table, useToast } from '../components/ui'

// Enter дархад дараагийн талбар руу шилжих (сүүлийнх дээр submit)
function enterToNext(e) {
  if (e.key !== 'Enter' || e.target.tagName === 'BUTTON') return
  e.preventDefault()
  const els = [...e.target.form.querySelectorAll('input, select')].filter((el) => !el.disabled)
  const i = els.indexOf(e.target)
  if (i > -1 && i < els.length - 1) els[i + 1].focus()
  else e.target.form.requestSubmit()
}

export default function Settings() {
  const [tab, setTab] = useState('sites')
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Тохиргоо</h1>
      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {[['sites', 'Зогсоол'], ['devices', 'Төхөөрөмж']].map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>
      {tab === 'sites' && <Sites />}
      {tab === 'devices' && <Devices />}
    </div>
  )
}

// QR зураг — ачаалж чадаагүй бол алдаа + "Дахин үүсгэх" товч харуулна
function QrImage({ code, alt }) {
  const [key, setKey] = useState(0)
  const [err, setErr] = useState(false)
  const retry = () => { setErr(false); setKey((k) => k + 1) }
  if (err) {
    return (
      <div className="mx-auto w-52 h-52 rounded-xl bg-surface-muted flex flex-col items-center justify-center gap-3 text-sm text-slate-400">
        QR ачаалж чадсангүй
        <button type="button" className="btn-primary py-1.5" onClick={retry}>Дахин үүсгэх</button>
      </div>
    )
  }
  return (
    <div>
      <img key={key} className="mx-auto rounded-xl bg-white p-3 w-52 h-52"
        src={`/api/public/qr/${code}.png?v=${key}`} alt={alt} onError={() => setErr(true)} />
      <button type="button" className="text-xs text-slate-500 hover:text-slate-300 mt-1.5 cursor-pointer underline"
        onClick={retry}>QR дахин үүсгэх</button>
    </div>
  )
}

// Орох/гарах хаалтын тоогоор төхөөрөмжийн загварыг динамикаар үүсгэнэ.
// Эгнээ бүр өөрийн камер + хаалттай, ижил lane_no-той (barrier камерынхаа реле-ээр нээгддэг).
function genDevices(entryLanes, exitLanes) {
  const list = []
  for (let i = 1; i <= entryLanes; i++) {
    const suf = entryLanes > 1 ? ` ${i}` : ''
    list.push({ key: `entry_cam_${i}`, name: `Орох камер${suf}`, device_type: 'camera', lane_dir: 'entry', lane_no: i, auto_open: true, icon: Camera })
    list.push({ key: `entry_bar_${i}`, name: `Орох хаалт${suf}`, device_type: 'barrier', lane_dir: 'entry', lane_no: i, auto_open: false, icon: DoorOpen })
  }
  for (let j = 1; j <= exitLanes; j++) {
    const lane = entryLanes + j
    const suf = exitLanes > 1 ? ` ${j}` : ''
    list.push({ key: `exit_cam_${j}`, name: `Гарах камер${suf}`, device_type: 'camera', lane_dir: 'exit', lane_no: lane, auto_open: false, icon: Camera })
    list.push({ key: `exit_bar_${j}`, name: `Гарах хаалт${suf}`, device_type: 'barrier', lane_dir: 'exit', lane_no: lane, auto_open: false, icon: DoorOpen })
  }
  return list
}

function Sites() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [templates, setTemplates] = useState([])
  const [editing, setEditing] = useState(null)
  const [qrSite, setQrSite] = useState(null)
  const [wizard, setWizard] = useState(null)
  const load = () => api('/api/admin/sites').then(setRows)
  useEffect(() => { load(); api('/api/admin/tariff-templates').then(setTemplates) }, [])

  const save = async (e) => {
    e.preventDefault()
    try {
      const body = {
        ...editing,
        capacity: editing.unlimited ? 0 : +editing.capacity,
        tariff_template_id: editing.tariff_template_id || null,
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

  // QR-т кодлогдсонтой ижил линк — backend public_base_url (домэйн) ашиглана
  const payUrl = (s) => s?.pay_url || `${location.origin}/pay?site=${s?.site_code}`
  const qrUrl = (code) => `/api/public/qr/${code}.png`

  const openWizard = () => setWizard({
    step: 1,
    unlimited: false,
    site: { name: '', site_code: '', zone_code: 'A', address: '', capacity: 50, tariff_template_id: templates[0]?.id || '' },
    entryLanes: 1, exitLanes: 1,
    devices: Object.fromEntries(genDevices(1, 1).map((d) => [d.key, { enabled: true, ip_address: '' }])),
    created: null,
    createdDevices: [],
  })

  // Орох/гарах хаалтын тоо өөрчлөгдөхөд төхөөрөмжийн жагсаалтыг дахин үүсгэнэ (IP-г хадгалж)
  const setLanes = (entryLanes, exitLanes) => {
    const e = Math.max(1, Math.min(6, +entryLanes || 1))
    const x = Math.max(1, Math.min(6, +exitLanes || 1))
    const devices = Object.fromEntries(
      genDevices(e, x).map((d) => [d.key, wizard.devices[d.key] || { enabled: true, ip_address: '' }]))
    setWizard({ ...wizard, entryLanes: e, exitLanes: x, devices })
  }

  // Алхам 1 → зогсоол үүсгэх
  const wizardCreateSite = async (e) => {
    e.preventDefault()
    try {
      const s = wizard.site
      const created = await api('/api/admin/sites', {
        method: 'POST',
        body: { ...s, capacity: wizard.unlimited ? 0 : +s.capacity, tariff_template_id: s.tariff_template_id || null },
      })
      setWizard({ ...wizard, step: 2, created })
      load()
    } catch (err) { toast(err.message, 'error') }
  }

  // Алхам 2 → сонгосон төхөөрөмжүүдийг үүсгэх
  const wizardCreateDevices = async (e) => {
    e.preventDefault()
    try {
      const createdDevices = []
      for (const tpl of genDevices(wizard.entryLanes, wizard.exitLanes)) {
        const cfg = wizard.devices[tpl.key]
        if (!cfg.enabled) continue
        const d = await api('/api/admin/devices', {
          method: 'POST',
          body: {
            site_id: wizard.created.id, name: tpl.name, device_type: tpl.device_type,
            vendor: 'Dahua', model: tpl.device_type === 'camera' ? 'IPMECS-2234-IZ' : 'DZBL-A / DZE-BL',
            ip_address: cfg.ip_address, lane_no: tpl.lane_no, lane_dir: tpl.lane_dir, auto_open: tpl.auto_open,
          },
        })
        createdDevices.push(d)
      }
      setWizard({ ...wizard, step: 3, createdDevices })
      toast(`${createdDevices.length} төхөөрөмж холбогдлоо`)
    } catch (err) { toast(err.message, 'error') }
  }

  const copy = (text) => { navigator.clipboard.writeText(text); toast('Хуулагдлаа') }
  const callbackUrl = (key) => `${location.origin}/api/lpr/callback?device_key=${key}`

  return (
    <>
      <div className="flex justify-end">
        <button className="btn-primary" onClick={openWizard}>
          <Plus size={16} /> Зогсоол нэмэх
        </button>
      </div>
      <Table headers={['Нэр', 'Код', 'Бүс', 'Багтаамж', 'Зогсож буй', 'Сул', 'Тариф', 'QR', '']} empty={rows.length === 0}>
        {rows.map((s) => (
          <tr key={s.id}>
            <td className="td font-medium">{s.name}</td>
            <td className="td font-mono">{s.site_code}</td>
            <td className="td">{s.zone_code}</td>
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
      <Modal open={!!wizard} onClose={() => { setWizard(null); load() }} title="Шинэ зогсоол холбох" wide>
        {wizard && (
          <div>
            {/* Алхамын заагч */}
            <div className="flex items-center gap-2 mb-5">
              {[[1, 'Мэдээлэл'], [2, 'Төхөөрөмж'], [3, 'QR ба тохиргоо']].map(([n, label], i) => (
                <div key={n} className="flex items-center gap-2 flex-1">
                  <span className={`w-7 h-7 rounded-full flex items-center justify-center text-sm font-bold shrink-0
                    ${wizard.step > n ? 'bg-accent text-white' : wizard.step === n ? 'bg-accent/20 text-accent border-2 border-accent' : 'bg-surface-muted text-slate-500'}`}>
                    {wizard.step > n ? <Check size={15} /> : n}
                  </span>
                  <span className={`text-sm ${wizard.step === n ? 'text-accent font-medium' : 'text-slate-500'}`}>{label}</span>
                  {i < 2 && <div className="flex-1 h-px bg-surface-border" />}
                </div>
              ))}
            </div>

            {/* Алхам 1: Зогсоолын мэдээлэл — Enter дараагийн талбар руу */}
            {wizard.step === 1 && (
              <form onSubmit={wizardCreateSite} className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Зогсоолын нэр" required>
                    <input className="input" value={wizard.site.name} required autoFocus onKeyDown={enterToNext}
                      onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, name: e.target.value } })} />
                  </Field>
                  <Field label="Код (QR URL-д, жишээ: SITE02)" required>
                    <input className="input font-mono" value={wizard.site.site_code} required onKeyDown={enterToNext}
                      onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, site_code: e.target.value.toUpperCase().replace(/\s/g, '') } })} />
                  </Field>
                  <Field label="Бүс">
                    <select className="input" value={wizard.site.zone_code} onKeyDown={enterToNext}
                      onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, zone_code: e.target.value } })}>
                      {['A', 'B', 'C'].map((z) => <option key={z}>{z}</option>)}
                    </select>
                  </Field>
                  <Field label="Багтаамж">
                    <input className="input" type="number" min="1" value={wizard.unlimited ? '' : wizard.site.capacity}
                      disabled={wizard.unlimited} placeholder={wizard.unlimited ? 'Хязгааргүй' : ''} onKeyDown={enterToNext}
                      onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, capacity: e.target.value } })} />
                    <label className="flex items-center gap-2 mt-1.5 text-xs text-slate-400 cursor-pointer">
                      <input type="checkbox" className="cursor-pointer" checked={wizard.unlimited}
                        onChange={(e) => setWizard({ ...wizard, unlimited: e.target.checked })} />
                      Дүүргэлтгүй (багтаамжийн хязгааргүй)
                    </label>
                  </Field>
                </div>
                <Field label="Хаяг">
                  <input className="input" value={wizard.site.address} onKeyDown={enterToNext}
                    onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, address: e.target.value } })} />
                </Field>
                <Field label="Тарифын загвар">
                  <select className="input" value={wizard.site.tariff_template_id} onKeyDown={enterToNext}
                    onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, tariff_template_id: e.target.value } })}>
                    <option value="">Сонгоогүй</option>
                    {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                </Field>
                {/* Хэдэн хаалттай вэ — орох/гарах эгнээ тус бүрт камер+хаалт үүснэ */}
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Орох хаалт (эгнээ) хэд вэ?">
                    <input className="input" type="number" min="1" max="6" value={wizard.entryLanes} onKeyDown={enterToNext}
                      onChange={(e) => setLanes(e.target.value, wizard.exitLanes)} />
                  </Field>
                  <Field label="Гарах хаалт (эгнээ) хэд вэ?">
                    <input className="input" type="number" min="1" max="6" value={wizard.exitLanes} onKeyDown={enterToNext}
                      onChange={(e) => setLanes(wizard.entryLanes, e.target.value)} />
                  </Field>
                </div>
                <div className="text-xs text-slate-500">
                  Эгнээ тус бүрт нэг камер + нэг хаалт үүснэ (нийт {(wizard.entryLanes + wizard.exitLanes) * 2} төхөөрөмж).
                  Дараагийн алхамд IP хаяг оруулна.
                </div>
                <button className="btn-primary w-full justify-center">Үргэлжлүүлэх →</button>
              </form>
            )}

            {/* Алхам 2: Орох/гарах төхөөрөмж холбох */}
            {wizard.step === 2 && (
              <form onSubmit={wizardCreateDevices} className="space-y-3">
                <div className="text-sm text-slate-400">
                  <b className="text-slate-200">{wizard.created?.name}</b> зогсоолын орох/гарах төхөөрөмжүүдийг сонгоно уу.
                  IP хаягийг дараа нь ч оруулж болно.
                </div>
                {genDevices(wizard.entryLanes, wizard.exitLanes).map((tpl) => {
                  const cfg = wizard.devices[tpl.key]
                  const Icon = tpl.icon
                  return (
                    <div key={tpl.key} className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-colors
                      ${cfg.enabled ? 'border-accent/40 bg-accent/5' : 'border-surface-border bg-surface-muted/30 opacity-60'}`}>
                      <input type="checkbox" checked={cfg.enabled} id={`dev-${tpl.key}`} className="cursor-pointer"
                        onChange={(e) => setWizard({ ...wizard, devices: { ...wizard.devices, [tpl.key]: { ...cfg, enabled: e.target.checked } } })} />
                      <label htmlFor={`dev-${tpl.key}`} className="flex items-center gap-2 w-36 cursor-pointer">
                        <Icon size={16} className={tpl.lane_dir === 'entry' ? 'text-accent' : 'text-amber-400'} />
                        <span className="text-sm font-medium">{tpl.name}</span>
                      </label>
                      <span className="text-xs text-slate-500 w-14">Эгнээ {tpl.lane_no}</span>
                      <input className="input flex-1 font-mono text-xs" placeholder="IP хаяг (заавал биш)" value={cfg.ip_address}
                        disabled={!cfg.enabled} onKeyDown={enterToNext}
                        onChange={(e) => setWizard({ ...wizard, devices: { ...wizard.devices, [tpl.key]: { ...cfg, ip_address: e.target.value } } })} />
                    </div>
                  )
                })}
                <div className="flex gap-2">
                  <button type="button" className="btn-secondary flex-1 justify-center"
                    onClick={() => setWizard({ ...wizard, step: 3, createdDevices: [] })}>Алгасах</button>
                  <button className="btn-primary flex-1 justify-center">Төхөөрөмж холбох →</button>
                </div>
              </form>
            )}

            {/* Алхам 3: QR татах + камерын callback тохиргоо */}
            {wizard.step === 3 && wizard.created && (
              <div className="space-y-4">
                <div className="text-center">
                  <QrImage code={wizard.created.site_code}
                    alt={`${wizard.created.name} зогсоолын төлбөрийн QR код`} />
                  <a href={qrUrl(wizard.created.site_code)} download={`${wizard.created.site_code}-pay-qr.png`}
                    className="btn-primary justify-center mt-3 w-full">
                    <Download size={16} /> QR зураг татах (хэвлэхэд бэлэн)
                  </a>
                  <div className="flex items-center gap-2 bg-surface-muted rounded-lg px-3 py-2 mt-2">
                    <code className="text-xs flex-1 text-left break-all">{payUrl(wizard.created)}</code>
                    <button className="btn-secondary py-1 px-2" onClick={() => copy(payUrl(wizard.created))} aria-label="URL хуулах">
                      <Copy size={13} />
                    </button>
                  </div>
                </div>

                {wizard.createdDevices.length > 0 && (
                  <div>
                    <div className="label mb-2">Камерын ITSAPI callback тохиргоо (камерын Web UI дээр оруулна):</div>
                    <div className="space-y-2">
                      {wizard.createdDevices.filter((d) => d.device_type === 'camera').map((d) => (
                        <div key={d.id} className="bg-surface-muted/40 rounded-lg px-3 py-2">
                          <div className="text-xs font-medium mb-1">{d.name}</div>
                          <div className="flex items-center gap-2">
                            <code className="text-[10px] flex-1 break-all text-slate-400">{callbackUrl(d.device_key)}</code>
                            <button className="btn-secondary py-1 px-2" onClick={() => copy(callbackUrl(d.device_key))} aria-label="Callback URL хуулах">
                              <Copy size={13} />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                    <div className="text-xs text-slate-500 mt-2">
                      Хаалтны IP хаягийг Тохиргоо → Төхөөрөмж хэсгээс хэзээ ч засаж болно.
                    </div>
                  </div>
                )}

                <button className="btn-primary w-full justify-center" onClick={() => { setWizard(null); load() }}>
                  <Check size={16} /> Дуусгах
                </button>
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal open={!!editing} onClose={() => setEditing(null)} title="Зогсоол засах">
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
                <input className="input" type="number" min="1" value={editing.unlimited ? '' : editing.capacity}
                  disabled={editing.unlimited} placeholder={editing.unlimited ? 'Хязгааргүй' : ''}
                  onChange={(e) => setEditing({ ...editing, capacity: e.target.value })} />
                <label className="flex items-center gap-2 mt-1.5 text-xs text-slate-400 cursor-pointer">
                  <input type="checkbox" className="cursor-pointer" checked={!!editing.unlimited}
                    onChange={(e) => setEditing({ ...editing, unlimited: e.target.checked, capacity: e.target.checked ? 0 : (editing.capacity || 50) })} />
                  Дүүргэлтгүй (багтаамжийн хязгааргүй)
                </label>
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
            <QrImage code={qrSite.site_code}
              alt={`${qrSite.name} зогсоолын төлбөрийн QR код`} />
            <a href={qrUrl(qrSite.site_code)} download={`${qrSite.site_code}-pay-qr.png`}
              className="btn-primary justify-center w-full">Хэвлэх PNG татах (өндөр нягтрал)</a>
            <div className="flex items-center gap-2 bg-surface-muted rounded-lg px-3 py-2">
              <code className="text-xs flex-1 text-left break-all">{payUrl(qrSite)}</code>
              <button className="btn-secondary py-1 px-2" aria-label="Хуулах"
                onClick={() => { navigator.clipboard.writeText(payUrl(qrSite)); useToast()('Хуулагдлаа') }}>
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
  const [showDeleted, setShowDeleted] = useState(false)
  const load = (withDeleted = showDeleted) =>
    api(`/api/admin/devices${withDeleted ? '?include_deleted=true' : ''}`).then(setRows)
  useEffect(() => { load(); api('/api/admin/sites').then(setSites) }, [])
  useEffect(() => { load(showDeleted) }, [showDeleted])

  // Санамсаргүй устгасан төхөөрөмжийг status='active' болгож сэргээнэ (түлхүүр, тохиргоо хэвээр)
  const restore = async (d) => {
    try {
      await api(`/api/admin/devices/${d.id}`, { method: 'PUT', body: { status: 'active' } })
      toast(`"${d.name}" сэргээгдлээ`); load()
    } catch (err) { toast(err.message, 'error') }
  }

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
      <div className="flex justify-between items-center">
        <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
          <input type="checkbox" className="cursor-pointer" checked={showDeleted}
            onChange={(e) => setShowDeleted(e.target.checked)} />
          Устгагдсан төхөөрөмж харуулах (сэргээх боломжтой)
        </label>
        <button className="btn-primary" onClick={() => setEditing({
          site_id: sites[0]?.id || '', name: '', device_type: 'camera', vendor: 'Dahua',
          model: '', ip_address: '', lane_no: 1, lane_dir: 'entry', auto_open: true,
        })}><Plus size={16} /> Төхөөрөмж нэмэх</button>
      </div>
      {/* Зогсоол тус бүрээр бүлэглэн харуулна */}
      {rows.length === 0 ? (
        <div className="card text-sm text-slate-500 py-6 text-center">Төхөөрөмж бүртгэгдээгүй байна</div>
      ) : (() => {
        const bySite = {}
        for (const d of rows) {
          const key = d.site_name || 'Зогсоолгүй'
          ;(bySite[key] = bySite[key] || []).push(d)
        }
        const order = sites.map((s) => s.name).filter((n) => bySite[n])
          .concat(Object.keys(bySite).filter((n) => !sites.some((s) => s.name === n)))
        return order.map((siteName) => {
          const list = bySite[siteName]
          const cams = list.filter((d) => d.device_type === 'camera').length
          const bars = list.filter((d) => d.device_type === 'barrier').length
          return (
            <div key={siteName} className="space-y-2">
              <div className="flex items-center gap-2 mt-3">
                <h3 className="font-semibold text-accent">{siteName}</h3>
                <span className="text-xs text-slate-500">
                  {list.length} төхөөрөмж{cams ? ` · ${cams} камер` : ''}{bars ? ` · ${bars} хаалт` : ''}
                </span>
              </div>
              <Table headers={['Нэр', 'Төрөл', 'Модел', 'IP', 'Эгнээ', 'Чиглэл', 'Callback түлхүүр', '']} empty={false}>
                {list.map((d) => (
                  <tr key={d.id} className={d.status === 'deleted' ? 'opacity-50' : ''}>
                    <td className="td font-medium">
                      {d.name}
                      {d.status === 'deleted' && <span className="ml-1.5 text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">устгагдсан</span>}
                    </td>
                    <td className="td text-xs">{TYPES[d.device_type] || d.device_type}</td>
                    <td className="td text-xs">{d.model}</td>
                    <td className="td font-mono text-xs">{d.ip_address || '-'}</td>
                    <td className="td font-mono">{d.lane_no}</td>
                    <td className="td text-xs">{d.lane_dir === 'entry' ? 'Орох' : d.lane_dir === 'exit' ? 'Гарах' : 'Хоёулаа'}</td>
                    <td className="td font-mono text-[10px] text-slate-500">{d.device_key}</td>
                    <td className="td text-right whitespace-nowrap">
                      {d.status === 'deleted' ? (
                        <button className="btn-primary py-1 text-xs" onClick={() => restore(d)}>Сэргээх</button>
                      ) : (
                        <>
                          <button className="btn-secondary py-1 text-xs mr-1" onClick={() => setEditing(d)}>Засах</button>
                          <button className="btn-danger py-1 text-xs" onClick={() => remove(d)}>Устгах</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </Table>
            </div>
          )
        })
      })()}

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
