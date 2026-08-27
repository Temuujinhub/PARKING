// Админ: EV цэнэглэгчдийн амьд самбар + бүртгэл + командууд (§4.4)
import { Loader2, Plug, Plus, RefreshCw, RotateCcw, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { Field, Modal, useToast } from '../../components/ui'

const fmt = (n) => Number(n || 0).toLocaleString('mn-MN')

const STATUS_COLORS = {
  Available: 'bg-slate-600/40 text-slate-300',
  Preparing: 'bg-amber-500/20 text-amber-300',
  Charging: 'bg-emerald-500/20 text-emerald-300',
  SuspendedEV: 'bg-blue-500/20 text-blue-300',
  SuspendedEVSE: 'bg-blue-500/20 text-blue-300',
  Finishing: 'bg-amber-500/20 text-amber-300',
  Faulted: 'bg-red-500/20 text-red-300',
  Unavailable: 'bg-red-500/20 text-red-300',
}

const STATUS_LABELS = {
  Available: 'Сул', Preparing: 'Залгаастай', Charging: 'Цэнэглэж байна',
  SuspendedEV: 'Машин зогсоосон', SuspendedEVSE: 'Түр зогссон',
  Finishing: 'Дуусгаж байна', Faulted: 'Гэмтэлтэй', Unavailable: 'Ажиллахгүй',
}

export default function EvBoard() {
  const toast = useToast()
  const [data, setData] = useState(null)
  const [sessions, setSessions] = useState([])
  const [sites, setSites] = useState([])
  const [reg, setReg] = useState(null)     // бүртгэх modal: {cp_id}
  const [busy, setBusy] = useState(false)

  const load = async () => {
    try {
      const d = await api('/api/admin/ev/chargers')
      setData(d)
      setSessions(await api('/api/admin/ev/sessions?limit=30'))
    } catch (e) { toast.error(e.message) }
  }
  useEffect(() => {
    load()
    api('/api/admin/sites').then((s) => setSites(s.items || s)).catch(() => {})
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [])

  const command = async (id, cmd, extra = {}) => {
    setBusy(true)
    try {
      await api(`/api/admin/ev/chargers/${id}/command`, { method: 'POST', body: { command: cmd, ...extra } })
      toast.success('Команд илгээгдлээ')
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  const register = async (form) => {
    setBusy(true)
    try {
      const r = await api('/api/admin/ev/chargers', { method: 'POST', body: form })
      toast.success(`Бүртгэгдлээ — QR түлхүүр: ${r.charger_key}`)
      if (r.hub_note) toast.error(r.hub_note)
      setReg(null); load()
    } catch (e) { toast.error(e.message) } finally { setBusy(false) }
  }

  if (!data) return <div className="p-8 text-slate-500 flex gap-2"><Loader2 className="animate-spin" />Ачаалж байна…</div>

  return (
    <div className="p-4 sm:p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold flex items-center gap-2"><Zap className="text-amber-400" />EV цэнэглэгч</h1>
        <button onClick={load} className="btn-secondary flex items-center gap-1.5 text-sm"><RefreshCw size={15} />Шинэчлэх</button>
      </div>
      {data.hub_error && (
        <div className="text-sm text-amber-300 bg-amber-500/10 rounded-xl px-4 py-3">
          Hub-тай холбогдож чадсангүй: {data.hub_error}
        </div>
      )}

      {/* Бүртгэлгүй (hub дээр шинээр гарч ирсэн) цэнэглэгчид */}
      {data.unregistered?.length > 0 && (
        <div className="bg-amber-500/10 rounded-2xl p-4 space-y-2">
          <div className="font-semibold text-amber-300">Шинэ (бүртгэлгүй) цэнэглэгчид</div>
          {data.unregistered.map((h) => (
            <div key={h.cp_id} className="flex items-center justify-between text-sm">
              <span>{h.cp_id} · {h.vendor} {h.model} {h.online ? '· онлайн' : '· офлайн'}</span>
              <button onClick={() => setReg({ cp_id: h.cp_id })}
                className="btn-secondary flex items-center gap-1 text-xs"><Plus size={13} />Бүртгэх</button>
            </div>
          ))}
        </div>
      )}

      {/* Цэнэглэгчдийн самбар */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {data.chargers.map((c) => (
          <div key={c.id} className="bg-slate-900 rounded-2xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="font-semibold">{c.name || c.cp_id}</div>
                <div className="text-xs text-slate-500">{c.cp_id} · {c.site_name} · QR: {c.charger_key}</div>
              </div>
              <span className={`text-xs px-2 py-1 rounded-full ${c.online ? 'bg-emerald-500/20 text-emerald-300' : 'bg-red-500/20 text-red-300'}`}>
                {c.online ? 'Онлайн' : 'Офлайн'}
              </span>
            </div>
            <div className="space-y-1.5">
              {(c.connectors || []).filter((x) => x.connector_id > 0).map((x) => (
                <div key={x.connector_id} className="flex items-center gap-2 text-sm">
                  <Plug size={14} className="text-slate-500" />
                  <span className="text-slate-400">{x.connector_id}-р бууц</span>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${STATUS_COLORS[x.status] || 'bg-slate-700'}`}>
                    {STATUS_LABELS[x.status] || x.status}
                  </span>
                  {x.power_w != null && x.status === 'Charging' && (
                    <span className="text-xs text-emerald-400 ml-auto">{(x.power_w / 1000).toFixed(1)} кВт{x.soc != null ? ` · ${x.soc}%` : ''}</span>
                  )}
                </div>
              ))}
              {(!c.connectors || c.connectors.length === 0) && (
                <div className="text-xs text-slate-600">Төлөв ирээгүй</div>
              )}
            </div>
            <div className="flex gap-2">
              <button onClick={() => command(c.id, 'reset')} disabled={busy || !c.online}
                className="btn-secondary flex items-center gap-1 text-xs disabled:opacity-40">
                <RotateCcw size={13} />Reset
              </button>
              <button onClick={() => command(c.id, 'unlock', { connector_id: 1 })} disabled={busy || !c.online}
                className="btn-secondary text-xs disabled:opacity-40">Unlock</button>
              <a href={`/ev/${c.charger_key}`} target="_blank" rel="noreferrer"
                className="btn-secondary text-xs ml-auto">QR хуудас</a>
            </div>
          </div>
        ))}
        {data.chargers.length === 0 && (
          <div className="text-slate-500 text-sm col-span-full">
            Цэнэглэгч бүртгэгдээгүй байна. Цэнэглэгчийг hub-д холбоод дээрх
            «Шинэ цэнэглэгчид» хэсгээс бүртгэнэ.
          </div>
        )}
      </div>

      {/* Сүүлийн цэнэглэлтүүд */}
      <div className="bg-slate-900 rounded-2xl overflow-x-auto">
        <div className="px-4 pt-4 font-semibold">Сүүлийн цэнэглэлтүүд</div>
        <table className="w-full text-sm mt-2">
          <thead className="text-slate-500 text-left">
            <tr>
              <th className="px-4 py-2">Огноо</th><th className="px-2 py-2">Дугаар</th>
              <th className="px-2 py-2">Цэнэглэгч</th><th className="px-2 py-2">Энерги</th>
              <th className="px-2 py-2">Дүн</th><th className="px-2 py-2">Төлөв</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {sessions.map((s) => (
              <tr key={s.id}>
                <td className="px-4 py-2 text-slate-400">
                  {new Date(s.created_at + 'Z').toLocaleString('mn-MN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}
                </td>
                <td className="px-2 py-2">{s.plate_number}</td>
                <td className="px-2 py-2 text-slate-400">{s.charger_name || s.cp_id}</td>
                <td className="px-2 py-2">{s.energy_wh != null ? `${fmt(s.energy_wh)} Wh` : `${fmt(s.last_energy_wh)} Wh…`}</td>
                <td className="px-2 py-2">{s.total_amount != null ? `${fmt(s.total_amount)}₮` : `(${fmt(s.authorized_amount)}₮ hold)`}</td>
                <td className="px-2 py-2 text-slate-400">{s.status}</td>
              </tr>
            ))}
            {sessions.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-600">Цэнэглэлт хараахан алга</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {reg && <RegisterModal cp={reg} sites={sites} busy={busy}
        onClose={() => setReg(null)} onSave={register} />}
    </div>
  )
}

function RegisterModal({ cp, sites, busy, onClose, onSave }) {
  const [form, setForm] = useState({
    cp_id: cp.cp_id, name: cp.cp_id, site_id: sites[0]?.id || '',
    connector_count: 2, auth_password: '',
  })
  return (
    <Modal open title={`Цэнэглэгч бүртгэх — ${cp.cp_id}`} onClose={onClose}>
      <div className="space-y-3">
        <Field label="Нэр">
          <input className="input" value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label="Зогсоол">
          <select className="input" value={form.site_id}
            onChange={(e) => setForm({ ...form, site_id: e.target.value })}>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        <Field label="Бууцны тоо">
          <input type="number" min="1" max="4" className="input" value={form.connector_count}
            onChange={(e) => setForm({ ...form, connector_count: Number(e.target.value) })} />
        </Field>
        <Field label="Шинэ OCPP нууц үг (заавал биш — provision нууц үгийг солино)">
          <input className="input" value={form.auth_password}
            onChange={(e) => setForm({ ...form, auth_password: e.target.value })} />
        </Field>
        <button onClick={() => onSave(form)} disabled={busy || !form.site_id}
          className="btn-primary w-full justify-center">
          {busy ? <Loader2 className="animate-spin" size={18} /> : 'Бүртгэх'}
        </button>
      </div>
    </Modal>
  )
}
