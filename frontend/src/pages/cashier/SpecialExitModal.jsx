// Онцгой гаргалт — free_exit ЭРХГҮЙ оператор/POS-ийн явцуу урсгал (2026-09-14).
// Шийдвэр: операторт «төлбөргүй гаргах» эрх нээхгүй. Харин ХБИ, түргэн/цагдаа,
// орох уншилтгүй (бүртгэлгүй) машин, төлөөд хаалт нээгдээгүй машиныг зогсоол
// дээр шийдэх ёстой тул: товч дармагц ГАРАХ ХААЛТНЫ дугаар уншигч камераас
// ОДООГИЙН зургийг гараар авч харуулна → оператор машиныг нүдээр баталгаажуулж
// «Гаргах» дарна. Зураг session-д (verify) хадгалагдаж Түүх/аудитад үлдэнэ.
import { Camera, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api, fmt } from '../../api'
import { SnapshotImg } from '../../components/Snapshot'
import { Field, Modal } from '../../components/ui'

export const SPECIAL_KINDS = {
  paid_no_open: { label: 'Төлөөд нээгдээгүй', hint: 'Төлбөр төлөгдсөн ч хаалт нээгдээгүй — хаалтыг дахин нээнэ', needsSnapshot: false },
  hbi: { label: 'ХБИ', hint: 'Хөгжлийн бэрхшээлтэй иргэний машин — төлбөргүй гаргана', needsSnapshot: true },
  emergency: { label: 'Түргэн / Цагдаа', hint: 'Онцгой байдал (түргэн, цагдаа, гал) — төлбөргүй гаргана', needsSnapshot: true },
  no_session: { label: 'Бүртгэлгүй', hint: 'Орох уншилтгүй (бүртгэлгүй) машин — суурь хураамжгүй гаргана', needsSnapshot: true },
}

export default function SpecialExitModal({ kind, session, fee, busy, onClose, onConfirm }) {
  const [snap, setSnap] = useState(null)      // {camera, taken_at, path}
  const [snapErr, setSnapErr] = useState('')
  const [taking, setTaking] = useState(false)
  const [note, setNote] = useState('')
  const [tick, setTick] = useState(0)          // зураг дахин авахад SnapshotImg-ийг сэргээнэ

  const spec = kind ? SPECIAL_KINDS[kind] : null

  const takeSnapshot = async () => {
    if (!session) return
    setTaking(true); setSnapErr('')
    try {
      const r = await api(`/api/sessions/${session.id}/special-exit/snapshot`, { method: 'POST', body: {} })
      setSnap(r); setTick((t) => t + 1)
    } catch (e) {
      setSnapErr(e.message || 'Камераас зураг авч чадсангүй')
    } finally { setTaking(false) }
  }

  // Модал нээгдмэгц камераас зураг авна — оператор нэмэлт товч дарахгүй
  useEffect(() => {
    if (!kind || !session) return
    setSnap(null); setSnapErr(''); setNote('')
    takeSnapshot()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, session?.id])

  if (!kind || !session || !spec) return null
  const unpaid = !fee?.is_free && Number(fee?.total_fee || 0) > 0 && !session.paid_at
  // Зураг ЗААВАЛ БИШ (2026-09-14): камер зураг өгөхгүй үед ч оператор гаргана —
  // зураггүй гаргалт аудитад тусдаа тэмдэглэгдэнэ. Зураг авч байх хооронд л хүлээнэ.
  const ready = true
  const noSnap = !snap && spec.needsSnapshot

  return (
    <Modal open={!!kind} title={`${spec.label} — гаргах`} onClose={onClose} wide>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3 text-sm text-slate-300">
          <span className="font-mono font-bold text-lg">{session.plate_number}</span>
          {kind !== 'paid_no_open' && unpaid && (
            <span className="text-amber-400">төлбөр {fmt(fee?.total_fee)}₮ — төлбөргүй гарна</span>
          )}
          {session.exit_device_name && (
            <span className="text-xs text-slate-500">гарах камер: {session.exit_device_name}</span>
          )}
        </div>
        <div className="text-xs text-slate-400">{spec.hint}</div>

        {/* Орох зураг (event) ба ОДООГИЙН гарах камерын зураг — нэг машин мөн эсэхийг харна */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <SnapshotImg sessionId={session.id} kind="entry" label="Орох зураг (камерын event)"
            eventTime={session.entry_time} />
          <div>
            <div className="label mb-1 flex items-center justify-between">
              <span>Гарах камер — одоо {snap?.camera ? `(${snap.camera})` : ''}</span>
              <button type="button" className="text-xs text-slate-400 hover:text-accent flex items-center gap-1"
                onClick={takeSnapshot} disabled={taking}>
                <RefreshCw size={12} className={taking ? 'animate-spin' : ''} /> дахин авах
              </button>
            </div>
            {taking && !snap ? (
              <div className="rounded-lg bg-surface-muted h-44 flex items-center justify-center text-xs text-slate-400 gap-2">
                <Camera size={16} className="animate-pulse" /> Камераас зураг авч байна…
              </div>
            ) : snap ? (
              <SnapshotImg key={tick} sessionId={session.id} kind="verify" label="" />
            ) : (
              <div className="rounded-lg bg-red-500/10 border border-red-500/40 h-44 flex flex-col items-center justify-center text-xs text-red-300 gap-2 px-3 text-center">
                <span>{snapErr || 'Зураг аваагүй'}</span>
                <button type="button" className="btn-secondary py-1 px-2 text-xs" onClick={takeSnapshot} disabled={taking}>
                  Дахин оролдох
                </button>
                {spec.needsSnapshot && (
                  <span className="text-slate-400">Зураггүй ч гаргаж болно — аудитад «зураггүй» гэж тэмдэглэгдэнэ</span>
                )}
              </div>
            )}
          </div>
        </div>

        <Field label="Тайлбар (заавал биш)">
          <input className="input" value={note} maxLength={200}
            placeholder={kind === 'emergency' ? 'жишээ: түргэний 103 машин' : ''}
            onChange={(e) => setNote(e.target.value)} />
        </Field>

        <div className="flex gap-2">
          <button className="btn-primary" disabled={!ready || busy || taking}
            onClick={() => onConfirm({ kind, note: note.trim() })}>
            {busy ? 'Гаргаж байна…' : (kind === 'paid_no_open' ? 'Хаалт нээх' : (noSnap ? 'Зураггүй гаргах' : 'Баталгаажуулж гаргах'))}
          </button>
          <button className="btn-secondary" onClick={onClose}>Болих</button>
        </div>
      </div>
    </Modal>
  )
}
