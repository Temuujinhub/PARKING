// Зогсоол үүсгэх 3 алхамт wizard — мэдээлэл → төхөөрөмж → QR ба тохиргоо
import { Check, Copy, Download } from 'lucide-react'
import { api } from '../../api'
import { Field, Modal, useToast } from '../../components/ui'
import { IP_HINT, clampNum, isIp, normalizeCode, normalizeIp } from '../../validation'
import { enterToNext, genDevices, payUrl, qrUrl, QrImage } from './shared'

export default function SiteWizardModal({ wizard, setWizard, templates, reload }) {
  const toast = useToast()
  const close = () => { setWizard(null); reload() }

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
      const r = wizard.rules || {}
      // Хоосон үлдээсэн дүрэм = ерөнхий утга (давхаргад бичигдэхгүй)
      const num = (v, max) => (v === '' || v === null || v === undefined ? undefined : clampNum(v, { min: 0, max, fallback: 0 }))
      const payment_rules = {
        exit_rules: { no_session_fee: num(r.no_session_fee, 1_000_000), min_stay_seconds: num(r.min_stay_seconds, 3600) },
        entry_plate_rules: { policy: r.policy || undefined },
        blacklist_rules: { block_exit_debt_count: num(r.block_exit_debt_count, 100) },
      }
      const created = await api('/api/admin/sites', {
        method: 'POST',
        body: {
          ...s,
          capacity: wizard.unlimited ? 0 : clampNum(s.capacity, { min: 0, max: 100000, fallback: 0 }),
          tariff_template_id: s.tariff_template_id || null,
          payment_rules,
        },
      })
      setWizard({ ...wizard, step: 2, created })
      reload()
    } catch (err) { toast(err.message, 'error') }
  }

  // Алхам 2 → сонгосон төхөөрөмжүүдийг үүсгэх
  const wizardCreateDevices = async (e) => {
    e.preventDefault()
    try {
      // Буруу IP-тэй төхөөрөмж үүсгэвэл хожим «холбогдохгүй» болж чимээгүй унана
      const badIp = genDevices(wizard.entryLanes, wizard.exitLanes)
        .find((t) => wizard.devices[t.key]?.enabled && !isIp(wizard.devices[t.key].ip_address))
      if (badIp) { toast(`${badIp.name}: IP хаяг буруу — ${IP_HINT}`, 'error'); return }
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
    <Modal open={!!wizard} onClose={close} title="Шинэ зогсоол холбох" wide>
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
                    maxLength={30}
                    onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, site_code: normalizeCode(e.target.value) } })} />
                </Field>
                <Field label="Бүс">
                  <select className="input" value={wizard.site.zone_code} onKeyDown={enterToNext}
                    onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, zone_code: e.target.value } })}>
                    {['A', 'B', 'C'].map((z) => <option key={z}>{z}</option>)}
                  </select>
                </Field>
                <Field label="Багтаамж">
                  <input className="input" type="number" min="1" max="100000" step="1" value={wizard.unlimited ? '' : wizard.site.capacity}
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

              {/* Зогсоолын төлбөрийн дүрэм — хамгийн их асуугддаг 4 тохиргоо.
                  Хоосон = ерөнхий утга; бүгдийг нь дараа «Төлбөрийн дүрэм» табаас
                  зогсоол бүрээр өөрчилж болно. */}
              <details className="rounded-lg border border-slate-700 px-3 py-2">
                <summary className="cursor-pointer text-sm font-medium py-1">
                  Төлбөрийн дүрэм
                  <span className="ml-2 text-xs text-slate-500">· хоосон = ерөнхий утга, дараа нь табаас засна</span>
                </summary>
                <div className="mt-2 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <label className="flex items-center gap-2 cursor-pointer select-none text-sm">
                      <input type="checkbox" className="w-4 h-4 accent-accent" checked={!!wizard.site.registered_only}
                        onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, registered_only: e.target.checked } })} />
                      Зөвхөн гэрээт машин нэвтэрнэ
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer select-none text-sm">
                      <input type="checkbox" className="w-4 h-4 accent-accent" checked={!!wizard.site.no_charge}
                        onChange={(e) => setWizard({ ...wizard, site: { ...wizard.site, no_charge: e.target.checked } })} />
                      Төлбөр авахгүй зогсоол
                    </label>
                    <Field label="Орох цаг олдоогүй машины суурь хураамж (₮)">
                      <input className="input font-mono" type="number" min="0" max="1000000" step="500" placeholder="ерөнхий (2000)"
                        value={wizard.rules?.no_session_fee ?? ''} onKeyDown={enterToNext}
                        onChange={(e) => setWizard({ ...wizard, rules: { ...wizard.rules, no_session_fee: e.target.value } })} />
                    </Field>
                    <Field label="Эрт гарахад хаалт нээхгүй (сек)">
                      <input className="input font-mono" type="number" min="0" max="3600" step="5" placeholder="ерөнхий (0 = унтраалттай)"
                        value={wizard.rules?.min_stay_seconds ?? ''} onKeyDown={enterToNext}
                        onChange={(e) => setWizard({ ...wizard, rules: { ...wizard.rules, min_stay_seconds: e.target.value } })} />
                    </Field>
                    <Field label="Формат буруу дугаарт орох хаалт">
                      <select className="input" value={wizard.rules?.policy || ''} onKeyDown={enterToNext}
                        onChange={(e) => setWizard({ ...wizard, rules: { ...wizard.rules, policy: e.target.value } })}>
                        <option value="">ерөнхий</option>
                        <option value="open">Шууд нээнэ</option>
                        <option value="hold">Түр барина, дараа нь нээнэ</option>
                        <option value="strict">Түр барина, нээхгүй</option>
                      </select>
                    </Field>
                    <Field label="Хэдэн өртэй машиныг гарцад саатуулах">
                      <input className="input font-mono" type="number" min="0" max="100" step="1" placeholder="ерөнхий (3)"
                        value={wizard.rules?.block_exit_debt_count ?? ''} onKeyDown={enterToNext}
                        onChange={(e) => setWizard({ ...wizard, rules: { ...wizard.rules, block_exit_debt_count: e.target.value } })} />
                    </Field>
                  </div>
                </div>
              </details>
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
                    <input className={`input flex-1 font-mono text-xs${isIp(cfg.ip_address) ? '' : ' input-error'}`}
                      placeholder="IP хаяг (заавал биш)" value={cfg.ip_address} inputMode="decimal" maxLength={15}
                      disabled={!cfg.enabled} onKeyDown={enterToNext}
                      onChange={(e) => setWizard({ ...wizard, devices: { ...wizard.devices, [tpl.key]: { ...cfg, ip_address: normalizeIp(e.target.value) } } })} />
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

              <button className="btn-primary w-full justify-center" onClick={close}>
                <Check size={16} /> Дуусгах
              </button>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
