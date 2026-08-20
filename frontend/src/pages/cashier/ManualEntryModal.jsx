// Гараар бүртгэх modal — уншигдалгүй орсон машин (эргүүлийн шалгалт)
import { api } from '../../api'
import { Field, Modal, useToast } from '../../components/ui'
import { PLATE_HINT, isPlate, normalizePlate, toDateTimeInput } from '../../validation'

// Гараар бүртгэх боломжтой хамгийн эртний цаг — 7 хоног.
// Үүнээс хол цаг оруулбал тарифын тооцоо утгагүй том дүн гаргана.
const MAX_BACKDATE_DAYS = 7

// datetime-local талбарт тохирох локал цаг (YYYY-MM-DDTHH:MM)
export const minutesAgo = (mins) => toDateTimeInput(new Date(Date.now() - mins * 60000))

export default function ManualEntryModal({ manualEntry, setManualEntry, siteId }) {
  const toast = useToast()
  const plateValid = manualEntry ? isPlate(manualEntry.plate_number) : false
  // Ирээдүйн цаг бүртгэхийг хориглоно; хамгийн эрт нь MAX_BACKDATE_DAYS хоногийн өмнө
  const nowInput = toDateTimeInput()
  const minInput = toDateTimeInput(new Date(Date.now() - MAX_BACKDATE_DAYS * 86400000))
  const timeErr = !manualEntry?.entry_time ? null
    : manualEntry.entry_time > nowInput ? 'Ирээдүйн цаг оруулах боломжгүй'
      : manualEntry.entry_time < minInput ? `${MAX_BACKDATE_DAYS} хоногоос хэтэрсэн цаг оруулах боломжгүй`
        : null

  const saveManualEntry = async (e) => {
    e.preventDefault()
    if (timeErr) { toast(timeErr, 'error'); return }
    try {
      const body = { site_id: siteId, plate_number: manualEntry.plate_number }
      // Стандарт бус (дипломат/тусгай) дугаарыг оператор баталгаажуулж бүртгэнэ
      if (!plateValid) {
        if (!confirm(`«${manualEntry.plate_number}» стандарт форматад тохирохгүй байна.\nДипломат/тусгай дугаар мөн бол OK дарж бүртгэнэ үү.`)) return
        body.force = true
      }
      // datetime-local нь локал цаг — backend UTC хадгалдаг тул хөрвүүлнэ
      if (manualEntry.entry_time) body.entry_time = new Date(manualEntry.entry_time).toISOString().slice(0, 19)
      const s = await api('/api/sessions/manual-entry', { method: 'POST', body })
      toast(`${s.plate_number} бүртгэгдлээ`)
      setManualEntry(null)
    } catch (err) { toast(err.message, 'error') }
  }

  return (
    <Modal open={!!manualEntry} onClose={() => setManualEntry(null)} title="Машин гараар бүртгэх">
      {manualEntry && (
        <form onSubmit={saveManualEntry} className="space-y-3">
          <div className="text-sm text-slate-400 bg-surface-muted/40 rounded-lg px-3 py-2">
            Орох камерт уншигдалгүй орсон машиныг (эргүүлээр илэрсэн) энд бүртгэнэ.
            Бүртгэсэн цагаас нь төлбөр тооцогдоно.
          </div>
          <Field label="Улсын дугаар (4 орон + 3 кирилл үсэг)" required>
            <input autoFocus required maxLength={7} inputMode="text"
              className={`input font-mono text-xl text-center tracking-widest uppercase border-2
                ${!manualEntry.plate_number ? '' : plateValid ? 'border-accent' : 'border-red-500/70'}`}
              value={manualEntry.plate_number} placeholder="1234УБА" aria-describedby="plate-hint"
              onChange={(e) => setManualEntry({
                ...manualEntry,
                // Зөвхөн тоо + кирилл үсэг үлдээж, урд нь 4 тоо, ард нь 3 үсэг гэсэн дарааллаар шүүнэ
                plate_number: normalizePlate(e.target.value),
              })} />
            <div id="plate-hint" aria-live="polite"
              className={`text-xs mt-1 ${!manualEntry.plate_number ? 'text-slate-500' : plateValid ? 'text-accent' : 'text-red-400'}`}>
              {!manualEntry.plate_number
                ? 'Жишээ: 1234УБА'
                : plateValid
                  ? '✓ Дугаарын формат зөв'
                  : `Формат буруу — ${PLATE_HINT}`}
            </div>
          </Field>
          <Field label="Хэдий хугацааны өмнө орсон бэ?">
            <div className="grid grid-cols-5 gap-1.5 mb-2">
              {[[0, 'Одоо'], [30, '30 мин'], [60, '1 цаг'], [120, '2 цаг'], [180, '3 цаг']].map(([mins, label]) => (
                <button key={mins} type="button"
                  onClick={() => setManualEntry({ ...manualEntry, entry_time: minutesAgo(mins), offset: mins })}
                  className={`px-2 py-2 rounded-lg text-sm font-medium border transition-colors cursor-pointer
                    ${manualEntry.offset === mins
                      ? 'bg-accent text-white border-accent'
                      : 'bg-surface-muted/40 text-slate-300 border-surface-border hover:border-slate-500'}`}>
                  {label}
                </button>
              ))}
            </div>
            <input className={`input${timeErr ? ' input-error' : ''}`} type="datetime-local"
              value={manualEntry.entry_time} aria-label="Орсон цаг гараар засах"
              min={minInput} max={nowInput} aria-invalid={!!timeErr}
              onChange={(e) => setManualEntry({ ...manualEntry, entry_time: e.target.value, offset: -1 })} />
            <div className={timeErr ? 'hint-error' : 'hint'} aria-live="polite">
              {timeErr || `3 цагаас дээш бол дээрх талбараас гараар засна (${MAX_BACKDATE_DAYS} хоног хүртэл).`}
            </div>
          </Field>
          <button className="btn-primary w-full justify-center" disabled={!manualEntry.plate_number || !!timeErr}>
            {plateValid ? 'Бүртгэх' : 'Тусгай дугаараар бүртгэх'}
          </button>
        </form>
      )}
    </Modal>
  )
}
