// Лог: аудит + LPR event
import { Download } from 'lucide-react'
import { useState } from 'react'
import { api, fmtDate } from '../api'
import { useFetch } from '../hooks/useFetch'
import { Badge, Table, useToast } from '../components/ui'

export default function Logs() {
  const toast = useToast()
  const [tab, setTab] = useState('audit')
  const { data: audit } = useFetch(tab === 'audit' ? '/api/reports/audit-logs' : null, { initial: [] })
  const { data: lpr } = useFetch(tab === 'lpr' ? '/api/reports/lpr-events' : null, { initial: [] })

  const downloadAudit = async () => {
    try {
      const blob = await api('/api/reports/audit-logs/excel', { blob: true })
      const url = URL.createObjectURL(blob)
      const a = Object.assign(document.createElement('a'), { href: url, download: 'uildliin_log.xlsx' })
      a.click(); URL.revokeObjectURL(url)
    } catch (e) { toast(e.message, 'error') }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Лог</h1>
        {tab === 'audit' && (
          <button className="btn-primary" onClick={downloadAudit}><Download size={16} /> Excel татах</button>
        )}
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
        <Table headers={['Огноо', 'Дугаар', 'Чиглэл', 'Итгэлцүүр', 'Хүлээн авсан', 'Татгалзсан шалтгаан']} empty={lpr.length === 0}>
          {lpr.map((e) => (
            <tr key={e.id}>
              <td className="td font-mono text-xs">{fmtDate(e.created_at)}</td>
              <td className="td font-mono font-bold">{e.plate_number}</td>
              <td className="td text-xs">{e.lane_dir === 'entry' ? 'Орох' : 'Гарах'}</td>
              <td className="td font-mono">{e.confidence?.toFixed(0)}%</td>
              <td className="td"><Badge value={e.accepted ? 'SUCCESS' : 'FAILED'} /></td>
              <td className="td text-xs text-slate-500">{e.reject_reason || '-'}</td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  )
}
