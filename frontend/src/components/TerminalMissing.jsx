// ТЕГ тулгалт — «POS терминал гаргасан» гэж бүртгэсэн ч ТЕГ-ийн экспортод баримт
// АЛГА төлбөрүүд.
//
// Яагаад (2026-10-13 хяналт #3): «POS терминал өөрөө НӨАТ баримт гаргадаг» зогсоолд
// систем баримт үүсгэхгүй (EXTERNAL) — тэр мөр «манайх»-аас хасагддаг тул терминал
// баримт ХЭВЛЭЭГҮЙ (унтарсан, цаасгүй, Ontime салгасан) үед борлуулалт огт баримтгүй
// үлдсэнийг юу ч харуулдаггүй байв. Энд тэдгээрийг жагсааж, баталгаажсан бол
// «Систем баримт гаргах»-аар msgbill-ээр нөхнө (2026-10-14: дүрэм ерөнхийдөө асаалттай
// тул тохиргоо унтраахгүй — «терминал хэвлээгүй» баталгаажуулалтыг (no_terminal_receipt)
// илгээнэ; олноор нь бол tools/vat_reissue_missing.py).
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { api, fmt, fmtDate } from '../api'

export default function TerminalMissing({ recon, toast }) {
  const [busy, setBusy] = useState(null)
  const [done, setDone] = useState({})
  const rows = recon?.terminal_missing || []
  if (!rows.length) return null

  const reissue = async (r) => {
    if (!window.confirm(`${r.plate || ''} — ${fmt(r.amount)}₮\n`
      + 'POS терминал баримт гаргасан гэж бүртгэгдсэн ч ТЕГ-ийн файлд баримт алга.\n'
      + 'Терминал баримт ГАРГААГҮЙ нь батлагдсан уу? Систем msgbill-ээр шинэ баримт гаргана.\n\n'
      + 'Анхаар: терминал баримт хэвлэсэн бол ТЕГ-т нэг борлуулалт 2 баримттай болно.')) return
    setBusy(r.payment_id)
    try {
      const res = await api(`/api/payments/${r.payment_id}/retry-ebarimt`,
        { method: 'POST', body: { reissue_external: true, no_terminal_receipt: true } })
      setDone((d) => ({ ...d, [r.payment_id]: res.error || 'Баримтын ажил хадгалагдлаа' }))
      toast(res.error || 'Баримтын ажил хадгалагдлаа', res.pending ? 'success' : 'error')
    } catch (e) { toast(e.message, 'error') } finally { setBusy(null) }
  }

  return (
    <div className="card border-amber-500/40 bg-amber-500/5 space-y-2" role="alert">
      <div className="flex items-center gap-2 text-amber-300 text-sm font-semibold">
        <AlertTriangle size={14} /> POS терминал гаргасан гэж бүртгэсэн ч ТЕГ-т баримт алга:
        <b className="font-mono">{recon.terminal_missing_total}</b>
      </div>
      <p className="text-xs text-slate-400">
        Эдгээр төлбөрт систем баримт гаргаагүй (терминал гаргана гэж үзсэн), гэвч файлын
        хугацаанд ижил цаг, дүнтэй терминалын баримт ТЕГ-т олдсонгүй — терминал хэвлээгүй
        бол борлуулалт баримтгүй үлдсэн. Терминалын бүртгэлээс шалгаад нөхнө үү.
      </p>
      <div className="max-h-64 overflow-auto text-xs font-mono space-y-1">
        {rows.map((r) => (
          <div key={r.payment_id || r.paid_at} className="flex flex-wrap items-center gap-2">
            <span>{fmtDate(r.paid_at)} {r.plate || '—'} {fmt(r.amount)}₮ {r.site_name || ''}</span>
            {done[r.payment_id] ? (
              <span className="text-slate-400">{done[r.payment_id]}</span>
            ) : r.payment_id && (
              <button type="button" className="btn-secondary py-0.5 px-2 text-[11px]"
                disabled={busy === r.payment_id} onClick={() => reissue(r)}
                title="Терминал баримт ГАРГААГҮЙ нь батлагдсан үед л">
                <RefreshCw size={11} className={busy === r.payment_id ? 'animate-spin' : ''} />
                {busy === r.payment_id ? '…' : 'Систем баримт гаргах'}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
