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
    // site_id === '*' — нэг дансыг БҮХ зогсоолд хадгална
    const targets = f.site_id === '*' ? (f._all || []).map((s) => s.id) : [f.site_id]
    if (!targets.length) return toast('Зогсоол олдсонгүй', 'error')
    const body = { bank_name: f.bank_name || '', bank_account: f.bank_account || '',
                   bank_account_name: f.bank_account_name || '' }
    try {
      for (const id of targets) {
        await api(`/api/admin/sites/${id}`, { method: 'PUT', body })
      }
      toast(targets.length > 1 ? `${targets.length} зогсоолд хадгалагдлаа` : 'Хадгалагдлаа')
      onClose(); onDone()
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
                // Данс байхгүй зогсоол үлдээгүй ч «Бүгд» сонголтоор бүгдийг
                // нэг дор дарж бичих боломж хэрэгтэй тул модалыг ямагт нээнэ
                setBankModal({ _pick: free, _all: sites })
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
          <h3 className="font-semibold text-sm mb-1">e-Barimt — сувгууд</h3>
          {/* Суваг бүрийн бодит байдал: QR → QPay; бэлэн/карт/дансаар → msgbill (түлхүүртэй
              зогсоол) эсвэл PosAPI; аль нь ч байхгүй бол баримт FAILED бүртгэгдэнэ */}
          <div>
            <span className="text-slate-400">QPay QR төлбөр:</span>{' '}
            {data.ebarimt.qpay_ebarimt
              ? <span className={data.ebarimt.qpay_mock ? 'text-amber-400' : 'text-accent'}>
                  QPay e-Barimt 3.0{data.ebarimt.qpay_mock ? ' (QPay MOCK — бодит биш)' : ' — бодит'}</span>
              : <span className="text-amber-400">унтраалттай</span>}
          </div>
          <div>
            <span className="text-slate-400">Бэлэн / карт / дансаар:</span>{' '}
            {(data.msgbill?.configured || (data.msgbill?.tenants || []).some((t) => t.key_set))
              ? <span className="text-accent">msgbill.mn — бодит (түлхүүртэй зогсоолууд, доор)</span>
              : null}
            {data.ebarimt.local_channel === 'posapi' && (
              <span className="text-accent ml-1">· PosAPI бодит (<span className="font-mono">{data.ebarimt.posapi_url}</span>, ТТД {data.ebarimt.merchant_tin || '—'})</span>)}
            {data.ebarimt.local_channel === 'mock' && (
              <span className="text-amber-400 ml-1">· msgbill түлхүүргүй зогсоолд PosAPI MOCK (хуурамч баримт!)</span>)}
            {data.ebarimt.local_channel === 'none' && (
              <span className="text-slate-400 ml-1">· msgbill түлхүүргүй зогсоолд суваг байхгүй → баримт «Амжилтгүй» бүртгэгдэж, түлхүүр тавьсны дараа «Дахин үүсгэх»-ээр нөхөгдөнө</span>)}
          </div>
          <div className="text-slate-500">Локал PosAPI (.env): {data.ebarimt.mock ? 'суугаагүй/MOCK' : 'бодит'}
            {data.ebarimt.mock && !data.ebarimt.mock_receipts && ' — хуурамч баримт үүсгэхгүй (EBARIMT_MOCK_RECEIPTS=false)'}</div>
        </div>
      </div>

      {/* msgbill.mn eBarimt API — дансаар (online operator) төлбөрт PosAPI-гүйгээр
          жинхэнэ баримт. Түлхүүр: түрээслэгч бүрийнх (энд) → глобал .env */}
      {data.msgbill && (
        <MsgbillPanel mb={data.msgbill} isSuper={isSuper} onChanged={load} />
      )}

      {/* Bank modal-д зогсоол сонгуулах хувилбар (шинээр нэмэхэд) */}
      {bankModal?._pick && (
        <Modal open title="Аль зогсоолд данс нэмэх вэ?" onClose={() => setBankModal(null)}>
          <div className="space-y-2">
            {/* Нэг данс бүх зогсоолд — ихэнх тохиолдолд байгууллага НЭГ дансаар
                хураадаг тул зогсоол бүрээр давтахгүйн тулд */}
            <button className="btn-primary w-full justify-start"
              onClick={() => setBankModal({ site_id: '*', name: 'Бүх зогсоол',
                                            _all: bankModal._all })}>
              Бүх зогсоол
              <span className="text-xs opacity-70 ml-1">({bankModal._all.length}) — нэг данс бүгдэд</span>
            </button>
            {bankModal._pick.length > 0 && (
              <div className="text-xs text-slate-500 pt-1">эсвэл тухайн нэг зогсоолд:</div>
            )}
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

// ───────────────────────── msgbill.mn e-Barimt API ─────────────────────────
// Үйлчилгээ 3 (eBarimt API): ДАНСААР (online operator) төлбөрт баримтыг msgbill.mn
// үүсгэж өгнө — сервер бүр дээр ТЕГ PosAPI суулгах шаардлагагүй. Түлхүүрийн
// шатлал backend services/msgbill.api_key_for-той ижил: түрээслэгч → глобал.
function MsgbillKeyModal({ state, onClose, onDone }) {
  // state: {id, name, code, key_set}
  const toast = useToast()
  const [key, setKey] = useState('')
  const [whsec, setWhsec] = useState('')
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState(null)
  useEffect(() => { setKey(''); setWhsec(''); setResult(null) }, [state])
  if (!state) return null

  const save = async (e) => {
    e.preventDefault()
    if (!key.trim() && !whsec.trim()) return toast('bsk_… түлхүүр эсвэл whsec_ нууцаа бичнэ үү', 'error')
    try {
      const body = {}
      if (key.trim()) body.msgbill_api_key = key.trim()
      if (whsec.trim()) body.msgbill_webhook_secret = whsec.trim()
      await api(`/api/admin/tenants/${state.id}`, { method: 'PUT', body })
      toast('msgbill түлхүүр хадгалагдлаа'); onClose(); onDone()
    } catch (err) { toast(err.message, 'error') }
  }
  const unlink = async () => {
    if (!confirm(`${state.name} — msgbill түлхүүрийг салгах уу? Дансаар төлбөрийн баримт `
      + 'глобал түлхүүр (байвал) эсвэл локал PosAPI руу буцна.')) return
    try {
      await api(`/api/admin/tenants/${state.id}`, { method: 'PUT', body: { msgbill_api_key: '', msgbill_webhook_secret: '' } })
      toast('Салгагдлаа'); onClose(); onDone()
    } catch (err) { toast(err.message, 'error') }
  }
  // Турших: бичсэн түлхүүрээр (хадгалахаас өмнө) эсвэл хадгалсан түлхүүрээр
  const test = async () => {
    setTesting(true); setResult(null)
    try {
      const body = key.trim() ? { api_key: key.trim() } : { tenant_id: state.id }
      setResult(await api('/api/admin/msgbill/test', { method: 'POST', body }))
    } catch (err) { setResult({ ok: false, error: err.message }) } finally { setTesting(false) }
  }

  return (
    <Modal open title={`${state.name} — msgbill.mn түлхүүр`} onClose={onClose}>
      <form onSubmit={save} className="space-y-3">
        <Field label={state.key_set ? 'API түлхүүр (тохируулсан — солих бол бичнэ)' : 'API түлхүүр (bsk_…)'}>
          <PasswordInput className="input font-mono text-xs" value={key}
            placeholder={state.key_set ? '••••••••••••' : 'bsk_live_…'} onChange={(e) => setKey(e.target.value)} />
        </Field>
        <Field label={state.webhook_secret_set ? 'Webhook нууц (whsec_… — тохируулсан)' : 'Webhook нууц (whsec_…, заавал биш)'}>
          <PasswordInput className="input font-mono text-xs" value={whsec}
            placeholder={state.webhook_secret_set ? '••••••••••••' : 'whsec_…'} onChange={(e) => setWhsec(e.target.value)} />
          <div className="text-[11px] text-slate-500 mt-1">
            Webhook URL: <span className="font-mono select-all">{window.location.origin}/api/payments/msgbill/webhook</span>
          </div>
        </Field>
        <p className="text-[11px] text-slate-500">
          msgbill.mn → Dashboard → Developers хуудаснаас <b>receipt</b> эрхтэй түлхүүр үүсгэнэ.
          <span className="font-mono">bsk_test_</span> түлхүүр серверт юу ч бичихгүй симуляц буцаана;
          live түлхүүрээр «Турших» дарвал 10₮-ийн ЖИНХЭНЭ баримт үүсэж сарын тоонд орно.
        </p>
        {result && (
          <div className={`text-xs rounded-lg px-3 py-2 ${result.ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
            {result.ok
              ? <>✓ Баримт үүслээ{result.test ? ' (СИМУЛЯЦ — тест түлхүүр)' : ''} · ДДТД <span className="font-mono">{result.receipt_no}</span>
                  {result.lottery && <> · сугалаа <span className="font-mono">{result.lottery}</span></>}</>
              : <>✗ {result.error || `Төлөв ${result.state || '?'}`}
                  {result.msgbill_id && <div className="font-mono text-[10px] opacity-70 mt-0.5">id {result.msgbill_id}</div>}</>}
          </div>
        )}
        <div className="flex gap-2">
          {state.key_set && (
            <button type="button" className="btn-secondary text-red-400" onClick={unlink}>Салгах</button>
          )}
          <button type="button" className="btn-secondary" disabled={testing || (!key.trim() && !state.key_set)}
            onClick={test}>{testing ? 'Турших…' : 'Турших'}</button>
          <button className="btn-primary flex-1 justify-center" disabled={!key.trim() && !whsec.trim()}>Хадгалах</button>
        </div>
      </form>
    </Modal>
  )
}

// Глобал түлхүүр/хамрах арга — DB-д (app_settings), .env-г дарна. Прод серверт
// SSH-гүй тул .env засахын оронд эндээс тохируулна.
const METHOD_OPTS = [['TRANSFER', 'Дансаар (online operator)'], ['CASH', 'Бэлэн'], ['CARD', 'Карт (POS)']]
function MsgbillGlobalModal({ open, mb, onClose, onDone }) {
  const toast = useToast()
  const [key, setKey] = useState('')
  const [whsec, setWhsec] = useState('')
  const [methods, setMethods] = useState([])
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState(null)
  useEffect(() => {
    if (open) { setKey(''); setWhsec(''); setResult(null); setMethods(mb.methods || []) }
  }, [open])
  const webhookUrl = `${window.location.origin}${mb.webhook_path || '/api/payments/msgbill/webhook'}`
  if (!open) return null
  const toggle = (m) => setMethods(methods.includes(m) ? methods.filter((x) => x !== m) : [...methods, m])
  const save = async (e) => {
    e.preventDefault()
    try {
      const body = { methods: methods.join(',') }
      if (key.trim()) body.api_key = key.trim()
      if (whsec.trim()) body.webhook_secret = whsec.trim()
      await api('/api/admin/msgbill/global', { method: 'PUT', body })
      toast('msgbill глобал тохиргоо хадгалагдлаа'); onClose(); onDone()
    } catch (err) { toast(err.message, 'error') }
  }
  const clearKey = async () => {
    if (!confirm('UI-аас тавьсан глобал түлхүүрийг устгах уу? (.env-ийн түлхүүр байвал түүн рүү буцна)')) return
    try {
      await api('/api/admin/msgbill/global', { method: 'PUT', body: { api_key: '' } })
      toast('Устгагдлаа'); onClose(); onDone()
    } catch (err) { toast(err.message, 'error') }
  }
  const test = async () => {
    setTesting(true); setResult(null)
    try {
      setResult(await api('/api/admin/msgbill/test', { method: 'POST', body: key.trim() ? { api_key: key.trim() } : {} }))
    } catch (err) { setResult({ ok: false, error: err.message }) } finally { setTesting(false) }
  }
  return (
    <Modal open title="msgbill.mn — глобал тохиргоо" onClose={onClose}>
      <form onSubmit={save} className="space-y-3">
        <Field label={mb.configured ? `API түлхүүр (тохируулсан: ${mb.source === 'db' ? 'UI' : '.env'} ${mb.key_hint || ''} — солих бол бичнэ)` : 'API түлхүүр (bsk_…)'}>
          <PasswordInput className="input font-mono text-xs" value={key}
            placeholder={mb.configured ? '••••••••••••' : 'bsk_live_…'} onChange={(e) => setKey(e.target.value)} />
        </Field>
        <Field label={mb.webhook_secret_set ? 'Webhook нууц (whsec_… — тохируулсан, солих бол бичнэ)' : 'Webhook нууц (whsec_…)'}>
          <PasswordInput className="input font-mono text-xs" value={whsec}
            placeholder={mb.webhook_secret_set ? '••••••••••••' : 'whsec_…'} onChange={(e) => setWhsec(e.target.value)} />
          <div className="text-[11px] text-slate-500 mt-1">
            msgbill → Developers → «Webhook endpoint нэмэх» дээр URL:{' '}
            <span className="font-mono text-slate-300 select-all">{webhookUrl}</span>{' '}
            <button type="button" className="text-accent" onClick={() => navigator.clipboard.writeText(webhookUrl)}>хуулах</button>
            {' '}· events: receipt.created, receipt.cancelled. Нэмэхэд нэг удаа харуулах whsec_ нууцыг энд тавина.
          </div>
        </Field>
        <div>
          <div className="label mb-1.5">Аль төлбөрийн аргад msgbill-ээр баримт үүсгэх</div>
          <div className="flex flex-wrap gap-2">
            {METHOD_OPTS.map(([v, l]) => (
              <label key={v} className={`px-3 py-1.5 rounded-lg border text-sm cursor-pointer transition-colors
                  ${methods.includes(v) ? 'border-accent bg-accent/10 text-accent' : 'border-surface-border text-slate-300 hover:border-accent/40'}`}>
                <input type="checkbox" className="hidden" checked={methods.includes(v)} onChange={() => toggle(v)} />{l}
              </label>
            ))}
          </div>
          <p className="text-[11px] text-slate-500 mt-1">
            Сонгоогүй арга (ж: бэлэн/карт) локал PosAPI-аар хэвээр. QPay QR төлбөр үргэлж QPay-ийн e-Barimt-аар.
          </p>
        </div>
        {result && (
          <div className={`text-xs rounded-lg px-3 py-2 ${result.ok ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-300'}`}>
            {result.ok
              ? <>✓ Баримт үүслээ{result.test ? ' (СИМУЛЯЦ)' : ''} · ДДТД <span className="font-mono">{result.receipt_no}</span></>
              : <>✗ {result.error || `Төлөв ${result.state || '?'}`}</>}
          </div>
        )}
        <div className="flex gap-2">
          {mb.source === 'db' && (
            <button type="button" className="btn-secondary text-red-400" onClick={clearKey}>Түлхүүр устгах</button>
          )}
          <button type="button" className="btn-secondary" disabled={testing || (!key.trim() && !mb.configured)} onClick={test}>
            {testing ? 'Турших…' : 'Турших (10₮)'}</button>
          <button className="btn-primary flex-1 justify-center">Хадгалах</button>
        </div>
        <p className="text-[11px] text-slate-500">
          Глобал тохиргоо .env-г дарна (прод серверт SSH-гүй тул эндээс удирдана).
        </p>
      </form>
    </Modal>
  )
}

function MsgbillPanel({ mb, isSuper, onChanged }) {
  const toast = useToast()
  const [modal, setModal] = useState(null)
  const [gmodal, setGmodal] = useState(false)
  const [testing, setTesting] = useState(false)
  const testGlobal = async () => {
    if (!confirm('Глобал түлхүүрээр 10₮-ийн туршилтын баримт үүсгэх үү? (live түлхүүр бол ЖИНХЭНЭ баримт)')) return
    setTesting(true)
    try {
      const r = await api('/api/admin/msgbill/test', { method: 'POST', body: {} })
      toast(r.ok ? `✓ msgbill баримт үүслээ${r.test ? ' (симуляц)' : ''} — ДДТД ${r.receipt_no}`
                 : `✗ ${r.error || r.state}`, r.ok ? undefined : 'error')
    } catch (e) { toast(e.message, 'error') } finally { setTesting(false) }
  }
  const methods = (mb.methods || []).map((m) => ({ TRANSFER: 'Дансаар', CASH: 'Бэлэн', CARD: 'Карт', QR: 'QR' }[m] || m))
  return (
    <div className="card space-y-2 text-xs">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h3 className="font-semibold text-sm">e-Barimt API — msgbill.mn</h3>
        <span className="text-slate-500">
          Хамрах төлбөр: {methods.length ? methods.join(', ') : <span className="text-amber-400">байхгүй — «Засах»-аас сонгоно</span>}
        </span>
      </div>
      <p className="text-slate-500">
        Дансаар (online operator) төлбөрийн НӨАТ баримтыг msgbill.mn Partner API-аар үүсгэнэ —
        локал PosAPI шаардлагагүй. Түлхүүрийн шатлал: түрээслэгчийн өөрийн → глобал (.env).
        Өөрийн QPay данстай түрээслэгч глобал түлхүүр рүү УНАХГҮЙ (өөр ТТД-ээр баримт гарахаас сэргийлнэ).
      </p>
      <div className="flex items-center justify-between border-b border-surface-border/40 pb-1.5">
        <div>
          <span className="text-slate-400 mr-2">Глобал (EasyParking):</span>
          {mb.configured
            ? <><span className="text-accent">тохируулсан</span> <span className="font-mono text-slate-400">{mb.key_hint}</span>
                <span className="text-slate-500 ml-1">({mb.source === 'db' ? 'UI-аас' : '.env'})</span>
                {mb.test_key && <span className="text-amber-400 ml-1">· ТЕСТ түлхүүр (симуляц)</span>}</>
            : <span className="text-amber-400">тохируулаагүй</span>}
          {mb.orphan_sites?.length > 0 && (
            <span className="text-slate-500 ml-2">· түрээслэгчгүй {mb.orphan_sites.length} зогсоол энэ түлхүүрийг ашиглана</span>
          )}
        </div>
        {isSuper && (
          <div className="flex gap-1.5">
            {mb.configured && (
              <button className="btn-secondary py-0.5 text-xs" disabled={testing} onClick={testGlobal}>Турших</button>
            )}
            <button className="btn-secondary py-0.5 text-xs" onClick={() => setGmodal(true)}>
              {mb.configured ? 'Засах' : 'Түлхүүр тавих'}</button>
          </div>
        )}
      </div>
      {(mb.tenants || []).map((t) => (
        <div key={t.id} className="flex items-center justify-between border-b border-surface-border/40 pb-1.5 gap-2">
          <div className="min-w-0">
            <span className="font-mono text-slate-400 mr-2">{t.code}</span>{t.name}
            <span className="ml-2">
              {t.key_set
                ? <span className="text-accent">өөрийн түлхүүр</span>
                : t.effective === 'global'
                  ? <span className="text-slate-400">глобал түлхүүр</span>
                  : <span className="text-amber-400">түлхүүргүй — локал PosAPI{mb.configured ? ' (өөрийн QPay данстай тул глобал руу унахгүй)' : ''}</span>}
            </span>
            {t.sites?.length > 0 && <SiteList sites={t.sites} />}
          </div>
          {isSuper && (
            <button className="btn-secondary py-0.5 text-xs shrink-0" onClick={() => setModal(t)}>
              {t.key_set ? 'Засах' : 'Түлхүүр'}
            </button>
          )}
        </div>
      ))}
      <MsgbillKeyModal state={modal} onClose={() => setModal(null)} onDone={onChanged} />
      <MsgbillGlobalModal open={gmodal} mb={mb} onClose={() => setGmodal(false)} onDone={onChanged} />
    </div>
  )
}

// ───────────────────────── Гадаад API ─────────────────────────

const CURL_EXAMPLES = (base) => [
  ['Машины зогсолт, төлөх дүнг лавлах — бүх зогсоолоос хайна',
    `# Жолооч аль зогсоолд байгааг wallet мэдэх шаардлагагүй: дугаараар БҮХ
# зогсоолоос хайж, олдсон зогсолт бүр аль зогсоолд (site_code, site_name),
# хэдэн төгрөг төлөхийг (amount_due) хариултдаа агуулна.
curl -H "X-API-Key: ТҮЛХҮҮР" \\\n  "${base}/api/v1/sessions?plate=1234УБА"

# Тодорхой НЭГ зогсоолоор хязгаарлах бол site_code нэмнэ:
curl -H "X-API-Key: ТҮЛХҮҮР" \\\n  "${base}/api/v1/sessions?plate=1234УБА&site_code=SITE10"`],
  ['Зогсоолуудын жагсаалт, сул орон тоо',
    `curl -H "X-API-Key: ТҮЛХҮҮР" "${base}/api/v1/sites"`],
  ['Төлбөрийн хүсэлт (intent) үүсгэх',
    `curl -X POST -H "X-API-Key: ТҮЛХҮҮР" -H "Content-Type: application/json" \\\n  -d '{"session_id": "SESSION_ID", "amount": 3000}' \\\n  ${base}/api/v1/payments`],
  ['Төлөгдсөнийг батлах → хаалт нээгдэж, e-Barimt үүснэ',
    `curl -X POST -H "X-API-Key: ТҮЛХҮҮР" -H "Content-Type: application/json" \\\n  -d '{"transaction_id": "ТАНЫ_ГҮЙЛГЭЭНИЙ_ДУГААР"}' \\\n  ${base}/api/v1/payments/PAYMENT_ID/confirm`],
  ['Төлбөрийн төлөв шалгах',
    `curl -H "X-API-Key: ТҮЛХҮҮР" "${base}/api/v1/payments/PAYMENT_ID"`],
]

// Түлхүүр үүсгэх модал — үүссэн түлхүүр НЭГ УДАА л ил гарна
function KeyCreateModal({ open, sites, onClose, onDone }) {
  const toast = useToast()
  const [f, setF] = useState({ name: '', can_pay: true, site_id: '' })
  const [created, setCreated] = useState(null)
  useEffect(() => { if (open) { setF({ name: '', can_pay: true, site_id: '' }); setCreated(null) } }, [open])
  if (!open) return null
  const copy = (text) => navigator.clipboard.writeText(text)
    .then(() => toast('Хуулагдлаа')).catch(() => toast('Хуулж чадсангүй', 'error'))

  const save = async (e) => {
    e.preventDefault()
    try {
      const r = await api('/api/admin/partner-keys', { method: 'POST', body: f })
      setCreated(r); onDone()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <Modal open title={created ? 'Түлхүүр үүслээ' : 'API түлхүүр үүсгэх'} onClose={onClose}>
      {!created ? (
        <form onSubmit={save} className="space-y-3">
          <Field label="Партнерын нэр (тайланд provider болно)" required>
            <input className="input font-mono" value={f.name} required
              placeholder="toki, monnis-app …"
              onChange={(e) => setF({ ...f, name: e.target.value })} />
          </Field>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={f.can_pay}
              onChange={(e) => setF({ ...f, can_pay: e.target.checked })} />
            Төлбөр батлах эрхтэй (унтраавал зөвхөн лавлана)
          </label>
          <Field label="Зогсоолын хязгаар">
            <select className="input" value={f.site_id}
              onChange={(e) => setF({ ...f, site_id: e.target.value })}>
              <option value="">Бүх зогсоол</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.site_code})</option>)}
            </select>
          </Field>
          <button className="btn-primary w-full justify-center">Үүсгэх</button>
        </form>
      ) : (
        <div className="space-y-3">
          <div className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-xs text-amber-300">
            <b>Энэ түлхүүр ДАХИН харагдахгүй</b> — одоо хуулаад партнерт аюулгүй сувгаар
            (биечлэн / нууцлагдсан чат) дамжуулна уу.
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs font-mono bg-surface-muted/60 rounded-lg px-3 py-2.5 break-all">{created.key}</code>
            <button className="btn-primary py-2 text-xs" onClick={() => copy(created.key)}>Хуулах</button>
          </div>
          <button className="btn-secondary w-full justify-center" onClick={onClose}>Хаах</button>
        </div>
      )}
    </Modal>
  )
}

function PartnerApiPanel() {
  const toast = useToast()
  const { user } = useAuth()
  const isSuper = user?.role === 'SUPER_ADMIN'
  const [data, setData] = useState(null)      // супер: {keys, env_partners}
  const [partners, setPartners] = useState(null)  // энгийн админ: зөвхөн нэрс
  const [sites, setSites] = useState([])
  const [createOpen, setCreateOpen] = useState(false)
  const load = () => {
    if (isSuper) {
      api('/api/admin/partner-keys').then(setData).catch((e) => toast(e.message, 'error'))
      api('/api/admin/sites').then(setSites)
    } else {
      api('/api/admin/payment-accounts')
        .then((d) => setPartners(d.partners)).catch(() => setPartners([]))
    }
  }
  useEffect(load, [])
  const base = window.location.origin
  const copy = (text) => navigator.clipboard.writeText(text)
    .then(() => toast('Хуулагдлаа')).catch(() => toast('Хуулж чадсангүй', 'error'))

  const revoke = async (k) => {
    if (!confirm(`«${k.name}» түлхүүрийг хаах уу? Тэр дороо хүчингүй болно, буцаахгүй.`)) return
    try {
      await api(`/api/admin/partner-keys/${k.id}/revoke`, { method: 'POST' })
      toast('Хаагдлаа'); load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <div className="space-y-4">
      {isSuper ? (
        <div className="space-y-2">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <h3 className="font-semibold text-sm flex items-center gap-1.5"><Plug size={15} /> API түлхүүрүүд</h3>
            <button className="btn-primary py-1.5 text-xs" onClick={() => setCreateOpen(true)}>
              <Plus size={14} /> Түлхүүр үүсгэх
            </button>
          </div>
          <Table headers={['Партнер', 'Түлхүүр', 'Эрх', 'Зогсоолын хязгаар', 'Сүүлд ашигласан', 'Төлөв', '']}
            empty={!data || (data.keys.length === 0 && data.env_partners.length === 0)}>
            {(data?.keys || []).map((k) => (
              <tr key={k.id} className={k.is_active ? '' : 'opacity-50'}>
                <td className="td font-mono text-sm">{k.name}</td>
                <td className="td font-mono text-xs text-slate-500">{k.key_prefix}…</td>
                <td className="td text-xs">{k.scopes.includes('pay') ? 'Лавлах + Төлбөр' : 'Зөвхөн лавлах'}</td>
                <td className="td text-xs">{k.site_code
                  ? <span>{k.site_name} <span className="font-mono text-slate-500">({k.site_code})</span></span>
                  : 'Бүх зогсоол'}</td>
                <td className="td text-xs">{k.last_used_at
                  ? new Date(k.last_used_at + 'Z').toLocaleString()
                  : <span className="text-slate-500">ашиглаагүй</span>}</td>
                <td className="td">{k.is_active
                  ? <span className="text-accent text-xs">Идэвхтэй</span>
                  : <span className="text-red-400 text-xs">Хаагдсан</span>}</td>
                <td className="td text-right">
                  {k.is_active && (
                    <button className="btn-secondary py-1 text-xs text-red-400 hover:text-red-300"
                      onClick={() => revoke(k)}>Хаах</button>
                  )}
                </td>
              </tr>
            ))}
            {(data?.env_partners || []).map((p) => (
              <tr key={'env-' + p}>
                <td className="td font-mono text-sm">{p}</td>
                <td className="td font-mono text-xs text-slate-500">—</td>
                <td className="td text-xs">Лавлах + Төлбөр</td>
                <td className="td text-xs">Бүх зогсоол</td>
                <td className="td text-xs text-slate-500">—</td>
                <td className="td"><span className="text-accent text-xs">Идэвхтэй</span>
                  <span className="ml-1.5 text-[10px] text-slate-500 cursor-help"
                    title="Серверийн тохиргоонд бүртгэлтэй хуучин түлхүүр — UI-гаас хаах боломжгүй. Оронд нь энд шинэ түлхүүр үүсгэж өгөөд хуучныг серверээс хасуулна.">(сервер)</span></td>
                <td className="td"></td>
              </tr>
            ))}
          </Table>
          <p className="text-[11px] text-slate-500">
            Түлхүүр үүсгэх мөчид нэг л удаа бүтнээрээ харагдана; хаасан түлхүүр тэр дороо
            хүчингүй болно. «Зөвхөн лавлах» түлхүүрээр төлбөр батлах боломжгүй.
          </p>
        </div>
      ) : (
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
            Түлхүүрийн удирдлага зөвхөн Супер админд байдаг.
          </p>
        </div>
      )}

      <KeyCreateModal open={createOpen} sites={sites}
        onClose={() => setCreateOpen(false)} onDone={load} />

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
        </p>
        <div className="text-xs text-slate-400 rounded-lg border border-surface-border px-3 py-2 space-y-1">
          <div className="text-slate-300 font-medium">Бүтэн урсгал:</div>
          <div>1. Дугаараар зогсолт лавлана → төлөх дүн (amount_due) ирнэ</div>
          <div>2. Intent үүсгээд wallet өөрийн хэрэглэгчээс дүнг нэхэмжилнэ</div>
          <div>3. Төлөгдмөгц confirm дуудна → зогсолт ТӨЛӨГДСӨН болж, e-Barimt автоматаар үүснэ</div>
          <div>4. Жолооч тарифын «үнэгүй гарах хугацаа»-нд (default 15 мин) багтаж гарахад
            хаалт дугаараар нь автоматаар нээгдэнэ; машин гарах хаалтан дээр аль хэдийн
            зогсож байсан бол confirm-ын дараа шууд нээгдэнэ</div>
          <div>Хугацаа хэтэрвэл нэмэлт төлбөр бодогдож, дахин төлүүлнэ.</div>
        </div>
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
          Дэлгэрэнгүй техникийн баримтыг (алдааны кодууд, PAX терминал бүртгэл)
          системийн админаас авна.
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
