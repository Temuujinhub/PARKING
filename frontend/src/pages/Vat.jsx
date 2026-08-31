// Ибаримт — НӨАТ баримтын жагсаалт + ТЕГ мэдээ илгээлт
import { AlertTriangle, Ban, QrCode, RefreshCw, Search, Send, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt, fmtDate } from '../api'
import { useFetch } from '../hooks/useFetch'
import { Badge, DateRange, Modal, Table, useToast } from '../components/ui'
import VatRecon from '../components/VatRecon'
import { toDateInput } from '../validation'

const TABS = [['receipts', 'Баримт'], ['recon', 'ТЕГ тулгалт']]

export default function Vat() {
  // ЛОКАЛ огноо (toISOString нь UTC — УБ-д шөнө 00:00–08:00-д «өчигдөр» гаргадаг байв)
  const today = toDateInput()
  const monthAgo = toDateInput(new Date(Date.now() - 30 * 864e5))
  const [from, setFrom] = useState(monthAgo)
  const [to, setTo] = useState(today)
  const [qrReceipt, setQrReceipt] = useState(null)
  const [retrying, setRetrying] = useState(null)
  const [cancelling, setCancelling] = useState(null)
  const [tab, setTab] = useState('receipts')
  const [q, setQ] = useState('')          // хайлтын талбар (бичиж байгаа)
  const [term, setTerm] = useState('')    // сервер рүү илгээсэн (debounce-той)

  const [sending, setSending] = useState(false)
  const toast = useToast()

  // Бичиж дуустал хүлээгээд (350мс) сервер рүү нэг л удаа хайна
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 350)
    return () => clearTimeout(t)
  }, [q])

  const { data: rows, loading, reload: reloadRows } = useFetch(
    `/api/reports/vat-receipts?date_from=${from}&date_to=${to}${term ? `&q=${encodeURIComponent(term)}` : ''}`,
    { initial: [] })
  const { data: info, reload: reloadInfo } = useFetch('/api/reports/vat-info', { initial: null })
  // Бүтэлгүйтлийг ШАЛТГААНААР бүлэглэсэн нэгтгэл. Мөр тус бүрийн алдаа доорх
  // хүснэгтэд харагддаг ч 500+ ИЖИЛ алдаа хуудаслалттай жагсаалтад хэв маяг
  // болж харагддаггүй — прод дээр ийм хоёр тасалдал 24-48 цаг анзаарагдаагүй
  // (msgbill квот 85ш, QPay «ТТД бүртгэлгүй» 588ш). 2026-08-28.
  const { data: fails, reload: reloadFails } = useFetch('/api/reports/vat-failures?days=7', { initial: [] })
  const [bulking, setBulking] = useState(false)

  // Бүтэлгүйтсэн баримтуудыг БӨӨНӨӨР нөхөх. Нэг гадны шалтгаанаар олон зуун
  // баримт зэрэг унадаг тул нэг нэгээр дарж нөхөх боломжгүй. Төлбөрийг ДАХИН
  // АВАХГҮЙ — зөвхөн ДДТД үүсгэнэ.
  const retryAll = async () => {
    setBulking(true)
    try {
      const pre = await api('/api/reports/vat-retry-failed', {
        method: 'POST', body: { days: 7, limit: 500, dry: true } })
      if (!pre.candidates) { toast('Нөхөх баримт олдсонгүй'); return }
      if (!window.confirm(`${pre.candidates} төлбөрийн ДДТД-г дахин үүсгэх үү?\n\n`
        + 'Төлбөрийг ДАХИН АВАХГҮЙ — зөвхөн баримт үүснэ. ДДТД аль хэдийн үүссэн '
        + 'баримтыг алгасна. Квот дүүрвэл тэр дороо зогсоно.\n\n'
        + 'Гадны шалтгааныг (квот/ТТД бүртгэл) ЗАССАН эсэхээ эхлээд шалгаарай — '
        + 'эс бол бүгд дахин унана.')) return
      const r = await api('/api/reports/vat-retry-failed', {
        method: 'POST', body: { days: 7, limit: 500 } })
      const top = Object.entries(r.errors || {}).sort((a, b) => b[1] - a[1])[0]
      toast(`${r.ok} баримт үүсэв · ${r.failed} унав · ${r.skipped} алгасав`
        + (r.stopped ? ` — ${r.stopped}` : top ? ` · «${top[0].slice(0, 70)}»` : ''),
        r.ok ? 'success' : 'error')
      reloadFails(); reloadRows(); reloadInfo()
    } catch (e) { toast(e.message || 'Бөөнөөр нөхөхөд алдаа гарлаа', 'error') } finally { setBulking(false) }
  }

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
          {tab === 'receipts' && <DateRange from={from} to={to} setFrom={setFrom} setTo={setTo} />}
          {tab === 'receipts' && info?.channels?.posapi && (
            <button className="btn-primary" onClick={sendData} disabled={sending}
              title="Локал PosAPI-д цугларсан баримтуудыг ТЕГ-ын нэгдсэн системд илгээнэ (msgbill/QPay баримт өөрсдөө илгээгддэг)">
              <Send size={15} /> {sending ? 'Илгээж байна…' : 'Мэдээ илгээх'}
            </button>
          )}
        </div>
      </div>

      <div className="flex gap-1 border-b border-surface-border/60 overflow-x-auto" role="tablist">
        {TABS.map(([v, l]) => (
          <button key={v} role="tab" aria-selected={tab === v} onClick={() => setTab(v)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer
              ${tab === v ? 'border-accent text-accent' : 'border-transparent text-slate-400 hover:text-slate-200'}`}>
            {l}
          </button>
        ))}
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
          {/* Суваг бүрийн бодит байдал — өмнө нь локал PosAPI-ийн MOCK badge
              бүх баримтыг хуурамч мэт харагдуулж төөрөгдүүлдэг байв */}
          <span>QPay QR: {info.channels?.qpay
            ? <b className="text-accent">бодит</b> : <b className="text-amber-400">mock</b>}</span>
          <span>msgbill.mn: {info.channels?.msgbill
            ? <b className="text-accent">холбогдсон</b> : <b className="text-amber-400">түлхүүргүй</b>}</span>
          {info.channels?.posapi && (<>
            <span>Сугалааны үлдэгдэл: <b className="font-mono text-slate-200">{fmt(info.leftLotteries)}</b></span>
            <span>Илгээгдээгүй: <b className="font-mono text-slate-200">{fmt(info.unsentCount)}</b></span>
            <span>Сүүлд илгээсэн: <b className="font-mono text-slate-200">{info.lastSentDate || '-'}</b></span>
          </>)}
          {info.channels?.mock_receipts && <span className="text-amber-400 text-xs font-semibold">MOCK баримт асаалттай!</span>}
        </div>
      )}
      {tab === 'recon' && <VatRecon tenants={info?.tenants || []} />}

      {tab === 'receipts' && fails.length > 0 && (
        <div className="card space-y-2">
          <div className="flex items-center gap-2">
            <AlertTriangle size={15} className="text-amber-400" />
            <h3 className="font-semibold text-slate-200">Бүтэлгүйтсэн баримт — шалтгаанаар (сүүлийн 7 хоног)</h3>
            <div className="ml-auto flex gap-1.5">
              <button className="btn-primary py-0.5 text-xs" disabled={bulking} onClick={retryAll}
                title="Бүх бүтэлгүйтсэн баримтын ДДТД-г дахин үүсгэнэ. Төлбөрийг ДАХИН АВАХГҮЙ. Шалтгааныг зассаны ДАРАА дарна уу.">
                {bulking ? 'Үүсгэж байна…' : 'Бүгдийг дахин үүсгэх'}
              </button>
              <button className="btn-secondary py-0.5 text-xs" onClick={reloadFails}>Шинэчлэх</button>
            </div>
          </div>
          <p className="text-xs text-slate-500">
            Нэг шалтгаан олон зуун баримтыг зогсоож болно. Дүн нь ДДТД ҮҮСЭЭГҮЙ гүйлгээний нийлбэр —
            шалтгааныг зассаны дараа доорх жагсаалтаас «Дахин үүсгэх»-ээр нөхнө.
          </p>
          <Table headers={['Суваг', 'Тоо', 'Дүн', 'Эхэлсэн', 'Сүүлийн', 'Алдаа']} empty={false}>
            {fails.map((f, i) => (
              <tr key={i}>
                <td className="td text-xs font-medium">{f.provider}</td>
                <td className="td font-mono text-right">{fmt(f.count)}</td>
                <td className="td font-mono text-right whitespace-nowrap">{fmt(f.amount)}₮</td>
                <td className="td text-xs whitespace-nowrap">{fmtDate(f.first_at)}</td>
                <td className="td text-xs whitespace-nowrap">{fmtDate(f.last_at)}</td>
                <td className="td text-[11px] text-amber-400 break-words max-w-[28rem]">{f.error}</td>
              </tr>
            ))}
          </Table>
        </div>
      )}
      {tab === 'receipts' && (<>
      <div className="card flex flex-wrap gap-2 py-3 items-center">
        <div className="relative flex-1 min-w-56">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input className="input pl-9 pr-9" value={q} onChange={(e) => setQ(e.target.value)}
            aria-label="Баримт хайх"
            placeholder="Дугаар, ДДТД, сугалаа, зогсоол, суваг, төлөв, дүнгээр хайх…" />
          {q && (
            <button className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-200"
              onClick={() => setQ('')} aria-label="Хайлт цэвэрлэх"><X size={15} /></button>
          )}
        </div>
        <span className="text-xs text-slate-500">
          {loading ? 'Хайж байна…' : `${rows.length} баримт${term ? ` · «${term}»` : ''}`}
        </span>
      </div>

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
              {r.provider && <div className="text-[10px] text-slate-500 mt-0.5">{r.provider === 'MSGBILL' ? 'msgbill.mn' : r.provider === 'QPAY' ? 'QPay' : r.provider === 'TERMINAL' ? 'POS терминал' : r.provider === 'POSAPI' ? 'PosAPI' : r.provider}</div>}
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
      </>)}

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
