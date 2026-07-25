// Лог: аудит + LPR event (камерын уншилт — гарах OCR зөрүүг илрүүлэхэд)
import { Download } from 'lucide-react'
import { useState } from 'react'
import { api, fmtDate } from '../api'
import { useFetch } from '../hooks/useFetch'
import { Badge, Table, useToast } from '../components/ui'

export default function Logs() {
  const toast = useToast()
  const [tab, setTab] = useState('audit')
  const [plate, setPlate] = useState('')
  const [lane, setLane] = useState('')
  const { data: audit } = useFetch(tab === 'audit' ? '/api/reports/audit-logs' : null, { initial: [] })
  const lprParams = new URLSearchParams({ limit: 300 })
  if (plate.trim()) lprParams.set('plate', plate.trim())
  if (lane) lprParams.set('lane', lane)
  const { data: lpr } = useFetch(tab === 'lpr' ? `/api/reports/lpr-events?${lprParams}` : null, { initial: [] })

  const download = async (path, name) => {
    try {
      const blob = await api(path, { blob: true })
      const url = URL.createObjectURL(blob)
      const a = Object.assign(document.createElement('a'), { href: url, download: name })
      a.click(); URL.revokeObjectURL(url)
    } catch (e) { toast(e.message, 'error') }
  }
  const downloadAudit = () => download('/api/reports/audit-logs/excel', 'uildliin_log.xlsx')
  const downloadLpr = () => download(`/api/reports/lpr-events/excel?${lprParams}`, 'kamer_unshilt.xlsx')

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Лог</h1>
        <button className="btn-primary" onClick={tab === 'audit' ? downloadAudit : downloadLpr}>
          <Download size={16} /> Excel татах
        </button>
      </div>
      <div className="flex gap-1 border-b border-surface-border/60" role="tablist">
        {[['audit', 'Үйлдлийн лог'], ['lpr', 'Камерын event лог']].map(([v, l]) => (
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
              value={plate} onChange={(e) => setPlate(e.target.value.toUpperCase())}
              aria-label="Дугаараар шүүх" />
            <select className="input" value={lane} onChange={(e) => setLane(e.target.value)} aria-label="Чиглэл">
              <option value="">Орох + Гарах</option>
              <option value="entry">Зөвхөн Орох</option>
              <option value="exit">Зөвхөн Гарах</option>
            </select>
            <div className="text-xs text-slate-500 flex items-center px-1">
              Нэг машины орох ба гарах уншилтыг харьцуулж, гарах талд «таарсангүй»
              байвал OCR зөрүүг (үсэг андуурч уншсан) илрүүлнэ.
            </div>
          </div>
          <Table headers={['Огноо', 'Дугаар', 'Чиглэл', 'Камер', 'Итгэлцүүр', 'Хүлээн авсан', 'Session', 'Шалтгаан']}
            empty={lpr.length === 0}>
            {lpr.map((e) => (
              <tr key={e.id} className={e.lane_dir === 'exit' && e.matched === false ? 'bg-amber-500/10' : ''}>
                <td className="td font-mono text-xs">{fmtDate(e.created_at)}</td>
                <td className="td font-mono font-bold">{e.plate_number}</td>
                <td className="td text-xs">{e.lane_dir === 'entry'
                  ? <span className="text-cyan-400">Орох</span>
                  : <span className="text-amber-400">Гарах</span>}</td>
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
    </div>
  )
}
