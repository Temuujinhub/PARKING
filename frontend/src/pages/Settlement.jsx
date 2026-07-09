// Санхүүгийн мөнгөн тооцоо — pos-Карт/pos-QPay/QR-QPay/Бэлэн; зөвхөн бэлэнг санхүү тулгана
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
  const [edit, setEdit] = useState({}) // {date: confirmed_cash}

  useEffect(() => {
    api('/api/admin/sites').then((s) => { setSites(s); if (s.length && !siteId) setSiteId(s[0].id) }).catch(() => {})
  }, [])

  const load = () => {
    if (!siteId) return
    api(`/api/reports/settlement?site_id=${siteId}&date_from=${from}&date_to=${to}`)
      .then((d) => { setRows(d.rows); setEdit({}) }).catch(() => {})
  }
  useEffect(load, [siteId, from, to])

  const cashVal = (r) => (edit[r.date] ?? r.confirmed_cash)

  const save = async (r, status) => {
    try {
      await api('/api/reports/settlement', {
        method: 'PUT',
        body: { site_id: siteId, date: r.date, status, confirmed_cash: +cashVal(r) || 0 },
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
        <b className="text-accent">pos-Карт · pos-QPay · QR-QPay</b> нь банкаар электрон баталгаажсан тул засахгүй. {' '}
        <b className="text-amber-400">Бэлэн</b>-г санхүү дансны хуулгаас (ATM-ээр орсон дүн) баталгаажуулж, зөрүү 0 болмогц тооцоог хаана.
      </div>

      <div className="overflow-x-auto">
        <Table headers={['Огноо', 'pos-Карт', 'pos-QPay', 'QR-QPay', 'Систем бэлэн', 'Баталгаа бэлэн', 'Зөрүү', 'Өр (үүссэн)', 'Ажилтан', 'Төлөв', 'Үйлдэл']}
          empty={rows.length === 0}>
          {rows.map((r) => {
            const closed = r.status === 'CLOSED'
            const diff = r.cash - (+cashVal(r) || 0)
            return (
              <tr key={r.date}>
                <td className="td font-mono font-medium">{r.date}</td>
                <td className="td font-mono text-slate-300">{fmt(r.card)}₮</td>
                <td className="td font-mono text-slate-300">{fmt(r.pos_qpay)}₮</td>
                <td className="td font-mono text-slate-300">{fmt(r.qr_qpay)}₮</td>
                <td className="td font-mono">{fmt(r.cash)}₮</td>
                <td className="td">
                  <input type="number" className="input w-24 py-1 text-sm font-mono" disabled={closed}
                    value={cashVal(r)} placeholder={fmt(r.cash)}
                    onChange={(e) => setEdit((x) => ({ ...x, [r.date]: e.target.value }))} />
                </td>
                <td className={`td font-mono font-semibold ${diff === 0 ? 'text-accent' : 'text-red-400'}`}>
                  {diff > 0 ? '+' : ''}{fmt(diff)}₮
                </td>
                <td className={`td font-mono text-xs ${r.debt > 0 ? 'text-red-400' : 'text-slate-500'}`}>{fmt(r.debt)}₮</td>
                <td className="td text-xs">{r.workers.length ? r.workers.join(', ') : <span className="text-slate-600">—</span>}</td>
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
