// Төлбөргүй гаргах — ШАЛТГААНЫГ жагсаалтаас сонгоно.
// Өмнө нь `confirm()` дээр «Кассын гараар гаргалт» гэсэн ТОГТМОЛ текст
// бичигддэг байсан тул «хэн, ямар шалтгаанаар хэдэн удаа үнэгүй гаргасан»
// гэдгийг тоолох боломжгүй байв — мөр болгон ижил харагдана.
import { useEffect, useState } from 'react'
import { api, fmt } from '../../api'
import { Field, Modal } from '../../components/ui'

export default function FreeExitModal({ open, session, fee, busy, onClose, onConfirm }) {
  const [reasons, setReasons] = useState([])
  const [code, setCode] = useState('')
  const [note, setNote] = useState('')
  const [createComp, setCreateComp] = useState(false)

  useEffect(() => {
    if (!open) return
    setCode(''); setNote(''); setCreateComp(false)
    api('/api/admin/open-reasons?active_only=true').then(setReasons).catch(() => setReasons([]))
  }, [open])

  if (!session) return null
  const paid = !fee?.is_free && Number(fee?.total_fee || 0) > 0
  // «Бусад» сонгосон үед тайлбар ЗААВАЛ — эс бол тайлан дээр утгагүй бүлэг үүснэ
  const needNote = code === 'other'
  const ready = !!code && (!needNote || note.trim().length >= 3)

  return (
    <Modal open={open} title="Төлбөргүй гаргах" onClose={onClose}>
      <div className="space-y-4">
        <div className="text-sm text-slate-300">
          <span className="font-mono font-bold text-lg">{session.plate_number}</span>
          {paid && <span className="ml-2 text-amber-400">төлбөр {fmt(fee?.total_fee)}₮</span>}
        </div>

        <Field label="Шалтгаан" required>
          <select className="input" value={code} onChange={(e) => setCode(e.target.value)}
            autoFocus aria-label="Шалтгаан сонгох">
            <option value="">— сонгоно уу —</option>
            {reasons.map((r) => <option key={r.code} value={r.code}>{r.label}</option>)}
          </select>
          <span className="block text-[11px] text-slate-500 mt-1">
            Шалтгаан бүр тайланд тоологдоно. Жагсаалтыг Тохиргоо → Нээх шалтгаанаас удирдана.
          </span>
        </Field>

        <Field label={needNote ? 'Тайлбар (заавал)' : 'Тайлбар (заавал биш)'}>
          <input className="input" value={note} maxLength={200}
            placeholder={needNote ? 'Юу болсныг товч бичнэ үү' : ''}
            onChange={(e) => setNote(e.target.value)} />
        </Field>

        {paid && (
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={createComp}
              onChange={(e) => setCreateComp(e.target.checked)} />
            <span>Нөхөн төлбөрийн нэхэмжлэл ({fmt(fee?.total_fee)}₮) үүсгэх
              <span className="block text-xs text-slate-500">
                Дараагийн ирэлтэд нэхэмжилнэ. Тэмдэглэхгүй бол өргүй гаргана.
              </span>
            </span>
          </label>
        )}

        <div className="flex gap-2">
          <button className="btn-primary" disabled={!ready || busy}
            onClick={() => onConfirm({ reason_code: code, reason: note.trim(), create_compensation: createComp })}>
            {busy ? 'Гаргаж байна…' : 'Гаргах'}
          </button>
          <button className="btn-secondary" onClick={onClose}>Болих</button>
        </div>
      </div>
    </Modal>
  )
}
