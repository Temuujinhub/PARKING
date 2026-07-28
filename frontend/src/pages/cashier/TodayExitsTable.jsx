// Өнөөдөр гарсан машинууд — гарах камерт уншсан бүх машин (төлбөргүй/үнэгүй ч)
import { fmt, fmtDur, fmtShort } from '../../api'
import { Table } from '../../components/ui'

export default function TodayExitsTable({ overview }) {
  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <h2 className="font-semibold">Өнөөдөр гарсан машинууд</h2>
        <span className="text-sm text-slate-400">{overview?.rows.length || 0} машин</span>
      </div>
      <Table headers={['Дугаар', 'Орсон', 'Гарсан', 'Хугацаа', 'Төрөл', 'Төлбөр', 'Хэрэгсэл', 'Төлөв', 'НӨАТ']}
        empty={!overview || overview.rows.length === 0}>
        {overview?.rows.map((r) => (
          <tr key={r.session_id}>
            <td className="td font-mono font-bold">
              {r.plate_number}
              {r.note && <span className="ml-1 cursor-help" title={r.note}>📝</span>}
            </td>
            <td className="td font-mono text-xs">{fmtShort(r.entry_time)}</td>
            <td className="td font-mono text-xs">{r.exit_time ? fmtShort(r.exit_time) : '—'}</td>
            <td className="td font-mono text-xs">{fmtDur(r.duration_minutes)}</td>
            <td className={`td text-xs font-medium ${r.car_type === 'Гэрээт' ? 'text-cyan-400' : r.car_type === 'Хөнгөлөлттэй' ? 'text-amber-400' : ''}`}>
              {r.car_type}{r.discount_name ? ` (${r.discount_name})` : ''}
            </td>
            <td className="td font-mono">{r.total_fee > 0 ? `${fmt(r.total_fee)}₮` : <span className="text-slate-500">Үнэгүй</span>}</td>
            <td className="td text-xs">{r.provider || <span className="text-slate-500">—</span>}</td>
            <td className="td">
              {r.paid ? <span className="text-accent text-xs">Төлсөн</span>
                : r.status === 'AWAITING_PAYMENT' ? <span className="text-amber-400 text-xs">Хүлээж буй</span>
                : <span className="text-slate-500 text-xs">Төлбөргүй</span>}
            </td>
            <td className="td text-xs">{r.ebarimt ? <span className="text-accent">✓</span> : <span className="text-slate-600">—</span>}</td>
          </tr>
        ))}
      </Table>
      <div className="text-xs text-slate-500 mt-2">
        Гарах камерт дугаар нь уншигдсан бүх машин (төлбөр аваагүй/үнэгүй гарсныг ч оруулав).
      </div>
    </div>
  )
}
