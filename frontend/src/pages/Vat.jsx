// Ибаримт — НӨАТ баримтын жагсаалт + ТЕГ мэдээ илгээлт
import { AlertTriangle, QrCode, Send } from 'lucide-react'
import { useState } from 'react'
import { api, fmt, fmtDate } from '../api'
import { useFetch } from '../hooks/useFetch'
import { Badge, Modal, Table, useToast } from '../components/ui'

export default function Vat() {
  const today = new Date().toISOString().slice(0, 10)
  const monthAgo = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10)
  const [from, setFrom] = useState(monthAgo)
  const [to, setTo] = useState(today)
  const [qrReceipt, setQrReceipt] = useState(null)
  const [sending, setSending] = useState(false)
  const toast = useToast()

  const { data: rows } = useFetch(`/api/reports/vat-receipts?date_from=${from}&date_to=${to}`, { initial: [] })
  const { data: info, reload: reloadInfo } = useFetch('/api/reports/vat-info', { initial: null })

  const sendData = async () => {
    setSending(true)
    try {
      const r = await api('/api/reports/vat-send', { method: 'POST' })
      toast(r.message || 'Мэдээ илгээгдлээ')
      reloadInfo()
    } catch (e) { toast(e.message, 'error') } finally { setSending(false) }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Ибаримт (НӨАТ)</h1>
        <div className="flex items-center gap-2">
          <input type="date" className="input w-40" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Эхлэх огноо" />
          <span className="text-slate-500">—</span>
          <input type="date" className="input w-40" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Дуусах огноо" />
          <button className="btn-primary" onClick={sendData} disabled={sending}
            title="Цугларсан баримтуудыг ТЕГ-ын нэгдсэн системд илгээнэ (автоматаар өдөрт 1 удаа явдаг)">
            <Send size={15} /> {sending ? 'Илгээж байна…' : 'Мэдээ илгээх'}
          </button>
        </div>
      </div>

      {/* e-Barimt-ийн 2 сувгийн тайлбар — түр нуусан (хэрэгтэй үед буцааж асаана) */}

      {/* ТЕГ-ын анхааруулга (сугалаа дуусах, илгээх хугацаа) */}
      {info?.warnings?.length > 0 && (
        <div className="card border-amber-500/50 bg-amber-500/5 space-y-1" role="alert">
          {info.warnings.map((w, i) => (
            <div key={i} className="flex items-center gap-2 text-sm text-amber-400">
              <AlertTriangle size={15} /> {w}
            </div>
          ))}
        </div>
      )}
      {info && (
        <div className="card py-3 flex flex-wrap gap-6 text-sm text-slate-400">
          <span>Сугалааны үлдэгдэл: <b className="font-mono text-slate-200">{fmt(info.leftLotteries)}</b></span>
          <span>Илгээгдээгүй баримт: <b className="font-mono text-slate-200">{fmt(info.unsentCount)}</b></span>
          <span>Сүүлд илгээсэн: <b className="font-mono text-slate-200">{info.lastSentDate || '-'}</b></span>
          {info.mock && <span className="text-amber-400 text-xs">MOCK горим</span>}
        </div>
      )}
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
              {r.status === 'SENT' && (
                <button className="btn-secondary py-1 px-2" onClick={() => setQrReceipt(r)}
                  aria-label="Баримтын QR харах" title="QR аюулгүй байдлын үүднээс 1 цаг л хадгалагдана">
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
              className="mx-auto w-56 h-56 bg-white rounded-xl p-2"
              onError={(e) => {
                e.target.outerHTML = '<div class="text-sm text-slate-400 py-8">QR-ийн хадгалах хугацаа (1 цаг) дууссан.<br/>ТЕГ-ын аюулгүй байдлын шаардлагаар QR кодыг байнга хадгалдаггүй.</div>'
              }} />
            {qrReceipt.lottery_code && (
              <div className="text-sm font-mono">Сугалаа: <b className="text-accent">{qrReceipt.lottery_code}</b></div>
            )}
            <div className="text-xs text-slate-500 font-mono break-all">{qrReceipt.ebarimt_id}</div>
          </div>
        )}
      </Modal>
    </div>
  )
}
