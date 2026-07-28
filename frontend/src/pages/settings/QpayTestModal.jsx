// QPay дансны туршилт — машин орох шаардлагагүйгээр түрээслэгчийн данс/e-Barimt-ыг шалгана.
// Урсгал: жижиг дүнгээр БОДИТ нэхэмжлэл → QR → төлнө → check → e-Barimt-ын ДДТД/ТТД харуулна.
import { useEffect, useState } from 'react'
import { api, fmt } from '../../api'
import { Field, Modal, useToast } from '../../components/ui'

export default function QpayTestModal({ state, onClose }) {
  const toast = useToast()
  const [amount, setAmount] = useState(10)
  const [inv, setInv] = useState(null)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const site = state?.site

  useEffect(() => { setInv(null); setResult(null); setAmount(10) }, [state])

  const start = async () => {
    setBusy(true)
    try {
      const r = await api(`/api/admin/sites/${site.id}/qpay-test`, {
        method: 'POST', body: { amount: +amount },
      })
      setInv(r); setResult(null)
      if (!r.using_own_account) {
        toast('Анхаар: энэ зогсоол өөрийн данстай биш — системийн ерөнхий данс руу төлөгдөнө', 'error')
      }
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const check = async () => {
    setBusy(true)
    try {
      const r = await api(`/api/admin/sites/${site.id}/qpay-test/check`, {
        method: 'POST', body: { invoice_id: inv.invoice_id },
      })
      setResult(r)
      if (!r.paid) toast('Төлбөр хараахан ороогүй байна — төлөөд дахин шалгана уу')
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  return (
    <Modal open={!!state} onClose={onClose} title={`${site?.name || ''} — QPay дансны туршилт`}>
      <div className="space-y-3 text-sm">
        {!inv && (
          <>
            <div className="text-slate-400">
              Энэ зогсоолын QPay дансаар <b className="text-slate-200">бодит</b> туршилтын
              нэхэмжлэл үүсгэнэ. Машин орох шаардлагагүй. Төлсөн дүн тухайн дансанд
              бодитоор орно — жижиг дүн ашиглана уу.
            </div>
            <Field label="Дүн (₮)">
              <input className="input" type="number" min="1" max="10000" value={amount}
                onChange={(e) => setAmount(e.target.value)} />
            </Field>
            <button className="btn-primary w-full justify-center" disabled={busy} onClick={start}>
              {busy ? 'Үүсгэж байна…' : 'Нэхэмжлэл үүсгэх'}
            </button>
          </>
        )}

        {inv && (
          <>
            {!inv.using_own_account && (
              <div className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-xs text-amber-300">
                <b>Анхаар: энэ зогсоол өөрийн QPay дансгүй байна.</b> Төлбөр системийн
                ерөнхий данс ({inv.merchant}) руу орж, e-Barimt мөн түүний ТТД-ээр үүснэ.
                Түрээслэгчийн данс руу орох ёстой бол цонхыг хааж, «Төлбөрийн данс (QPay)»
                хэсгээс «Энэ зогсоолын өөрийн данс»-ыг сонгож нэр/нууц үгийг бөглөөд
                ЗААВАЛ «Хадгалах» дараад дахин туршина уу.
              </div>
            )}
            <div className="rounded-lg bg-surface-muted/50 p-3 space-y-1 text-xs">
              <div>Мерчант: <b className="font-mono text-slate-200">{inv.merchant}</b>
                {inv.using_own_account
                  ? <span className="text-accent ml-2">· энэ зогсоолын өөрийн данс</span>
                  : <span className="text-amber-400 ml-2">· системийн ерөнхий данс!</span>}
              </div>
              <div>Нэхэмжлэхийн код: <span className="font-mono">{inv.invoice_code}</span></div>
              <div>Дүүрэг: <span className="font-mono">{inv.district_code || '—'}</span></div>
              <div>Дүн: <b>{fmt(inv.amount)}₮</b></div>
            </div>

            {inv.qr_image
              ? <img className="mx-auto rounded-xl bg-white p-3 w-56 h-56"
                  src={`data:image/png;base64,${inv.qr_image}`} alt="QPay QR" />
              : <div className="text-xs break-all font-mono bg-surface-muted/50 p-2 rounded">{inv.qr_text}</div>}

            {inv.deep_link && (
              <a className="btn-secondary w-full justify-center" href={inv.deep_link}>
                Банкны апп-аар нээх
              </a>
            )}

            <button className="btn-primary w-full justify-center" disabled={busy} onClick={check}>
              {busy ? 'Шалгаж байна…' : 'Төлөгдсөн эсэхийг шалгах'}
            </button>

            {result && result.paid && (
              <div className="rounded-lg border border-accent/40 bg-accent/5 p-3 space-y-1 text-xs">
                <div className="text-accent font-medium">Төлбөр амжилттай — {fmt(result.paid_amount)}₮</div>
                {result.ebarimt_ok ? (
                  <>
                    <div>ДДТД: <span className="font-mono break-all">{result.ebarimt_id}</span></div>
                    <div>Сугалаа: <span className="font-mono">{result.lottery || '—'}</span></div>
                    <div>Баримт олгосон ТТД: <b className="font-mono text-slate-200">
                      {result.merchant_register || '—'}</b></div>
                    <div className="text-slate-400 pt-1">
                      Энэ ТТД нь түрээслэгчийнх байвал данс зөв холбогдсон.
                    </div>
                  </>
                ) : (
                  <div className="text-amber-400">
                    Төлбөр орсон ч e-Barimt үүсээгүй: {result.ebarimt_error || '—'}
                  </div>
                )}
              </div>
            )}
            {result && !result.paid && (
              <div className="text-xs text-slate-400">
                Хараахан төлөгдөөгүй. QR-аа уншуулж төлөөд дахин шалгана уу.
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}
