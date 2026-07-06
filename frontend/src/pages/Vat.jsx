// Ибаримт — НӨАТ баримтын жагсаалт
import { QrCode } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate } from '../api'
import { Badge, Modal, Table } from '../components/ui'

export default function Vat() {
  const [rows, setRows] = useState([])
  const today = new Date().toISOString().slice(0, 10)
  const monthAgo = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10)
  const [from, setFrom] = useState(monthAgo)
  const [to, setTo] = useState(today)
  const [qrReceipt, setQrReceipt] = useState(null)

  useEffect(() => {
    api(`/api/reports/vat-receipts?date_from=${from}&date_to=${to}`).then(setRows).catch(() => {})
  }, [from, to])

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Ибаримт (НӨАТ)</h1>
        <div className="flex items-center gap-2">
          <input type="date" className="input w-40" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Эхлэх огноо" />
          <span className="text-slate-500">—</span>
          <input type="date" className="input w-40" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Дуусах огноо" />
        </div>
      </div>
      <Table headers={['ДДТД (billId)', 'Сугалааны код', 'Дүн', 'НӨАТ', 'Огноо', 'Төлөв', 'QR']} empty={rows.length === 0}>
        {rows.map((r) => (
          <tr key={r.id}>
            <td className="td font-mono text-[10px] max-w-[16rem] break-all">{r.ebarimt_id || '-'}</td>
            <td className="td font-mono font-semibold">{r.lottery_code || '-'}</td>
            <td className="td font-mono">{fmt(r.amount)}₮</td>
            <td className="td font-mono">{fmt(r.vat_amount)}₮</td>
            <td className="td font-mono text-xs">{fmtDate(r.created_at)}</td>
            <td className="td"><Badge value={r.status} /></td>
            <td className="td">
              {r.receipt_url && (
                <button className="btn-secondary py-1 px-2" onClick={() => setQrReceipt(r)} aria-label="Баримтын QR харах">
                  <QrCode size={14} />
                </button>
              )}
            </td>
          </tr>
        ))}
      </Table>

      <Modal open={!!qrReceipt} onClose={() => setQrReceipt(null)} title="e-Barimt баримтын QR">
        {qrReceipt && (
          <div className="text-center space-y-3">
            <img src={`/api/public/receipt/${qrReceipt.payment_id}/qr.png`} alt="e-Barimt QR код"
              className="mx-auto w-56 h-56 bg-white rounded-xl p-2" />
            <div className="text-sm font-mono">Сугалаа: <b className="text-accent">{qrReceipt.lottery_code}</b></div>
            <div className="text-xs text-slate-500 font-mono break-all">{qrReceipt.ebarimt_id}</div>
          </div>
        )}
      </Modal>
    </div>
  )
}
