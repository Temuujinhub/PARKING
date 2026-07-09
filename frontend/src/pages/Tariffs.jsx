// Тарифын загвар — Санхүү цэс. Загвар CRUD + аль зогсоол ямар тариф мөрдөж байгаа
import { Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt } from '../api'
import { useFetch } from '../hooks/useFetch'
import { Field, Modal, Table, useToast } from '../components/ui'

export default function Tariffs() {
  const [tab, setTab] = useState('templates')
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Тарифын загвар</h1>
      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {[['templates', 'Тарифын загвар'], ['sites', 'Зогсоол-тариф']].map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>
      {tab === 'templates' ? <Templates /> : <SiteTariffs />}
    </div>
  )
}

// Аль зогсоол ямар тариф мөрдөж байгааг жагсаалт + шууд засах
function SiteTariffs() {
  const toast = useToast()
  const { data: rows, reload: load } = useFetch('/api/admin/sites', { initial: [] })
  const { data: templates } = useFetch('/api/admin/tariff-templates', { initial: [] })

  const changeTariff = async (siteId, tariff_template_id) => {
    try {
      await api(`/api/admin/sites/${siteId}/tariff`, { method: 'PUT', body: { tariff_template_id } })
      toast('Тариф солигдлоо'); load()
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <Table headers={['Зогсоол', 'Код', 'Бүс', 'Багтаамж', 'Мөрдөж буй тариф']} empty={rows.length === 0}>
      {rows.map((s) => (
        <tr key={s.id}>
          <td className="td font-medium">{s.name}</td>
          <td className="td font-mono">{s.site_code}</td>
          <td className="td">{s.zone_code}</td>
          <td className="td font-mono">{s.capacity}</td>
          <td className="td">
            <select className="input w-auto py-1 text-sm" value={s.tariff_template_id || ''}
              onChange={(e) => changeTariff(s.id, e.target.value)} aria-label="Тариф солих">
              <option value="">Тариф холбоогүй</option>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </td>
        </tr>
      ))}
    </Table>
  )
}

function Templates() {
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
