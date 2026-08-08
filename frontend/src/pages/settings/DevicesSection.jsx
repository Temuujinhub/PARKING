// Төхөөрөмж — LPR камер, хаалт, POS терминал; зогсоол тус бүрээр бүлэглэн харуулна
import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { Field, Modal, Table, useToast } from '../../components/ui'

// Эгнээний DEFAULT дугаар чиглэлээр: орох=1, гарах=2. Ихэнх зогсоол нэг орох,
// нэг гарах эгнээтэй бөгөөд эгнээ бүрд өөр дугаар өгөх ЁСТОЙ (нэг зогсоолын нэг
// эгнээнд нэг л камер байхыг backend _assert_lane_free шалгадаг). Өмнө нь хоёулаа
// 1-ээр гардаг тул гарах камер нэмэхэд алдаа өгч, хэрэглэгч гараар 2 болгодог байв.
// Зогсоолын визард аль хэдийн ийм дугаарлалт үүсгэдэг — гараар нэмэхэд ч ижил.
const LANE_DEFAULT = { entry: 1, exit: 2, both: 1 }

export default function DevicesSection() {
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [sites, setSites] = useState([])
  const [editing, setEditing] = useState(null)
  const [showDeleted, setShowDeleted] = useState(false)
  // Хаалтыг backend (ensure_lane_barriers) камер бүрд АВТОМАТААР үүсгэж/сэргээж/
  // зөөж байдаг тул энэ жагсаалтад тусдаа мөр болж харагдах шаардлагагүй — зөвхөн
  // жагсаалтыг хоёр дахин уртасгаж, «яагаад хоосон IP-тэй вэ» гэсэн эргэлзээ
  // төрүүлдэг. Хаалтыг Хаалтны удирдлага хуудаснаас нээж/хаана. Оношилгоонд
  // (өнчин/давхар хаалт) хэрэгтэй үед доорх сэлгүүрээр гаргана.
  const [showBarriers, setShowBarriers] = useState(false)
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

  // Ирээдүйн өргөтгөлүүд (киоск/мэдрэгч/EV) бүртгэлийн түвшинд бэлэн — тоног
  // төхөөрөмж суумагц бүртгээд IP-ээр нь мониторинг (health TCP) шууд ажиллана.
  const TYPES = {
    camera: 'LPR камер', barrier: 'Хаалт (barrier)', pax_terminal: 'PAX POS терминал',
    led: 'LED дэлгэц', kiosk: 'Төлбөрийн киоск', guidance: 'Сул зайн мэдрэгч/чиглүүлэгч',
    ev_charger: 'EV цэнэглэгч',
  }

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
      <div className="flex justify-between items-center gap-3 flex-wrap">
        <div className="flex flex-col gap-1.5">
          <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
            <input type="checkbox" className="cursor-pointer" checked={showDeleted}
              onChange={(e) => setShowDeleted(e.target.checked)} />
            Устгагдсан төхөөрөмж харуулах (сэргээх боломжтой)
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
            <input type="checkbox" className="cursor-pointer" checked={showBarriers}
              onChange={(e) => setShowBarriers(e.target.checked)} />
            Авто үүссэн хаалтыг харуулах (оношилгоонд)
          </label>
        </div>
        <button className="btn-primary" onClick={() => setEditing({
          site_id: sites[0]?.id || '', name: '', device_type: 'camera', vendor: 'Dahua',
          model: '', ip_address: '', lane_no: LANE_DEFAULT.entry, lane_dir: 'entry', auto_open: true,
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
          const all = bySite[siteName]
          const cams = all.filter((d) => d.device_type === 'camera').length
          const bars = all.filter((d) => d.device_type === 'barrier').length
          // Хаалтыг нуусан ч ТООГ нь харуулна — «хаалт бүртгэгдсэн үү» гэдгийг
          // жагсаалтыг сунгалгүйгээр батална.
          const list = showBarriers ? all : all.filter((d) => d.device_type !== 'barrier')
          return (
            <div key={siteName} className="space-y-2">
              <div className="flex items-center gap-2 mt-3">
                <h3 className="font-semibold text-accent">{siteName}</h3>
                <span className="text-xs text-slate-500">
                  {all.length} төхөөрөмж{cams ? ` · ${cams} камер` : ''}
                  {bars ? ` · ${bars} хаалт${showBarriers ? '' : ' (авто, нуусан)'}` : ''}
                </span>
              </div>
              <Table headers={['Нэр', 'Төрөл', 'Модел', 'IP', 'Эгнээ', 'Чиглэл', 'Callback түлхүүр', 'Гадны хандалт', '']}
                empty={list.length === 0}>
                {list.map((d) => (
                  <tr key={d.id} className={d.status === 'deleted' ? 'opacity-50' : ''}>
                    <td className="td font-medium">
                      {d.name}
                      {d.status === 'deleted' && <span className="ml-1.5 text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded">устгагдсан</span>}
                      {/* IP эсвэл эгнээ давхардсан — хаалт аль камер руу явахаа
                          мэдэхгүй болох, камер түгжигдэх эрсдэлтэй */}
                      {d.ip_conflict && (
                        <span className="ml-1.5 text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded cursor-help whitespace-nowrap"
                          title={d.ip_conflict}>⚠ давхцал</span>
                      )}
                      {/* Дотоод хаалт нь зогсолт эхлүүлдэггүй — жагсаалтаас шууд ялгаж харагдана */}
                      {d.nested_inner && (
                        <span className="ml-1.5 text-[10px] text-sky-400 bg-sky-500/10 px-1.5 py-0.5 rounded cursor-help whitespace-nowrap"
                          title="Дотоод (давхар) зогсоолын хаалт. Зогсолт эхлүүлэхгүй/дуусгахгүй — зөвхөн төлбөрийн тоолуурыг зогсоож/үргэлжлүүлнэ.">
                          дотоод
                        </span>
                      )}
                    </td>
                    <td className="td text-xs">{TYPES[d.device_type] || d.device_type}</td>
                    <td className="td text-xs">{d.model}</td>
                    <td className="td font-mono text-xs">{d.ip_address || '-'}</td>
                    <td className="td font-mono">{d.lane_no}</td>
                    <td className="td text-xs">{d.lane_dir === 'entry' ? 'Орох' : d.lane_dir === 'exit' ? 'Гарах' : 'Хоёулаа'}</td>
                    <td className="td font-mono text-[10px] text-slate-500">{d.device_key}</td>
                    <td className="td">
                      {d.foreign_sessions?.length > 0 ? (
                        <div
                          className="cursor-help"
                          title={`Камерын нэвтрэлтийн логоос: манай серверээс ӨӨР систем/хүн камерт хандаж байна.\n${d.foreign_sessions.map((s) => `${s.user} @ ${s.ip} — сүүлд ${s.last}`).join('\n')}\nШалгасан: ${d.foreign_checked_at ? new Date(d.foreign_checked_at + 'Z').toLocaleTimeString() : '?'}`}
                        >
                          {d.foreign_sessions.slice(0, 2).map((s) => (
                            <div key={s.user + s.ip} className="text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded mb-0.5 inline-block mr-1 whitespace-nowrap">
                              ⚠ {s.user}@{s.ip}
                            </div>
                          ))}
                          {d.foreign_sessions.length > 2 && (
                            <span className="text-[10px] text-red-400">+{d.foreign_sessions.length - 2}</span>
                          )}
                        </div>
                      ) : (
                        d.device_type === 'camera' && d.foreign_checked_at ? (
                          <span className="text-[10px] text-slate-600" title={`Гадны хандалт илрээгүй. Шалгасан: ${new Date(d.foreign_checked_at + 'Z').toLocaleTimeString()}`}>—</span>
                        ) : null
                      )}
                    </td>
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
                {!editing.id && +editing.lane_no === LANE_DEFAULT[editing.lane_dir] && (
                  <div className="text-[11px] text-slate-500 mt-1">
                    Чиглэлээр автоматаар (орох=1, гарах=2) — өөрчилж болно.
                  </div>
                )}
              </Field>
              <Field label="Чиглэл">
                <select className="input" value={editing.lane_dir}
                  onChange={(e) => {
                    const dir = e.target.value
                    // Хэрэглэгч эгнээг ГАРААР өөрчилсөн бол хүндэтгэнэ: зөвхөн өмнөх
                    // чиглэлийн default утга хэвээр байвал шинэ default руу сэлгэнэ.
                    // Засварлаж буй төхөөрөмжийн эгнээг хэзээ ч дуугүй өөрчлөхгүй —
                    // тэр нь хаалтыг нь ард нь зөөх сүлжээ үйлдэл болно.
                    const auto = !editing.id && +editing.lane_no === LANE_DEFAULT[editing.lane_dir]
                    setEditing({ ...editing, lane_dir: dir, ...(auto ? { lane_no: LANE_DEFAULT[dir] } : {}) })
                  }}>
                  <option value="entry">Орох</option>
                  <option value="exit">Гарах</option>
                  <option value="both">Хоёулаа</option>
                </select>
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Нэвтрэх нэр">
                <input className="input font-mono" value={editing.username || ''} placeholder="admin"
                  autoComplete="off"
                  onChange={(e) => setEditing({ ...editing, username: e.target.value })} />
              </Field>
              <Field label="Нууц үг">
                <input className="input font-mono" type="password" autoComplete="new-password"
                  value={editing.password ?? ''}
                  placeholder={editing.password_set ? '•••••• (хадгалагдсан)' : 'Ерөнхий тохиргоог ашиглана'}
                  onChange={(e) => setEditing({ ...editing, password: e.target.value })} />
              </Field>
            </div>
            <div className="text-xs text-slate-400 -mt-1">
              Энэ төхөөрөмжийн ӨӨРИЙН нэвтрэлт. Хоосон үлдээвэл системийн ерөнхий
              тохиргоо (.env) үйлчилнэ. Зогсоол бүрийн камер өөр нууц үгтэй бол энд бичнэ.
              {editing.password_set && ' Нууц үгийг хоосон болгож хадгалвал устана.'}
            </div>
            {editing.device_type === 'camera' && editing.lane_dir === 'entry' && (
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={editing.auto_open}
                  onChange={(e) => setEditing({ ...editing, auto_open: e.target.checked })} />
                Дугаар уншмагц хаалтыг автоматаар нээх
              </label>
            )}
            {/* Давхар зогсоол НЭГ зогсоол дотор: доторх талбайн орох/гарах камер.
                Session нээхгүй/хаахгүй — зөвхөн төлбөрийн тоолуурыг зогсоож/
                үргэлжлүүлнэ. Зогсоолыг хоёр болгож салгах шаардлагагүй. */}
            {editing.device_type === 'camera' && (
              <div className="rounded-lg border border-slate-700 px-3 py-2">
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input type="checkbox" checked={!!editing.nested_inner}
                    onChange={(e) => setEditing({ ...editing, nested_inner: e.target.checked })} />
                  Дотоод (давхар) зогсоолын камер
                </label>
                <div className="text-[11px] text-slate-500 mt-1.5">
                  Нэг талбай дотор жижиг зогсоол байвал (гадна талбай → доторх зогсоол)
                  доторх хаалтны камеруудад үүнийг тэмдэглэнэ. Ийм камер зогсолт
                  {' '}<b>эхлүүлэхгүй, дуусгахгүй</b> — зөвхөн:
                  <br />• <b>Орох</b> чиглэл → машин дотогш оров, төлбөрийн тоолуур <b>зогсоно</b>
                  <br />• <b>Гарах</b> чиглэл → машин гадагш гарав, тоолуур <b>үргэлжилнэ</b>
                  <br />Хаалт нь хэвийн нээгдэнэ. Доторх хаалт өөрийн эгнээний дугаартай
                  байх ёстой (гаднахтай давхцуулахгүй) — доторх хаалт автоматаар үүснэ.
                  <br />Тоолуур зогсох дээд хугацааг Тохиргоо → Зогсоол → Засах дээрээс
                  тохируулна (хоосон = 4 цаг).
                </div>
              </div>
            )}
            <button className="btn-primary w-full justify-center">Хадгалах</button>
          </form>
        )}
      </Modal>
    </>
  )
}
