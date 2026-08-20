// Ээлж хаах — мөнгөн тооцооны дэлгэц (бэлэн тушаах, үлдсэн машин хаах)
import { fmt } from '../../api'
import { Field, Modal } from '../../components/ui'
import { clampNum } from '../../validation'

export default function ShiftCloseModal({ closeModal, setCloseModal, shift, busy, onConfirm }) {
  return (
    <Modal open={!!closeModal} onClose={() => setCloseModal(null)} title="Ээлж хаах — мөнгөн тооцоо">
      {closeModal && shift?.open && (
        <div className="space-y-4">
          {/* Ээлжийн орлого хэрэгслээр */}
          <div className="bg-surface-muted/30 rounded-lg p-3 space-y-2 text-sm">
            <div className="text-xs text-slate-400 mb-1">Энэ ээлжийн орлого ({shift.count} гүйлгээ):</div>
            <div className="flex justify-between"><span className="text-slate-400">QPay</span>
              <span className="font-mono">{fmt(shift.by_provider?.QPAY?.amount || 0)}₮</span></div>
            <div className="flex justify-between"><span className="text-slate-400">Банкны карт (POS)</span>
              <span className="font-mono">{fmt(shift.by_provider?.POS?.amount || 0)}₮</span></div>
            <div className="flex justify-between"><span className="text-slate-400">Дансаар (шилжүүлэг)</span>
              <span className="font-mono">{fmt(shift.by_provider?.TRANSFER?.amount || 0)}₮</span></div>
            <div className="flex justify-between"><span className="text-slate-400">Бэлэн</span>
              <span className="font-mono">{fmt(shift.by_provider?.CASH?.amount || 0)}₮</span></div>
            <div className="flex justify-between border-t border-surface-border/50 pt-1.5 font-semibold">
              <span>Нийт орлого</span><span className="font-mono text-accent">{fmt(shift.total)}₮</span></div>
          </div>

          {/* Данс руу шилжүүлэх бэлэн */}
          <div className="bg-amber-500/5 border border-amber-500/30 rounded-lg p-3 space-y-2">
            <div className="text-sm text-amber-300 font-medium">Данс руу тушаах бэлэн мөнгө</div>
            <div className="text-xs text-slate-400">
              Бэлэн мөнгийг ажлын байранд үлдээхгүй — банкны ATM/салбараар байгууллагын данс руу тушаана.
              Тушаасан дүнгээ баталгаажуулна уу:
            </div>
            <div className="flex items-center gap-2">
              {/* Сөрөг/утгагүй тушаалт мөнгөн тооцоог гажуудуулна — оруулах үедээ шахна */}
              <input className="input font-mono text-lg" type="number" min="0" max="1000000000" step="100"
                value={closeModal.confirmed_cash} aria-label="Тушаах бэлэн"
                onChange={(e) => setCloseModal({
                  ...closeModal,
                  confirmed_cash: e.target.value === '' ? '' : clampNum(e.target.value, { min: 0, max: 1_000_000_000 }),
                })} />
              <span className="text-slate-400">₮</span>
            </div>
            {Math.abs((+closeModal.confirmed_cash || 0) - (shift.by_provider?.CASH?.amount || 0)) > 0 && (
              <div className="text-xs text-red-400">
                Системийн бэлэнтэй зөрүү: {fmt((+closeModal.confirmed_cash || 0) - (shift.by_provider?.CASH?.amount || 0))}₮
              </div>
            )}
          </div>

          {/* Үлдсэн машиныг хаах */}
          {shift.remaining_cars > 0 && (
            <label className="flex items-start gap-2 text-sm cursor-pointer bg-surface-muted/30 rounded-lg p-3">
              <input type="checkbox" checked={closeModal.close_cars} className="mt-0.5"
                onChange={(e) => setCloseModal({ ...closeModal, close_cars: e.target.checked })} />
              <span>Зогсоолд үлдсэн <b className="text-amber-400">{shift.remaining_cars}</b> машиныг бүгдийг гаргаж хаах
                <span className="block text-xs text-slate-500">Төлбөртэй машинд нөхөн төлбөрийн нэхэмжлэл үүснэ (дараагийн ирэлтэд төлүүлнэ).</span></span>
            </label>
          )}

          <Field label="Тэмдэглэл (сонголт)">
            <textarea className="input min-h-[50px]" placeholder="Ээлж хаахтай холбоотой тэмдэглэл…"
              value={closeModal.note} onChange={(e) => setCloseModal({ ...closeModal, note: e.target.value })} />
          </Field>

          <button onClick={onConfirm} disabled={busy} className="btn-primary w-full justify-center py-3">
            {busy ? 'Хааж байна…' : 'Зөвшөөрч ээлж хаах'}
          </button>
        </div>
      )}
    </Modal>
  )
}
