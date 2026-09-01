// Санхүүгийн мөнгөн тооцоо — pos-Карт/pos-QPay/QR-QPay/Бэлэн; зөвхөн бэлэнг санхүү тулгана
import { Download, Lock, Unlock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, preferredSite, rememberSite } from '../api'
import { useFetch } from '../hooks/useFetch'
import { Badge, DateRange, Table, useToast } from '../components/ui'
import { clampNum, toDateInput } from '../validation'

export default function Settlement() {
  const toast = useToast()
  // ЛОКАЛ огноо (toISOString нь UTC — УБ-д шөнө 00:00–08:00-д «өчигдөр» гаргадаг байв)
  const today = toDateInput()
  const weekAgo = toDateInput(new Date(Date.now() - 14 * 864e5))
  const [siteId, setSiteId] = useState('')
  const [from, setFrom] = useState(weekAgo)
  const [to, setTo] = useState(today)
  const [edit, setEdit] = useState({}) // {date: {cash, transfer}} — баталгаажуулах дүнгүүд

  const { data: sites } = useFetch('/api/admin/sites', { initial: [], silent: true })
  useEffect(() => { if (sites.length && !siteId) setSiteId(preferredSite(sites)) }, [sites]) // сүүлд сонгосон (эсвэл эхний) зогсоол
  const { data: settlement, reload: load } = useFetch(
    siteId ? `/api/reports/settlement?site_id=${siteId}&date_from=${from}&date_to=${to}` : null,
    { initial: { rows: [] } })
  const rows = settlement?.rows || []
  useEffect(() => { setEdit({}) }, [settlement]) // шинэ өгөгдөл ирэхэд гараар засварыг цэвэрлэнэ

  const cashVal = (r) => (edit[r.date]?.cash ?? r.confirmed_cash)
  const trVal = (r) => (edit[r.date]?.transfer ?? r.confirmed_transfer)

  const save = async (r, status) => {
    try {
      await api('/api/reports/settlement', {
        method: 'PUT',
        body: { site_id: siteId, date: r.date, status,
                // Баталгаажсан дүн сөрөг байж болохгүй — зөрүүг эсрэг тэмдэгтэй харуулж
                // тооцоог «хаасан» мэт харагдуулах эрсдэлээс сэргийлнэ
                confirmed_cash: clampNum(cashVal(r), { min: 0, max: 1_000_000_000, int: false }),
                confirmed_transfer: clampNum(trVal(r), { min: 0, max: 1_000_000_000, int: false }) },
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
          <select className="input w-auto" value={siteId} onChange={(e) => { setSiteId(e.target.value); rememberSite(e.target.value) }} aria-label="Зогсоол">
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <DateRange from={from} to={to} setFrom={setFrom} setTo={setTo} />
          <button className="btn-primary" onClick={download}><Download size={16} /> Excel</button>
        </div>
      </div>

      <div className="text-xs text-slate-400">
        <b className="text-accent">pos-Карт · pos-QPay · QR-QPay</b> нь банкаар электрон баталгаажсан тул засахгүй. {' '}
        <b className="text-amber-400">Бэлэн</b> ба <b className="text-amber-400">Дансаар (шилжүүлэг)</b>-ийг санхүү
        дансны хуулгаас баталгаажуулж, зөрүү 0 болмогц тооцоог хаана.
      </div>

      <div className="overflow-x-auto">
        <Table headers={['Огноо', 'pos-Карт', 'pos-QPay', 'QR-QPay', 'Систем бэлэн', 'Баталгаа бэлэн', 'Систем данс', 'Баталгаа данс', 'Зөрүү', 'Өр (үүссэн)', 'Ажилтан', 'Төлөв', 'Үйлдэл']}
          empty={rows.length === 0}>
          {rows.map((r) => {
            const closed = r.status === 'CLOSED'
            const diff = (r.cash - (+cashVal(r) || 0)) + (r.transfer - (+trVal(r) || 0))
            return (
              <tr key={r.date}>
                <td className="td font-mono font-medium">{r.date}</td>
                <td className="td font-mono text-slate-300">{fmt(r.card)}₮</td>
                <td className="td font-mono text-slate-300">{fmt(r.pos_qpay)}₮</td>
                <td className="td font-mono text-slate-300">{fmt(r.qr_qpay)}₮</td>
                <td className="td font-mono">{fmt(r.cash)}₮</td>
                <td className="td">
                  <input type="number" min="0" step="100" className="input w-24 py-1 text-sm font-mono" disabled={closed}
                    value={cashVal(r)} placeholder={fmt(r.cash)}
                    onChange={(e) => setEdit((x) => ({ ...x, [r.date]: { ...x[r.date], cash: e.target.value } }))} />
                </td>
                <td className="td font-mono">{fmt(r.transfer)}₮</td>
                <td className="td">
                  <input type="number" min="0" step="100" className="input w-24 py-1 text-sm font-mono" disabled={closed}
                    value={trVal(r)} placeholder={fmt(r.transfer)}
                    onChange={(e) => setEdit((x) => ({ ...x, [r.date]: { ...x[r.date], transfer: e.target.value } }))} />
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
