// Төлбөрийн дэлгэрэнгүй — дугаар хайх, сонгосон машины тооцоо, төлбөр авах
import { Banknote, DoorOpen, Landmark, QrCode, Search } from 'lucide-react'
import { api, fmt, fmtDate, fmtDur } from '../../api'
import { SnapshotImg } from '../../components/Snapshot'
import { Badge, Field, useToast } from '../../components/ui'

export default function PaymentPanel({
  selected, setSelected, fee, canAct, canFreeExit, busy, discounts,
  canTransfer, showCash, site,
  searchPlate, searchResults, onSearchChange, onSearch, onPickResult,
  onPay, onApplyDiscount, onManualExit, onSaveNote, siteId, loadExits,
}) {
  const toast = useToast()
  return (
    <div className="card">
      <h2 className="font-semibold mb-3">Төлбөр авах</h2>
      <div className="flex gap-2 mb-4">
        <input className="input font-mono" placeholder="Дугаар хайх… эхний тоо хангалттай (00…)" value={searchPlate}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSearch()} aria-label="Улсын дугаар хайх" />
        <button onClick={() => onSearch()} className="btn-secondary" aria-label="Хайх"><Search size={16} /></button>
      </div>
      {searchResults && (
        <div className="mb-4 space-y-1.5" aria-live="polite">
          {searchResults.length === 0 && <div className="text-sm text-slate-500">Нээлттэй бүртгэл олдсонгүй</div>}
          {searchResults.map((s) => (
            <button key={s.id} onClick={() => onPickResult(s)}
              className="w-full text-left px-3 py-2.5 rounded-lg bg-surface-muted/40 hover:bg-surface-muted border border-surface-border/50 hover:border-accent text-sm cursor-pointer flex items-center justify-between transition-colors">
              <span className="font-mono font-bold text-base">{s.plate_number}</span>
              <span className="flex items-center gap-3">
                <span className="font-mono text-amber-400">{s.fee?.is_free ? 'Үнэгүй' : `${fmt(s.fee?.total_fee ?? s.total_fee)}₮`}</span>
                <Badge value={s.status} />
              </span>
            </button>
          ))}
        </div>
      )}

      {selected ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="font-mono text-2xl font-bold flex items-center gap-2">
              {selected.plate_number}
              <button className="text-xs font-sans font-normal text-slate-500 hover:text-accent underline cursor-pointer"
                title="Камер алдаатай уншсан бол дугаарыг засна"
                onClick={async () => {
                  const np = prompt(`Дугаар засах (одоо: ${selected.plate_number}).\nЗөв формат: 4 тоо + 3 кирилл үсэг`, selected.plate_number)
                  if (!np || np === selected.plate_number) return
                  try {
                    const updated = await api(`/api/sessions/${selected.id}/plate`, { method: 'PUT', body: { plate_number: np } })
                    setSelected(updated)
                    toast(`Дугаар ${updated.plate_number} болж засагдлаа`)
                    loadExits(siteId)
                  } catch (err) { toast(err.message, 'error') }
                }}>
                засах
              </button>
            </span>
            <Badge value={selected.status} />
          </div>
          <div className="grid grid-cols-2 gap-2 text-sm bg-surface-muted/30 rounded-lg p-3">
            <span className="text-slate-400">Орсон цаг</span><span className="font-mono text-right">{fmtDate(selected.entry_time)}</span>
            <span className="text-slate-400">Хугацаа</span><span className="font-mono text-right">{fmtDur(fee?.duration_minutes)}</span>
            <span className="text-slate-400">Үндсэн дүн</span><span className="font-mono text-right">{fmt(fee?.base_fee)}₮</span>
            <span className="text-slate-400">Хөнгөлөлт</span><span className="font-mono text-right text-cyan-400">-{fmt(fee?.discount_amount)}₮</span>
            <span className="text-slate-400">НӨАТ (10%)</span><span className="font-mono text-right">{fmt(fee?.vat_amount)}₮</span>
            <span className="text-slate-300 font-semibold">Нийт дүн</span>
            <span className="font-mono text-right text-xl font-bold text-accent">{fmt(fee?.total_fee)}₮</span>
            {/* Төлснөөс хойш зогссоор байгаа машин: өмнө төлсөн дүнг хасаад
                зөвхөн ҮЛДЭГДЛИЙГ нэхэмжилнэ (grace хэтэрсэн тохиолдол) */}
            {selected.paid_total > 0 && (
              <>
                <span className="text-slate-400">Өмнө төлсөн</span>
                <span className="font-mono text-right text-emerald-400">-{fmt(selected.paid_total)}₮</span>
                <span className="text-slate-300 font-semibold">Төлөх үлдэгдэл</span>
                <span className="font-mono text-right text-xl font-bold text-amber-400">{fmt(selected.amount_due)}₮</span>
              </>
            )}
          </div>
          {/* Камерын зураг — машин таарч байгааг нүдээр баталгаажуулна */}
          <div className="grid grid-cols-2 gap-2">
            <SnapshotImg sessionId={selected.id} kind="entry" label="Орох зураг" />
            <SnapshotImg sessionId={selected.id} kind="exit" label="Гарах зураг" />
          </div>
          <Field label="Хөнгөлөлт хэрэглэх">
            <select className="input" value={selected.discount_id || ''} onChange={(e) => onApplyDiscount(e.target.value)} disabled={!canAct}>
              <option value="">Хөнгөлөлтгүй</option>
              {discounts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
          </Field>
          {/* Нэмэлт тэмдэглэл */}
          <Field label="Нэмэлт тэмдэглэл">
            <textarea className="input min-h-[60px] resize-y" placeholder="Жишээ: гэрээт машин, тусгай нөхцөл, гомдол…"
              value={selected.note || ''} disabled={!canAct}
              onChange={(e) => setSelected({ ...selected, note: e.target.value })} />
            {canAct && (
              <button type="button" onClick={onSaveNote} className="btn-secondary py-1 text-xs mt-1">Тэмдэглэл хадгалах</button>
            )}
          </Field>
          {/* Дансаар (шилжүүлэг) хүлээн авах данс — жолоочид хэлж өгнө */}
          {canAct && canTransfer && (
            <div className="text-xs bg-surface-muted/40 border border-surface-border/60 rounded-lg px-3 py-2">
              <span className="text-slate-400">Хүлээн авах данс: </span>
              {site?.bank_account
                ? <b className="font-mono">{site.bank_name} {site.bank_account}
                    {site.bank_account_name ? ` · ${site.bank_account_name}` : ''}</b>
                : <span className="text-amber-400">данс тохируулаагүй — Тохиргоо → Зогсоол → Засах</span>}
            </div>
          )}
          {canAct && (
            <div className={`grid gap-2 ${canTransfer && showCash ? 'grid-cols-3' : 'grid-cols-2'}`}>
              {/* Online operator: Бэлнээрийн ОРОНД Дансаар (шилжүүлэг) товч */}
              {canTransfer && (
                <button onClick={() => onPay('TRANSFER')} disabled={busy || fee?.is_free} className="btn-primary justify-center">
                  <Landmark size={16} /> Дансаар
                </button>
              )}
              {showCash && (
                <button onClick={() => onPay('CASH')} disabled={busy || fee?.is_free}
                  className={`${canTransfer ? 'btn-secondary' : 'btn-primary'} justify-center`}>
                  <Banknote size={16} /> Бэлнээр
                </button>
              )}
              <button onClick={() => onPay('QPAY')} disabled={busy || fee?.is_free} className="btn-secondary justify-center">
                <QrCode size={16} /> QPay
              </button>
            </div>
          )}
          {fee?.is_free && (
            <div className="text-sm text-cyan-400 bg-cyan-500/10 rounded-lg px-3 py-2">
              Төлбөргүй: {fee.reason || 'Үнэгүй хугацаанд байна'}
            </div>
          )}
          {canAct && canFreeExit && (
            <button onClick={onManualExit} className="btn-secondary w-full justify-center text-xs">
              <DoorOpen size={14} /> Гараар гаргах (төлбөргүй)
            </button>
          )}
        </div>
      ) : (
        <div className="text-sm text-slate-500 text-center py-10">
          Зүүн талаас машин сонгох эсвэл дугаараар хайна уу
        </div>
      )}
    </div>
  )
}
