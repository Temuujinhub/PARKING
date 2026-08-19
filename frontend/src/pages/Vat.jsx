// Ибаримт — НӨАТ баримтын жагсаалт + ТЕГ мэдээ илгээлт
import { AlertTriangle, Ban, QrCode, RefreshCw, Send } from 'lucide-react'
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
  const [retrying, setRetrying] = useState(null)
  const [cancelling, setCancelling] = useState(null)

  const [sending, setSending] = useState(false)
  const toast = useToast()

  const { data: rows, reload: reloadRows } = useFetch(`/api/reports/vat-receipts?date_from=${from}&date_to=${to}`, { initial: [] })
  const { data: info, reload: reloadInfo } = useFetch('/api/reports/vat-info', { initial: null })

  // Бүтэлгүйтсэн баримтыг дахин үүсгэх — ТӨЛБӨРИЙГ ДАХИН АВАХГҮЙ.
  // QPay талд «И баримт» тохиргоо идэвхжсэний дараа хуучин баримтуудыг нөхөхөд.
  const retry = async (r) => {
    setRetrying(r.id)
    try {
      const res = await api(`/api/payments/${r.payment_id}/retry-ebarimt`, { method: 'POST' })
      toast(`Баримт үүслээ — ДДТД ${res.ebarimt_id}`)
      reloadRows()
    } catch (e) { toast(e.message, 'error') } finally { setRetrying(null) }
  }

  // Баримт ЦУЦЛАХ (буцаалт) — мөнгө буцаахгүй, зөвхөн татварын баримт.
  // Суваг бүрээр: QPay → DELETE ebarimt_v3, PosAPI → DELETE /rest/receipt,
  // msgbill → DELETE /partner/receipts/{id} (msgbill талд нэмэгдэх хүртэл
  // «дэмжигдээгүй» гэж буцна). Цуцалсны дараа «Дахин үүсгэх» ШИНЭ баримт гаргана
  // (буруу ТТД/дүнтэй баримтыг засах урсгал).
  const cancel = async (r) => {
    const note = prompt(`${r.plate_number || ''} — ${fmt(r.amount)}₮ баримтыг ЦУЦЛАХ уу?\n`
      + 'Мөнгө буцаагдахгүй, зөвхөн НӨАТ баримт буцаагдана. Шалтгаан:', 'Буруу баримт — дахин үүсгэнэ')
    if (note === null) return
    setCancelling(r.id)
    try {
      const res = await api(`/api/payments/${r.payment_id}/cancel-ebarimt`, { method: 'POST', body: { note } })
      toast(res.ok
        ? `Баримт цуцлагдлаа (${(res.cancelled || []).length}) — шаардлагатай бол «Дахин үүсгэх» дарна уу`
        : (res.error || 'Цуцлалт хүлээгдэж байна'), res.ok ? undefined : 'error')
      reloadRows()
    } catch (e) { toast(e.message, 'error') } finally { setCancelling(null) }
  }

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
      {info && !info.scoped && (
        <div className="card py-3 flex flex-wrap gap-6 text-sm text-slate-400">
          <span>Сугалааны үлдэгдэл: <b className="font-mono text-slate-200">{fmt(info.leftLotteries)}</b></span>
          <span>Илгээгдээгүй баримт: <b className="font-mono text-slate-200">{fmt(info.unsentCount)}</b></span>
          <span>Сүүлд илгээсэн: <b className="font-mono text-slate-200">{info.lastSentDate || '-'}</b></span>
          {info.mock && <span className="text-amber-400 text-xs">MOCK горим</span>}
        </div>
      )}
      <Table headers={['Дугаар', 'Зогсоол', 'ДДТД (billId)', 'Сугалааны код', 'Дүн', 'НӨАТ', 'Огноо', 'Төлөв', 'Шалтгаан', 'Үйлдэл']} empty={rows.length === 0}>
        {rows.map((r) => (
          <tr key={r.id}>
            <td className="td font-mono font-bold">{r.plate_number || '—'}</td>
            <td className="td text-xs">{r.site_name || '—'}</td>
            <td className="td font-mono text-[10px] max-w-[16rem] break-all">{r.ebarimt_id || '-'}</td>
            <td className="td font-mono font-semibold">{r.lottery_code || '-'}</td>
            <td className="td font-mono">{fmt(r.amount)}₮</td>
            <td className="td font-mono">{fmt(r.vat_amount)}₮</td>
            <td className="td font-mono text-xs">{fmtDate(r.created_at)}</td>
            <td className="td">
              <Badge value={r.status} />
              {r.provider && <div className="text-[10px] text-slate-500 mt-0.5">{r.provider === 'MSGBILL' ? 'msgbill.mn' : r.provider === 'QPAY' ? 'QPay' : r.provider === 'TERMINAL' ? 'POS терминал' : 'PosAPI'}</div>}
            </td>
            <td className={`td text-[11px] max-w-[14rem] break-words ${r.status === 'CANCELLED' ? 'text-slate-400' : 'text-amber-400'}`}>
              {['FAILED', 'CANCELLED', 'CANCEL_PENDING'].includes(r.status) ? (r.receipt_url || '—') : ''}
            </td>
            <td className="td whitespace-nowrap">
              <div className="flex items-center gap-1">
                {r.status === 'SENT' && (
                  <button className="btn-secondary py-1 px-2" onClick={() => setQrReceipt(r)}
                    aria-label="Баримтын QR харах" title="QR аюулгүй байдлын үүднээс 1 цаг л хадгалагдана">
                    <QrCode size={14} />
                  </button>
                )}
                {(r.status === 'FAILED' || r.status === 'CANCELLED' || !r.ebarimt_id) && (
                  <button className="btn-secondary py-1 px-2 text-xs" disabled={retrying === r.id}
                    onClick={() => retry(r)}
                    title="Мөнгө дахин авахгүй — зөвхөн НӨАТ баримтыг (шинээр) үүсгэнэ">
                    <RefreshCw size={12} className={retrying === r.id ? 'animate-spin' : ''} />
                    {retrying === r.id ? '…' : 'Дахин үүсгэх'}
                  </button>
                )}
                {(r.status === 'SENT' || r.status === 'CANCEL_PENDING') && r.ebarimt_id && (
                  <button className="btn-secondary py-1 px-2 text-xs text-red-400" disabled={cancelling === r.id}
                    onClick={() => cancel(r)}
                    title={r.status === 'CANCEL_PENDING' ? 'msgbill дээр хүлээгдэж буй цуцлалтын төлөвийг шалгана' : 'Баримтыг ТЕГ-т буцааж (цуцалж) CANCELLED болгоно — мөнгө буцаахгүй'}>
                    <Ban size={12} /> {cancelling === r.id ? '…' : r.status === 'CANCEL_PENDING' ? 'Шалгах' : 'Цуцлах'}
                  </button>
                )}
              </div>
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
