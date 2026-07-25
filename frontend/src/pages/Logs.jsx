// Лог: аудит + камерын уншилт (LPR) + хаалтны команд.
// Хаалтны команд таб нь "төлбөргүй машин ЯМАР аргаар хаалт нээж гарсан"-ыг харуулна
// (эх сурвалж: авто гарах / төлбөр / гараар; команд огт байхгүй = tailgating).
import { Download } from 'lucide-react'
import { useState } from 'react'
import { api, fmtDate } from '../api'
import { useFetch } from '../hooks/useFetch'
import { Badge, Table, useToast } from '../components/ui'

const TABS = [['audit', 'Үйлдлийн лог'], ['lpr', 'Камерын event лог'], ['barrier', 'Хаалтны команд']]

export default function Logs() {
  const toast = useToast()
  const [tab, setTab] = useState('audit')
  const [plate, setPlate] = useState('')
  const [lane, setLane] = useState('')
  const [source, setSource] = useState('')
  const { data: audit } = useFetch(tab === 'audit' ? '/api/reports/audit-logs' : null, { initial: [] })

  const lprParams = new URLSearchParams({ limit: 300 })
  if (plate.trim()) lprParams.set('plate', plate.trim())
  if (lane) lprParams.set('lane', lane)
  const { data: lpr } = useFetch(tab === 'lpr' ? `/api/reports/lpr-events?${lprParams}` : null, { initial: [] })

  const barParams = new URLSearchParams({ limit: 300 })
  if (plate.trim()) barParams.set('plate', plate.trim())
  if (source) barParams.set('source', source)
  const { data: bar } = useFetch(tab === 'barrier' ? `/api/reports/barrier-commands?${barParams}` : null, { initial: [] })

  const download = async (path, name) => {
    try {
      const blob = await api(path, { blob: true })
      const url = URL.createObjectURL(blob)
      const a = Object.assign(document.createElement('a'), { href: url, download: name })
      a.click(); URL.revokeObjectURL(url)
    } catch (e) { toast(e.message, 'error') }
  }
  const doDownload = () => {
    if (tab === 'audit') return download('/api/reports/audit-logs/excel', 'uildliin_log.xlsx')
    if (tab === 'lpr') return download(`/api/reports/lpr-events/excel?${lprParams}`, 'kamer_unshilt.xlsx')
    return download(`/api/reports/barrier-commands/excel?${barParams}`, 'haalt_komand.xlsx')
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Лог</h1>
        <button className="btn-primary" onClick={doDownload}><Download size={16} /> Excel татах</button>
      </div>
      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {TABS.map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
      </div>

      {tab === 'audit' && (
        <Table headers={['Огноо', 'Хэрэглэгч', 'Үйлдэл', 'Объект', 'Дэлгэрэнгүй']} empty={audit.length === 0}>
          {audit.map((a) => (
            <tr key={a.id}>
              <td className="td font-mono text-xs">{fmtDate(a.created_at)}</td>
              <td className="td font-mono">{a.username}</td>
              <td className="td"><span className="text-xs font-mono bg-surface-muted px-2 py-0.5 rounded">{a.action}</span></td>
              <td className="td text-xs">{a.entity}</td>
              <td className="td text-xs text-slate-500 max-w-md truncate">{JSON.stringify(a.detail)}</td>
            </tr>
          ))}
        </Table>
      )}

      {tab === 'lpr' && (
        <>
          <div className="card grid grid-cols-1 md:grid-cols-3 gap-3">
            <input className="input font-mono" placeholder="Дугаараар шүүх (эхний тоо: ж 3970)…"
              value={plate} onChange={(e) => setPlate(e.target.value.toUpperCase())} aria-label="Дугаараар шүүх" />
            <select className="input" value={lane} onChange={(e) => setLane(e.target.value)} aria-label="Чиглэл">
              <option value="">Орох + Гарах</option>
              <option value="entry">Зөвхөн Орох</option>
              <option value="exit">Зөвхөн Гарах</option>
            </select>
            <div className="text-xs text-slate-500 flex items-center px-1">
              Нэг машины орох ба гарах уншилтыг харьцуулж, гарах талд «таарсангүй»
              байвал OCR зөрүүг илрүүлнэ.
            </div>
          </div>
          <Table headers={['Огноо', 'Дугаар', 'Чиглэл', 'Камер', 'Итгэлцүүр', 'Хүлээн авсан', 'Session', 'Шалтгаан']}
            empty={lpr.length === 0}>
            {lpr.map((e) => (
              <tr key={e.id} className={e.lane_dir === 'exit' && e.matched === false ? 'bg-amber-500/10' : ''}>
                <td className="td font-mono text-xs">{fmtDate(e.created_at)}</td>
                <td className="td font-mono font-bold">{e.plate_number}</td>
                <td className="td text-xs">{e.lane_dir === 'entry'
                  ? <span className="text-cyan-400">Орох</span> : <span className="text-amber-400">Гарах</span>}</td>
                <td className="td text-xs text-slate-400">{e.device_name || '-'}</td>
                <td className="td font-mono">{e.confidence?.toFixed(0)}%</td>
                <td className="td"><Badge value={e.accepted ? 'SUCCESS' : 'FAILED'} /></td>
                <td className="td text-xs">
                  {e.lane_dir !== 'exit' ? <span className="text-slate-600">-</span>
                    : e.matched ? <span className="text-green-400">таарсан</span>
                      : <span className="text-amber-400 font-semibold">таарсангүй</span>}
                </td>
                <td className="td text-xs text-slate-500">{e.reject_reason || '-'}</td>
              </tr>
            ))}
          </Table>
        </>
      )}

      {tab === 'barrier' && (
        <>
          <div className="card grid grid-cols-1 md:grid-cols-3 gap-3">
            <input className="input font-mono" placeholder="Дугаараар шүүх…"
              value={plate} onChange={(e) => setPlate(e.target.value.toUpperCase())} aria-label="Дугаараар шүүх" />
            <select className="input" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Эх сурвалж">
              <option value="">Бүх эх сурвалж</option>
              <option value="auto_exit">Авто гарах (үнэгүй/төлсөн)</option>
              <option value="payment">Төлбөрийн дараа</option>
              <option value="manual">Гараар (оператор)</option>
              <option value="auto_entry">Авто орох</option>
              <option value="whitelist">Цагаан жагсаалт</option>
            </select>
            <div className="text-xs text-slate-500 flex items-center px-1">
              Төлбөргүй машин гарсан бол дугаараар нь шүүж, хаалт ЯМАР эх сурвалжаар
              нээгдсэнийг хар. Команд огт байхгүй бол = tailgating (дагаж гарсан).
            </div>
          </div>
          <Table headers={['Огноо', 'Дугаар', 'Команд', 'Эх сурвалж', 'Төлөв', 'Хаалт', 'Оператор', 'Хариу']}
            empty={bar.length === 0}>
            {bar.map((c) => (
              <tr key={c.id}>
                <td className="td font-mono text-xs">{fmtDate(c.created_at)}</td>
                <td className="td font-mono font-bold">{c.plate_number || '-'}</td>
                <td className="td text-xs">{c.command === 'open' ? 'Нээх' : c.command === 'close' ? 'Хаах' : c.command}</td>
                <td className="td text-xs">{c.source_mn}</td>
                <td className="td"><Badge value={c.status} /></td>
                <td className="td text-xs text-slate-400">{c.device_name || '-'}</td>
                <td className="td text-xs text-slate-400">{c.issued_by || '-'}</td>
                <td className="td text-xs text-slate-500 max-w-xs truncate">{c.response_text || '-'}</td>
              </tr>
            ))}
          </Table>
        </>
      )}
    </div>
  )
}
