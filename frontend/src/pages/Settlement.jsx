// Санхүүгийн мөнгөн тооцоо — зогсоол/өдрөөр систем vs дансны баталгаажсан дүн тулгах
import { Download, Lock, Unlock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt } from '../api'
import { Badge, Table, useToast } from '../components/ui'

export default function Settlement() {
  const toast = useToast()
  const today = new Date().toISOString().slice(0, 10)
  const weekAgo = new Date(Date.now() - 14 * 864e5).toISOString().slice(0, 10)
  const [sites, setSites] = useState([])
  const [siteId, setSiteId] = useState('')
  const [from, setFrom] = useState(weekAgo)
  const [to, setTo] = useState(today)
  const [rows, setRows] = useState([])
  const [edit, setEdit] = useState({}) // {date: {confirmed_card, confirmed_qpay, confirmed_cash, note}}

  useEffect(() => {
    api('/api/admin/sites').then((s) => { setSites(s); if (s.length && !siteId) setSiteId(s[0].id) }).catch(() => {})
  }, [])

  const load = () => {
    if (!siteId) return
    api(`/api/reports/settlement?site_id=${siteId}&date_from=${from}&date_to=${to}`)
      .then((d) => { setRows(d.rows); setEdit({}) }).catch(() => {})
  }
  useEffect(load, [siteId, from, to])

  const setField = (date, k, v) => setEdit((e) => ({ ...e, [date]: { ...(e[date] || {}), [k]: v } }))
  const rowVal = (r, k) => (edit[r.date]?.[k] ?? r[k])

  const save = async (r, status) => {
    try {
      await api('/api/reports/settlement', {
        method: 'PUT',
        body: {
          site_id: siteId, date: r.date, status,
          confirmed_card: +rowVal(r, 'confirmed_card') || 0,
          confirmed_qpay: +rowVal(r, 'confirmed_qpay') || 0,
          confirmed_cash: +rowVal(r, 'confirmed_cash') || 0,
          note: rowVal(r, 'note') || '',
        },
      })
      toast(status === 'CLOSED' ? 'Тооцоо хаагдлаа' : 'Хадгалагдлаа'); load()
    } catch (e) { toast(e.message, 'error') }
  }

  const download = async () => {
    try {
      const blob = await api(`/api/reports/settlement/excel?site_id=${siteId}&date_from=${from}&date_to=${to}`, { blob: true })
      const url = URL.createObjectURL(blob)
      Object.assign(document.createElement('a'), { href: url, download: `montoo_${from}_${to}.xlsx` }).click()
      URL.revokeObjectURL(url)
    } catch (e) { toast(e.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Мөнгөн тооцоо</h1>
          <p className="text-sm text-slate-400">Системийн борлуулалт ба дансны баталгаажсан дүнг өдрөөр тулгана</p>
        </div>
        <div className="flex items-center gap-2">
          <select className="input w-auto" value={siteId} onChange={(e) => setSiteId(e.target.value)} aria-label="Зогсоол">
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <input type="date" className="input w-40" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Эхлэх" />
          <span className="text-slate-500">—</span>
          <input type="date" className="input w-40" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Дуусах" />
          <button className="btn-primary" onClick={download}><Download size={16} /> Excel</button>
        </div>
      </div>

      <div className="text-xs text-slate-400">
        <b className="text-slate-200">Систем</b> = POS/утаснаас төлөгдсөн (карт/QPay) + кассын бэлэн. {' '}
        <b className="text-slate-200">Баталгаа</b> = санхүү дансны хуулгаас оруулна (бэлэн нь ATM-ээр дансанд орсон дүн). {' '}
        Зөрүү 0 болмогц тухайн өдрийн тооцоог хаана.
      </div>

      <div className="overflow-x-auto">
        <Table headers={['Огноо', 'Систем нийт', 'Баталгаа: Карт', 'QPay', 'Бэлэн', 'Баталгаа нийт', 'Зөрүү', 'Төлөв', 'Үйлдэл']}
          empty={rows.length === 0}>
          {rows.map((r) => {
            const closed = r.status === 'CLOSED'
            const confTotal = (+rowVal(r, 'confirmed_card') || 0) + (+rowVal(r, 'confirmed_qpay') || 0) + (+rowVal(r, 'confirmed_cash') || 0)
            const diff = r.system_total - confTotal
            return (
              <tr key={r.date}>
                <td className="td font-mono font-medium">{r.date}</td>
                <td className="td font-mono" title={`Карт ${fmt(r.system_card)} · QPay ${fmt(r.system_qpay)} · Бэлэн ${fmt(r.system_cash)}`}>
                  {fmt(r.system_total)}₮
                </td>
                {['confirmed_card', 'confirmed_qpay', 'confirmed_cash'].map((k) => (
                  <td key={k} className="td">
                    <input type="number" className="input w-24 py-1 text-sm font-mono" disabled={closed}
                      value={rowVal(r, k)} onChange={(e) => setField(r.date, k, e.target.value)}
                      placeholder={fmt({ confirmed_card: r.system_card, confirmed_qpay: r.system_qpay, confirmed_cash: r.system_cash }[k])} />
                  </td>
                ))}
                <td className="td font-mono">{fmt(confTotal)}₮</td>
                <td className={`td font-mono font-semibold ${diff === 0 ? 'text-accent' : diff > 0 ? 'text-amber-400' : 'text-red-400'}`}>
                  {diff > 0 ? '+' : ''}{fmt(diff)}₮
                </td>
                <td className="td"><Badge value={closed ? 'CLOSED' : 'active'} /></td>
                <td className="td text-right whitespace-nowrap">
                  {closed ? (
                    <button className="btn-secondary py-1 text-xs" onClick={() => save(r, 'OPEN')}>
                      <Unlock size={13} /> Нээх
                    </button>
                  ) : (<>
                    <button className="btn-secondary py-1 text-xs mr-1" onClick={() => save(r, 'OPEN')}>Хадгалах</button>
                    <button className="btn-primary py-1 text-xs" onClick={() => save(r, 'CLOSED')} title="Дансны хуулгатай тулгасны дараа хаана">
                      <Lock size={13} /> Хаах
                    </button>
                  </>)}
                </td>
              </tr>
            )
          })}
        </Table>
      </div>
    </div>
  )
}
