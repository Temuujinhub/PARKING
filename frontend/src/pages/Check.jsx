// Шалгах — дугаараар нээлттэй session хайх
import { Search } from 'lucide-react'
import { useState } from 'react'
import { api, fmt, fmtDate, fmtDur } from '../api'
import { Badge, Table, useToast } from '../components/ui'

export default function Check() {
  const toast = useToast()
  const [plate, setPlate] = useState('')
  const [results, setResults] = useState(null)

  const search = async () => {
    if (!plate.trim()) return
    try { setResults(await api(`/api/sessions/check?plate=${encodeURIComponent(plate)}`)) }
    catch (e) { toast(e.message, 'error') }
  }

  return (
    <div className="space-y-5 max-w-4xl">
      <h1 className="text-2xl font-bold">Шалгах</h1>
      <div className="card">
        <div className="flex gap-2">
          <input className="input text-lg font-mono" placeholder="Улсын дугаар… (1234АБВ)"
            value={plate} onChange={(e) => setPlate(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === 'Enter' && search()} autoFocus aria-label="Улсын дугаар" />
          <button onClick={search} className="btn-primary"><Search size={17} /> Хайх</button>
        </div>
      </div>
      {results && (
        <Table headers={['Дугаар', 'Зогсоол', 'Орсон', 'Хугацаа', 'Дүн', 'Төлөв']} empty={results.length === 0}>
          {results.map((s) => (
            <tr key={s.id}>
              <td className="td font-mono font-bold">{s.plate_number}</td>
              <td className="td">{s.site_name}</td>
              <td className="td font-mono text-xs">{fmtDate(s.entry_time)}</td>
              <td className="td font-mono">{fmtDur(s.fee?.duration_minutes ?? s.duration_minutes)}</td>
              <td className="td font-mono font-semibold">{fmt(s.fee?.total_fee ?? s.total_fee)}₮</td>
              <td className="td"><Badge value={s.status} /></td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  )
}
