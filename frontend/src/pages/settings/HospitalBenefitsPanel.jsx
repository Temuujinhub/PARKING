import { useEffect, useState } from 'react'
import { KeyRound, Plus, ShieldCheck, Trash2 } from 'lucide-react'
import { api } from '../../api'

const URL = '/api/admin/hospital-integrations'
const emptyForm = { name: '', site_id: '', daily_minutes: 120, is_active: false }
const focus = 'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed'
const button = 'min-h-11 rounded-lg border border-surface-border px-3 py-2 text-sm text-slate-200 hover:bg-surface-muted ' + focus
const danger = 'min-h-11 rounded-lg border border-red-800 px-3 py-2 text-sm text-red-300 hover:bg-red-950 ' + focus
const primary = 'min-h-11 rounded-lg bg-emerald-800 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-900 ' + focus

export default function HospitalBenefitsPanel() {
  const [data, setData] = useState(null)
  const [sites, setSites] = useState([])
  const [form, setForm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [secret, setSecret] = useState(null)
  async function load() {
    const [config, allSites] = await Promise.all([api(URL), api('/api/admin/sites')])
    setData(config); setSites(allSites)
  }
  useEffect(() => { load().catch(e => setError(e.message)) }, [])

  async function save(event) {
    event.preventDefault()
    setError(''); setNotice('')
    const minutes = Number(form.daily_minutes)
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
      setError('Өдрийн лимит 1–1440 бүхэл минут байна.'); return
    }
    setBusy(true)
    try {
      await api(form.id ? URL + '/' + form.id : URL, { method: form.id ? 'PUT' : 'POST', body: {
        name: form.name.trim(), site_id: form.site_id || null, daily_minutes: minutes, is_active: form.is_active,
      } })
      setForm(null)
      setNotice('Хадгалагдлаа. Шинэ лимит дараа шинээр олгох өдрийн эрхэд үйлчилнэ.')
      await load()
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  async function rotate(row) {
    if (row.key_set && !window.confirm('Одоогийн API түлхүүр тэр даруй хүчингүй болно. Эмнэлгийн системд шинэ түлхүүрийг суулгахад бэлэн үү?')) return
    setError(''); setNotice(''); setBusy(true)
    try {
      const result = await api(URL + '/' + row.id + '/rotate-key', { method: 'POST' })
      setSecret({ id: row.id, name: row.name, value: result.signing_secret })
      await load()
      setNotice('Түлхүүр үүслээ. Доорх утга зөвхөн энэ удаа харагдана.')
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  async function remove(row) {
    const msg = row.has_history
      ? `«${row.name}» холболтыг устгах уу?\n\nЭнэ холболтоор эрх олгож байсан тул түүх (тайлан, зогсолтын хөнгөлөлт) хадгалагдана. Холболт идэвхгүй болж, API түлхүүр тэр даруй хүчингүй болох бөгөөд жагсаалтаас алга болно.`
      : `«${row.name}» холболтыг БҮРМӨСӨН устгах уу? Энэ үйлдлийг буцаах боломжгүй.`
    if (!window.confirm(msg)) return
    setError(''); setNotice(''); setBusy(true)
    try {
      const result = await api(URL + '/' + row.id, { method: 'DELETE' })
      if (form?.id === row.id) setForm(null)
      if (secret?.id === row.id) setSecret(null)
      await load()
      setNotice(result.mode === 'archived'
        ? `«${row.name}» устгагдлаа — түүх хадгалагдсан, түлхүүр хүчингүй болсон.`
        : `«${row.name}» бүрмөсөн устгагдлаа.`)
    } catch (e) {
      setError(e.message)
      if (form?.id === row.id) setForm(null)
      load().catch(() => {})   // өөр админ аль хэдийн устгасан бол жагсаалт шинэчлэгдэнэ
    } finally { setBusy(false) }
  }

  async function copySecret() {
    try {
      await navigator.clipboard.writeText(secret.value)
      setNotice('Түлхүүр хуулагдлаа. Эмнэлгийн серверийн нууц тохиргоонд хадгална уу.')
    } catch { setError('Хуулж чадсангүй. Түлхүүрийг талбараас сонгож хуулна уу.') }
  }

  return <section className="space-y-4" aria-labelledby="hospital-title">
    <div className="card space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id="hospital-title" className="font-semibold flex items-center gap-2"><ShieldCheck size={18} aria-hidden="true" /> Эмнэлгийн өдрийн хөнгөлөлт</h3>
        <button type="button" className={primary + ' flex items-center gap-2'} disabled={busy || !data}
          onClick={() => { setForm({ ...emptyForm }); setError(''); setNotice('') }}>
          <Plus size={16} aria-hidden="true" /> Холболт нэмэх
        </button>
      </div>
      <p className="text-sm text-slate-300">Эмнэлгийн систем баталгаажуулсан дугаарт нэг өдөр, нэг зогсоолд тохируулсан минутын лимит үйлчилнэ. Дахин орж гарсан ч лимит шинээр нэмэгдэхгүй.</p>
      <p className="text-sm text-slate-300">Бүтэн зогсолтын төлбөрөөс ашиглах үнэгүй минутын тарифын мөнгөн дүнг хасна. Жишээ: 3 цаг 5,000₮, эхний 2 цаг 2,000₮ бол 3,000₮ төлнө.</p>
      <p className="text-sm text-slate-300">Эхний зогсолт 60 минут бол 120 минутын лимитээс 60 минут дараагийн оролтод үлдэнэ. Өдрийг Улаанбаатарын цагаар, орсон өдрөөр тооцно; дараагийн өдөр эмнэлгийн шинэ баталгаажуулалт шаардлагатай.</p>
      <p className="text-sm text-slate-300">Эмнэлгийн баталгаажуулалтыг төлбөрийн QR эсвэл картын гүйлгээ үүсгэхээс өмнө илгээнэ. Баталгаажуулалт өөрөө хаалт нээх команд илгээхгүй.</p>
    </div>
    {error && <div role="alert" className="card border border-red-500 text-sm text-slate-100">{error}</div>}
    <div role="status" aria-live="polite" className="text-sm text-slate-200">{notice}</div>
    {!data && !error && <p className="text-sm text-slate-300">Холболтуудыг ачаалж байна…</p>}
    {data && !data.encryption_ready && <div className="card text-sm text-slate-200">
      API түлхүүр үүсгэхэд серверийн нууц утгын шифрлэлтийг эхлээд тохируулах шаардлагатай.
    </div>}
    {form && <form onSubmit={save} className="card space-y-4" aria-label="Эмнэлгийн холболт засах">
      <div className="grid gap-4 md:grid-cols-2">
        <label className="block text-sm text-slate-200">Эмнэлэг / бүртгэлийн системийн нэр
          <input className={'input mt-1 w-full ' + focus} required maxLength={120} value={form.name}
            disabled={busy} onChange={e => setForm({ ...form, name: e.target.value })} />
        </label>
        <label className="block text-sm text-slate-200">Холбох зогсоол
          <select className={'input mt-1 w-full ' + focus} value={form.site_id || ''} disabled={busy || form.key_set}
            required={!data.can_leave_unassigned} onChange={e => setForm({ ...form, site_id: e.target.value })}>
            <option value="">{data.can_leave_unassigned ? 'Одоогоор оноохгүй' : 'Зогсоол сонгох'}</option>
            {sites.map(s => <option key={s.id} value={s.id}>{s.name} ({s.site_code}){s.is_active ? '' : ' — идэвхгүй'}</option>)}
          </select>
          {form.key_set && <span className="block mt-1 text-xs text-slate-300">Өөр зогсоол холбох бол тусдаа холболт үүсгэнэ.</span>}
        </label>
        <label className="block text-sm text-slate-200">Өдрийн лимит (минут)
          <input className={'input mt-1 w-full ' + focus} type="number" inputMode="numeric" min="1" max="1440" step="1" required
            value={form.daily_minutes} disabled={busy} onChange={e => setForm({ ...form, daily_minutes: e.target.value })} />
          <span className="block mt-1 text-xs text-slate-300">120 минут = 2 цаг. Өнөөдрийн олгосон лимит, өмнөх төлбөрийг өөрчлөхгүй.</span>
        </label>
        <label className="flex items-center gap-3 text-sm text-slate-200">
          <input type="checkbox" className={'h-5 w-5 accent-emerald-800 ' + focus} checked={form.is_active}
            disabled={busy || !form.key_set || !form.site_id} onChange={e => setForm({ ...form, is_active: e.target.checked })} />
          Холболт идэвхтэй
        </label>
      </div>
      <p className="text-xs text-slate-300">Дараалал: хадгалах → зогсоол сонгох → түлхүүр үүсгэх → эмнэлгийн серверт тохируулах → идэвхжүүлэх.</p>
      <div className="flex gap-2">
        <button className={primary} disabled={busy}>{busy ? 'Хадгалж байна…' : 'Хадгалах'}</button>
        <button type="button" className={button} disabled={busy} onClick={() => setForm(null)}>Болих</button>
      </div>
    </form>}
    {secret && <div className="card space-y-3 border border-emerald-700">
      <h4 className="font-semibold">{secret.name} — шинэ нууц түлхүүр</h4>
      <label className="block text-sm">Гарын үсгийн түлхүүр (зөвхөн нэг удаа харагдана)
        <input readOnly autoComplete="off" spellCheck={false} value={secret.value} className={'input font-mono mt-1 w-full ' + focus} />
      </label>
      <p className="text-sm text-slate-300">Зөвхөн эмнэлгийн серверийн нууц тохиргоонд хадгална. Browser, утасны аппын код, URL эсвэл нийтэд харагдах логт оруулахгүй.</p>
      <div className="flex gap-2"><button type="button" className={button} onClick={copySecret}>Хуулах</button>
        <button type="button" className={button} onClick={() => setSecret(null)}>Хадгалсан, хаах</button></div>
    </div>}
    <div className="grid gap-3">
      {data?.integrations.length === 0 && <p className="card text-sm text-slate-300">Эмнэлгийн холболт үүсгээгүй байна. Зогсоол автоматаар оноохгүй.</p>}
      {data?.integrations.map(row => <article key={row.id} className="card space-y-3">
        <div className="flex flex-wrap justify-between gap-2">
          <h4 className="font-semibold">{row.name}</h4>
          <span className="text-sm text-slate-200">{row.is_active ? '● Идэвхтэй' : '○ Идэвхгүй'} · {row.daily_minutes} минут / өдөр</span>
        </div>
        <p className="text-sm text-slate-300">Зогсоол: {sites.find(s => s.id === row.site_id)?.name || 'Оноогоогүй'} · Түлхүүр: {row.key_set ? 'үүссэн (v' + row.key_version + ')' : 'үүсгээгүй'}</p>
        <p className="text-xs font-mono text-slate-300 break-all">X-Hospital-ID: {row.id}</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={button} disabled={busy} onClick={() => { setForm({ ...row }); setError(''); setNotice('') }}>Тохируулах</button>
          <button type="button" className={button + ' flex items-center gap-2'} disabled={busy || !row.site_id || !data.encryption_ready || Boolean(secret)}
            onClick={() => rotate(row)}><KeyRound size={15} aria-hidden="true" /> {row.key_set ? 'Түлхүүр солих' : 'Түлхүүр үүсгэх'}</button>
          <button type="button" className={danger + ' flex items-center gap-2'} disabled={busy}
            onClick={() => remove(row)}><Trash2 size={15} aria-hidden="true" /> Устгах</button>
        </div>
      </article>)}
    </div>
    <div className="card space-y-2 text-sm text-slate-300">
      <h4 className="font-semibold text-slate-100">Эмнэлгийн хөгжүүлэгчид өгөх мэдээлэл</h4>
      <p className="font-mono break-all">POST {window.location.origin}/api/v1/hospital/visits</p>
      <p>X-Hospital-ID, X-Hospital-Timestamp, X-Hospital-Signature толгойтой HMAC-SHA256 хүсэлт илгээнэ. HTTPS заавал. visit_id давтагдвал лимит нэмэгдэхгүй.</p>
      <p>Илгээх мэдээлэл: visit_id, site_code, plate_number (1234УБА), served_at (цагийн бүстэй). Өвчтөний нэр, регистр, онош илгээхгүй.</p>
      <p>Техникийн заавар болон Python гарын үсгийн жишээг системийн админаас авна.</p>
    </div>
  </section>
}
