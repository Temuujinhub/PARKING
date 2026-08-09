// Холболт (Integrations) — гадаад холболтуудын НЭГДСЭН хэсэг: төлбөрийн данс
// (QPay/банк/e-Barimt), гадаад API (партнер түлхүүр + баримтжуулалт), EV цэнэглэгч.
// Өмнө нь QPay данс 3 газар (зогсоолын модал, түрээслэгчийн модал, .env) тарсан
// байсныг энд нэгтгэв — данс бүрийн ард «яг аль зогсоолууд энэ данс руу төлж
// байгаа» нь жагсаалтаар шууд харагдана. SUPER_ADMIN бүгдийг, ADMIN өөрийн хамрах
// хүрээний зогсоол/дансыг харна (backend шүүнэ); түрээслэгчийн данс засах нь
// зөвхөн SUPER_ADMIN.
import { CreditCard, KeyRound, Plug, Plus, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { useAuth } from '../../auth'
import { Field, Modal, PasswordInput, Table, useToast } from '../../components/ui'
import QpayTestModal from './QpayTestModal'

const SUBTABS = [
  ['pay', 'Төлбөрийн данс', CreditCard],
  ['api', 'Гадаад API', KeyRound],
  ['ev', 'Цэнэглэгч', Zap],
]

export default function IntegrationsSection() {
  const [sub, setSub] = useState('pay')
  return (
    <div className="space-y-4">
      <div className="flex gap-2 flex-wrap">
        {SUBTABS.map(([v, l, Icon]) => (
          <button key={v} onClick={() => setSub(v)}
            className={`flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg border text-sm transition-colors cursor-pointer
              ${sub === v ? 'border-accent bg-accent/10 text-accent font-medium'
                          : 'border-surface-border text-slate-400 hover:text-slate-200 hover:border-slate-600'}`}>
            <Icon size={15} /> {l}
          </button>
        ))}
      </div>
      {sub === 'pay' && <PaymentAccountsPanel />}
      {sub === 'api' && <PartnerApiPanel />}
      {sub === 'ev' && <ChargersPanel />}
    </div>
  )
}

// ───────────────────────── Төлбөрийн данс ─────────────────────────

const ScopePill = ({ scope }) => {
  const map = {
    global: ['Үндсэн (.env)', 'text-sky-400 bg-sky-500/10'],
    tenant: ['Түрээслэгч', 'text-amber-400 bg-amber-500/10'],
    site: ['Зогсоол', 'text-rose-400 bg-rose-500/10'],
  }
  const [label, cls] = map[scope] || [scope, 'text-slate-400 bg-slate-500/10']
  return <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${cls}`}>{label}</span>
}

// Данс ашиглаж буй зогсоолууд — жагсаалт хэлбэрээр (код + нэр, идэвхгүй нь зурсан)
const SiteList = ({ sites }) => sites?.length
  ? (
    <ul className="text-xs space-y-0.5">
      {sites.map((s) => (
        <li key={s.id} className={s.is_active === false ? 'text-slate-500 line-through' : 'text-slate-300'}>
          <span className="font-mono text-slate-500 mr-1.5">{s.site_code}</span>
          {s.name}
        </li>
      ))}
    </ul>
  )
  : <span className="text-xs text-slate-500">— аль ч зогсоол ашиглахгүй</span>

function AccountModal({ state, tenants, sites, isSuper, onClose, onDone }) {
  // state: {mode:'new'} | {mode:'edit', scope, id, ...талбарууд}
  const toast = useToast()
  const [f, setF] = useState({})
  useEffect(() => {
    if (!state) return
    if (state.mode === 'new') {
      // ADMIN түрээслэгчийн данс үүсгэхгүй (backend ч 403 өгнө) — зогсоолын л данс
      setF({ scope: isSuper ? 'tenant' : 'site', target_id: '', qpay_username: '',
             qpay_password: '', qpay_invoice_code: '', qpay_district_code: '' })
    } else {
      setF({ scope: state.scope, target_id: state.id,
             qpay_username: state.merchant || '', qpay_password: '',
             qpay_invoice_code: state.invoice_code || '',
             qpay_district_code: state.district_code || '',
             password_set: state.qpay_password_set })
    }
  }, [state])
  if (!state) return null
  const isNew = state.mode === 'new'
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })

  const save = async (e) => {
    e.preventDefault()
    if (!f.target_id) return toast('Хамрах хүрээгээ сонгоно уу', 'error')
    if (!f.qpay_username.trim()) return toast('QPay нэвтрэх нэрээ бичнэ үү', 'error')
    if (!f.qpay_password.trim() && !f.password_set) {
      return toast('QPay нууц үгээ бичнэ үү — нэр ганцаараа хангалтгүй', 'error')
    }
    try {
      const body = {
        qpay_username: f.qpay_username.trim(),
        qpay_invoice_code: f.qpay_invoice_code.trim(),
        qpay_district_code: f.qpay_district_code.trim(),
      }
      if (f.qpay_password.trim()) body.qpay_password = f.qpay_password.trim()
      const url = f.scope === 'tenant'
        ? `/api/admin/tenants/${f.target_id}` : `/api/admin/sites/${f.target_id}`
      await api(url, { method: 'PUT', body })
      toast('Хадгалагдлаа — одоо «Турших»-аар данс руу бодит төлбөр орж буйг шалгана уу')
      onClose(); onDone()
    } catch (err) { toast(err.message, 'error') }
  }

  const unlink = async () => {
    if (!confirm('Энэ дансыг салгах уу? Хамаарах зогсоолууд дараагийн шатлалын '
      + '(түрээслэгч → үндсэн) данс руу шилжинэ.')) return
    try {
      const url = f.scope === 'tenant'
        ? `/api/admin/tenants/${f.target_id}` : `/api/admin/sites/${f.target_id}`
      await api(url, { method: 'PUT', body: {
        qpay_username: '', qpay_password: '', qpay_invoice_code: '',
        qpay_branch_code: '', qpay_district_code: '' } })
      toast('Данс салгагдлаа')
      onClose(); onDone()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <Modal open title={isNew ? 'QPay данс холбох' : `${state.name} — данс засах`} onClose={onClose}>
      <form onSubmit={save} className="space-y-3">
        {isNew && (
          <>
            <div className="flex gap-2">
              {(isSuper
                ? [['tenant', 'Түрээслэгчид (бүх зогсоолд нь)'], ['site', 'Нэг зогсоолд (ховор)']]
                : [['site', 'Нэг зогсоолд']]).map(([v, l]) => (
                <label key={v} className={`flex-1 px-3 py-2 rounded-lg border text-sm cursor-pointer text-center transition-colors
                  ${f.scope === v ? 'border-accent bg-accent/10 text-accent' : 'border-surface-border text-slate-300'}`}>
                  <input type="radio" className="hidden" checked={f.scope === v}
                    onChange={() => setF({ ...f, scope: v, target_id: '' })} />
                  {l}
                </label>
              ))}
            </div>
            <Field label={f.scope === 'tenant' ? 'Түрээслэгч' : 'Зогсоол'} required>
              <select className="input" value={f.target_id} required onChange={set('target_id')}>
                <option value="">— Сонгох —</option>
                {(f.scope === 'tenant' ? tenants : sites).map((x) => (
                  <option key={x.id} value={x.id}>{x.name}{x.site_code ? ` (${x.site_code})` : ''}</option>
                ))}
              </select>
              {f.scope === 'site' && (
                <div className="text-[11px] text-slate-500 mt-1">
                  Зогсоолын тусгай данс нь түрээслэгчийн дансыг ДАРНА — зөвхөн нэг зогсоол
                  л өөр дансаар ажиллах ёстой онцгой тохиолдолд ашиглана.
                </div>
              )}
            </Field>
          </>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Нэвтрэх нэр (client_id)" required>
            <input className="input font-mono text-xs" autoComplete="off" required
              value={f.qpay_username || ''} placeholder="MONNIS_PROPERTIES"
              onChange={set('qpay_username')} />
          </Field>
          <Field label={f.password_set ? 'Нууц үг (тохируулсан — солих бол бичнэ)' : 'Нууц үг'}>
            <PasswordInput className="input" value={f.qpay_password || ''}
              placeholder={f.password_set ? '••••••••' : ''}
              onChange={set('qpay_password')} />
          </Field>
          <Field label="Нэхэмжлэхийн код (ЗААВАЛ EB_-ээр эхэлнэ)">
            <input className="input font-mono text-xs" value={f.qpay_invoice_code || ''}
              placeholder="EB_MONNIS_INVOICE" onChange={set('qpay_invoice_code')} />
          </Field>
          <Field label="НӨАТ дүүрэг+хороо (4 орон)">
            <input className="input font-mono text-xs" value={f.qpay_district_code || ''}
              placeholder="2318" onChange={set('qpay_district_code')} />
          </Field>
        </div>
        {(f.qpay_invoice_code || '').trim() && !(f.qpay_invoice_code || '').startsWith('EB_') && (
          <div className="text-xs text-red-400 bg-red-500/10 rounded-lg px-3 py-2">
            ⚠ EB_ угтваргүй код НӨАТ давхар нэмэгдэж илүү дүн авах + e-Barimt үүсэхгүй
            алдаа өгдөг. QPay-ээс EB_-ээр эхэлсэн код авсан эсэхээ шалгана уу.
          </div>
        )}
        <div className="flex gap-2">
          {!isNew && (
            <button type="button" className="btn-secondary text-red-400" onClick={unlink}>
              Данс салгах
            </button>
          )}
          <button className="btn-primary flex-1 justify-center">Хадгалах</button>
        </div>
      </form>
    </Modal>
  )
}

function BankModal({ state, onClose, onDone }) {
  // state: {site_id, name, bank_name, bank_account, bank_account_name}
  const toast = useToast()
  const [f, setF] = useState({})
  useEffect(() => { if (state) setF({ ...state }) }, [state])
  if (!state) return null
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const save = async (e) => {
    e.preventDefault()
    try {
      await api(`/api/admin/sites/${f.site_id}`, { method: 'PUT', body: {
        bank_name: f.bank_name || '', bank_account: f.bank_account || '',
        bank_account_name: f.bank_account_name || '' } })
      toast('Хадгалагдлаа'); onClose(); onDone()
    } catch (err) { toast(err.message, 'error') }
  }
  return (
    <Modal open title={`${state.name} — шилжүүлгийн данс`} onClose={onClose}>
      <form onSubmit={save} className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Банк">
            <input className="input" value={f.bank_name || ''} placeholder="Хаан банк"
              onChange={set('bank_name')} />
          </Field>
          <Field label="Дансны дугаар">
            <input className="input font-mono" value={f.bank_account || ''} placeholder="5123456789"
              onChange={set('bank_account')} />
          </Field>
          <Field label="Данс эзэмшигч">
            <input className="input" value={f.bank_account_name || ''} placeholder="Моннис Пропертис ХХК"
              onChange={set('bank_account_name')} />
          </Field>
        </div>
        <div className="text-[11px] text-slate-500">
          «Дансаар» эрхтэй (pay_transfer) оператор кассаас энэ дансыг жолоочид хэлж,
          шилжүүлэг орж ирснийг хуулгаас шалгаад төлбөрийг баталгаажуулна.
          Бүх талбарыг хоослож хадгалбал данс устана.
        </div>
        <button className="btn-primary w-full justify-center">Хадгалах</button>
      </form>
    </Modal>
  )
}

function PaymentAccountsPanel() {
  const toast = useToast()
  const { user } = useAuth()
  const isSuper = user?.role === 'SUPER_ADMIN'
  const [data, setData] = useState(null)
  const [tenants, setTenants] = useState([])
  const [sites, setSites] = useState([])
  const [accModal, setAccModal] = useState(null)
  const [bankModal, setBankModal] = useState(null)
  const [qpayTest, setQpayTest] = useState(null)
  const load = () => api('/api/admin/payment-accounts').then(setData).catch((e) => toast(e.message, 'error'))
  useEffect(() => {
    load()
    if (isSuper) api('/api/admin/tenants').then(setTenants)  // түрээслэгчийн жагсаалт супер л авна
    api('/api/admin/sites').then(setSites)
  }, [])
  if (!data) return <div className="card text-sm text-slate-500 py-6 text-center">Ачаалж байна…</div>

  // Данс турших — данс бүр эхний (идэвхтэй) зогсоолоороо туршигдана
  const test = (acc) => {
    const s = (acc.sites || []).find((x) => x.is_active !== false) || (acc.sites || [])[0]
    if (!s) return toast('Энэ дансыг ямар ч зогсоол ашиглахгүй байна — эхлээд зогсоол холбоно уу', 'error')
    setQpayTest({ site: { id: s.id, name: `${acc.name} · ${s.name}` } })
  }
  const g = data.global

  return (
    <div className="space-y-4">
      {/* ── QPay дансууд ── */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <p className="text-sm text-slate-400 max-w-2xl">
          Данс сонгох дараалал: зогсоолын тусгай данс → түрээслэгчийн данс → үндсэн данс.
          Данс бүрийн ард яг аль зогсоолууд түүн рүү төлж байгаа нь харагдана.
        </p>
        <button className="btn-primary" onClick={() => setAccModal({ mode: 'new' })}>
          <Plus size={16} /> Данс холбох
        </button>
      </div>

      <Table headers={['Хамрах хүрээ', 'Нэр', 'Merchant', 'Нэхэмжлэхийн код', 'Ашиглаж буй зогсоолууд', '']}
        empty={false}>
        {/* Үндсэн (.env) данс — UI-гаас засахгүй, харин юу ашиглаж буйг ил харуулна */}
        <tr>
          <td className="td"><ScopePill scope="global" /></td>
          <td className="td text-sm">Easy Parking (систем)
            {g.mock && <span className="ml-1.5 text-[10px] text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded">MOCK</span>}
            {g.sandbox && !g.mock && <span className="ml-1.5 text-[10px] text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded">SANDBOX</span>}
          </td>
          <td className="td font-mono text-xs">{g.merchant || <span className="text-red-400">тохируулаагүй</span>}</td>
          <td className="td font-mono text-xs">{g.invoice_code}
            {g.warning && <span className="ml-1 text-red-400 cursor-help" title={g.warning}>⚠</span>}
          </td>
          <td className="td"><SiteList sites={g.sites} /></td>
          <td className="td text-right whitespace-nowrap">
            <button className="btn-secondary py-1 text-xs"
              onClick={() => test({ name: 'Үндсэн данс', sites: g.sites })}>Турших</button>
          </td>
        </tr>
        {data.accounts.map((a) => (
          <tr key={a.scope + a.id}>
            <td className="td"><ScopePill scope={a.scope} /></td>
            <td className="td text-sm">{a.name}
              {a.site_code && <span className="text-slate-500 font-mono text-xs ml-1">({a.site_code})</span>}
              {!a.complete && (
                <span className="ml-1.5 text-[10px] text-red-400 bg-red-500/10 px-1.5 py-0.5 rounded cursor-help"
                  title="Нэр эсвэл нууц үг дутуу — данс ажиллахгүй, төлбөр дараагийн шатлалын данс руу орно">
                  ⚠ дутуу
                </span>
              )}
            </td>
            <td className="td font-mono text-xs">{a.merchant || '—'}</td>
            <td className="td font-mono text-xs">{a.invoice_code || '—'}
              {a.warning && <span className="ml-1 text-red-400 cursor-help" title={a.warning}>⚠</span>}
            </td>
            <td className="td"><SiteList sites={a.sites} /></td>
            <td className="td text-right whitespace-nowrap">
              <button className="btn-secondary py-1 text-xs mr-1" onClick={() => test(a)}>Турших</button>
              {/* Түрээслэгчийн данс засах нь мөнгөний тохиргоо — зөвхөн Супер админ */}
              {(isSuper || a.scope === 'site') && (
                <button className="btn-secondary py-1 text-xs" onClick={() => setAccModal({ mode: 'edit', ...a })}>Засах</button>
              )}
            </td>
          </tr>
        ))}
      </Table>

      {/* ── Дансны бус сувгууд ── */}
      <div className="grid lg:grid-cols-2 gap-4">
        <div className="card space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-sm">Шилжүүлэг хүлээн авах данс (Дансаар)</h3>
            <button className="btn-secondary py-1 text-xs"
              onClick={() => {
                const free = sites.filter((s) => !data.bank_accounts.some((b) => b.site_id === s.id))
                if (!free.length) return toast('Бүх зогсоолд данс тохируулсан байна')
                setBankModal({ site_id: free[0].id, name: free[0].name, _pick: free })
              }}>
              <Plus size={13} className="inline -mt-0.5" /> Нэмэх
            </button>
          </div>
          {data.bank_accounts.length === 0
            ? <div className="text-xs text-slate-500">Тохируулсан данс алга — кассын «Дансаар» товч дансгүй ажиллана</div>
            : data.bank_accounts.map((b) => (
              <div key={b.site_id} className="flex items-center justify-between text-xs border-b border-surface-border/40 pb-1.5">
                <div>
                  <span className="font-mono text-slate-400 mr-2">{b.site_code}</span>
                  {b.bank_name} <span className="font-mono">{b.bank_account}</span>
                  <span className="text-slate-500 ml-1">({b.bank_account_name})</span>
                </div>
                <button className="btn-secondary py-0.5 text-xs" onClick={() => setBankModal(b)}>Засах</button>
              </div>
            ))}
        </div>
        <div className="card space-y-1.5 text-xs">
          <h3 className="font-semibold text-sm mb-1">e-Barimt</h3>
          <div>Горим: {data.ebarimt.mock
            ? <span className="text-amber-400">MOCK — бодит баримт үүсэхгүй</span>
            : <span className="text-accent">Бодит</span>}
            {data.ebarimt.qpay_ebarimt && <span className="text-slate-400 ml-2">· QPay төлбөрт QPay-ийн e-Barimt 3.0</span>}
          </div>
          <div>ТТД: <span className="font-mono">{data.ebarimt.merchant_tin || '—'}</span></div>
          <div className="text-slate-500">Бэлэн/карт/шилжүүлгийн баримт локал PosAPI-аар
            (<span className="font-mono">{data.ebarimt.posapi_url}</span>) үүснэ.
            Серверийн .env-ээс тохируулна.</div>
        </div>
      </div>

      {/* Bank modal-д зогсоол сонгуулах хувилбар (шинээр нэмэхэд) */}
      {bankModal?._pick && (
        <Modal open title="Аль зогсоолд данс нэмэх вэ?" onClose={() => setBankModal(null)}>
          <div className="space-y-2">
            {bankModal._pick.map((s) => (
              <button key={s.id} className="btn-secondary w-full justify-start"
                onClick={() => setBankModal({ site_id: s.id, name: s.name })}>
                {s.name} <span className="font-mono text-xs text-slate-500">({s.site_code})</span>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {bankModal && !bankModal._pick && (
        <BankModal state={bankModal} onClose={() => setBankModal(null)} onDone={load} />
      )}
      <AccountModal state={accModal} tenants={tenants} sites={sites} isSuper={isSuper}
        onClose={() => setAccModal(null)} onDone={load} />
      <QpayTestModal state={qpayTest} onClose={() => setQpayTest(null)} />
    </div>
  )
}

// ───────────────────────── Гадаад API ─────────────────────────

const CURL_EXAMPLES = (base) => [
  ['Машины зогсолт, төлөх дүнг лавлах',
    `curl -H "X-API-Key: ТҮЛХҮҮР" \\\n  "${base}/api/v1/sessions?plate=1234УБА"`],
  ['Зогсоолуудын жагсаалт, сул орон тоо',
    `curl -H "X-API-Key: ТҮЛХҮҮР" "${base}/api/v1/sites"`],
  ['Төлбөрийн хүсэлт (intent) үүсгэх',
    `curl -X POST -H "X-API-Key: ТҮЛХҮҮР" -H "Content-Type: application/json" \\\n  -d '{"session_id": "SESSION_ID", "amount": 3000}' \\\n  ${base}/api/v1/payments`],
  ['Төлөгдсөнийг батлах → хаалт нээгдэж, e-Barimt үүснэ',
    `curl -X POST -H "X-API-Key: ТҮЛХҮҮР" -H "Content-Type: application/json" \\\n  -d '{"transaction_id": "ТАНЫ_ГҮЙЛГЭЭНИЙ_ДУГААР"}' \\\n  ${base}/api/v1/payments/PAYMENT_ID/confirm`],
  ['Төлбөрийн төлөв шалгах',
    `curl -H "X-API-Key: ТҮЛХҮҮР" "${base}/api/v1/payments/PAYMENT_ID"`],
]

function PartnerApiPanel() {
  const toast = useToast()
  const [partners, setPartners] = useState(null)
  useEffect(() => {
    api('/api/admin/payment-accounts')
      .then((d) => setPartners(d.partners)).catch(() => setPartners([]))
  }, [])
  const base = window.location.origin
  const copy = (text) => navigator.clipboard.writeText(text)
    .then(() => toast('Хуулагдлаа')).catch(() => toast('Хуулж чадсангүй', 'error'))

  return (
    <div className="space-y-4">
      <div className="card space-y-2">
        <h3 className="font-semibold text-sm flex items-center gap-1.5"><Plug size={15} /> Холбогдсон партнерууд</h3>
        {partners === null ? <div className="text-xs text-slate-500">Ачаалж байна…</div>
          : partners.length === 0
            ? <div className="text-xs text-slate-500">Партнер бүртгэгдээгүй байна</div>
            : (
              <div className="flex flex-wrap gap-2">
                {partners.map((p) => (
                  <span key={p} className="px-3 py-1 rounded-lg border border-accent/40 bg-accent/5 text-accent text-sm font-mono">{p}</span>
                ))}
              </div>
            )}
        <p className="text-[11px] text-slate-500">
          Түлхүүрүүд серверийн .env файлын <span className="font-mono">PARKING_PARTNER_KEYS</span>-д
          хадгалагддаг (энд харагдахгүй). Партнер нэмэх/солиход .env засаад сервер restart
          хийнэ. Түлхүүрийг UI-гаас үүсгэх/хаах (restart-гүй) удирдлага дараагийн
          шатанд DB рүү шилжүүлснээр нэмэгдэнэ.
        </p>
      </div>

      <div className="card space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-sm">API баримтжуулалт — партнерт өгөхөд бэлэн</h3>
          <button className="btn-secondary py-1 text-xs"
            onClick={() => copy(CURL_EXAMPLES(base).map(([t, c]) => `# ${t}\n${c}`).join('\n\n'))}>
            Бүгдийг хуулах
          </button>
        </div>
        <p className="text-xs text-slate-400">
          TOKI, банкны апп, хэтэвч зэрэг гуравдагч систем машины зогсолтыг лавлаж, өөрийн
          сувгаар төлбөр авч баталгаажуулна. Бүх хүсэлт <span className="font-mono">X-API-Key</span> толгойтой.
          Батлагдмагц хаалт нээгдэж, e-Barimt автоматаар үүснэ.
        </p>
        {CURL_EXAMPLES(base).map(([title, cmd]) => (
          <div key={title}>
            <div className="flex items-center justify-between mb-1">
              <div className="text-xs text-slate-300">{title}</div>
              <button className="text-[11px] text-accent hover:underline cursor-pointer" type="button"
                onClick={() => copy(cmd)}>Хуулах</button>
            </div>
            <pre className="text-[11px] bg-surface-muted/50 rounded-lg p-2.5 overflow-x-auto font-mono whitespace-pre">{cmd}</pre>
          </div>
        ))}
        <p className="text-[11px] text-slate-500">
          Дэлгэрэнгүй (алдааны кодууд, PAX терминал бүртгэл): repo-гийн
          <span className="font-mono"> docs/INTEGRATION_API.md</span>
        </p>
      </div>
    </div>
  )
}

// ───────────────────────── EV цэнэглэгч ─────────────────────────

function ChargersPanel() {
  const [chargers, setChargers] = useState(null)
  useEffect(() => {
    api('/api/admin/devices')
      .then((rows) => setChargers(rows.filter((d) => d.device_type === 'ev_charger')))
      .catch(() => setChargers([]))
  }, [])
  return (
    <div className="space-y-4">
      <div className="card space-y-2 text-sm">
        <h3 className="font-semibold flex items-center gap-1.5"><Zap size={15} className="text-accent" /> Цахилгаан машины цэнэглэгч</h3>
        <p className="text-slate-400 text-xs max-w-2xl">
          Зарчим: машин цэнэглэгчид залгагдсан хугацаанд зогсоолын төлбөрийн тоолуур
          <b className="text-slate-300"> түр зогсоно</b> — гарахад цэнэглэсэн минут нийт
          хугацаанаас хасагдаж бодогдоно (дамжин зогсоолын тоолуур зогсоох механизмтай
          ижил, өдрийн дээд хязгаартай). Цэнэглэгч (эсвэл түүний удирдлагын систем)
          залгах/салгах үедээ хоёрхон API дуудлага хийнэ.
        </p>
        <pre className="text-[11px] bg-surface-muted/50 rounded-lg p-2.5 overflow-x-auto font-mono">{
`POST /api/v1/chargers/{charger_key}/plug-in   {"plate": "1234УБА"}
POST /api/v1/chargers/{charger_key}/plug-out  {"plate": "1234УБА"}`
        }</pre>
        <p className="text-[11px] text-amber-400/90">
          Энэ API дараагийн хувилбарт нэмэгдэнэ — одоогоор цэнэглэгчээ доор бүртгэж
          бэлтгэж болно (Тохиргоо → Төхөөрөмж хэсэгт «EV цэнэглэгч» төрлөөр).
        </p>
      </div>
      <div className="card">
        <h3 className="font-semibold text-sm mb-2">Бүртгэлтэй цэнэглэгчид</h3>
        {chargers === null ? <div className="text-xs text-slate-500">Ачаалж байна…</div>
          : chargers.length === 0
            ? <div className="text-xs text-slate-500">
                Цэнэглэгч бүртгэгдээгүй — Тохиргоо → Төхөөрөмж → «Төхөөрөмж нэмэх» дээр
                төрлийг «EV цэнэглэгч» гэж сонгоод зогсоол, нэр, IP-г нь оруулна.
              </div>
            : (
              <Table headers={['Нэр', 'Зогсоол', 'IP', 'Түлхүүр']} empty={false}>
                {chargers.map((d) => (
                  <tr key={d.id}>
                    <td className="td">{d.name}</td>
                    <td className="td text-xs">{d.site_name}</td>
                    <td className="td font-mono text-xs">{d.ip_address || '—'}</td>
                    <td className="td font-mono text-[10px] text-slate-500">{d.device_key}</td>
                  </tr>
                ))}
              </Table>
            )}
      </div>
    </div>
  )
}
