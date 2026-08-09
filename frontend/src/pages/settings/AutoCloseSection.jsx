// Тохиргоо → Авто цэвэрлэгээ: зогсоолд гацсан бүртгэлийг ХЭЗЭЭ, ЯАЖ хаах дүрэм.
// Өмнө нь эдгээр нь .env-д хатуу бичигдсэн байсан тул өөрчлөхөд deploy шаарддаг байв.
import { Eraser, PlayCircle, Save } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { Field, useToast } from '../../components/ui'

// Дүрэм бүрийн тайлбар — ЯМАР тохиолдолд ажилладгийг оператор ойлгохоор
const RULES = [
  ['invalid_plate_hours', 'Формат буруу (junk) дугаар',
   'Камер дутуу уншсан («4132» гэх мэт) бүртгэл — ийм машин гарахдаа хэзээ ч таарахгүй тул хурдан цэвэрлэнэ. ӨР ҮҮСГЭХГҮЙ, үнэгүй хаана.'],
  ['awaiting_hours', 'Гарах хаалтад уншигдсан ч төлөөгүй',
   'Гарах камерт уншигдсан хэрнээ N цаг хөдөлгөөнгүй бол дагаж гарсан гэж үзнэ. Төлбөр нь сүүлд харагдсан үеийн дүнгээр царцаж ӨР болно.'],
  ['entry_only_free_hours', 'Зөвхөн орох уншилттай',
   'Гарах камерт огт уншигдаагүй бүртгэл — гарах уншилт алдагдсан байх магадлалтай тул ӨРГҮЙГЭЭР үнэгүй хаана.'],
  ['stale_hours', 'Ерөнхий хугацаа хэтэрсэн',
   'Дээрхэд хамаарахгүй бүх гацсан бүртгэл. Зогсоол бүрд өөрөөр («Зогсоол» табын засах цонхны «Гацсан машины авто хаалт») тохируулж болно.'],
]

// Камерын лог нөхөлт — камер уншсан ч серверт бүртгэгдээгүй машиныг нөхнө.
// WATERMARK-аар ажилладаг тул нэг event ХОЁР УДАА боловсруулагдахгүй (өмнө нь
// 48ц-ийн логийг бүхлээр нь дахин уншиж давхар өр үүсгэсэн).
function CamSyncCard({ toast }) {
  const [rules, setRules] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = () => api('/api/admin/camsync/rules').then(setRules).catch(() => {})
  useEffect(() => { load() }, [])

  const save = async () => {
    setBusy(true)
    try {
      const { watermarks, ...body } = rules
      await api('/api/admin/camsync/rules', { method: 'PUT', body })
      toast('Хадгалагдлаа'); load()
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const run = async (dryRun) => {
    setBusy(true)
    try {
      const r = await api('/api/admin/camsync/run', { method: 'POST', body: { dry_run: dryRun } })
      const total = r.rows.reduce((a, x) => a + (x.created || 0), 0)
      toast(dryRun
        ? `Урьдчилан харах: ${total} бүртгэл нэмэгдэх байсан`
        : (total ? `${total} бүртгэл нөхөгдлөө` : 'Нөхөх бүртгэл олдсонгүй'))
      if (!dryRun) load()
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  if (!rules) return null
  const set = (k, v) => setRules({ ...rules, [k]: v })

  return (
    <div className="card space-y-4">
      <div>
        <h2 className="font-semibold flex items-center gap-2">
          <Eraser size={16} className="text-accent" /> Камерын лог нөхөлт
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Сервер унтарсан/холболт тасарсан үед камер машиныг уншсан ч бүртгэл үүсдэггүй.
          Энэ ажиллагаа камерын ДОТООД логоос тэдгээрийг олж нөхнө. Сүүлд боловсруулсан
          цагийг тэмдэглэж явдаг тул <b className="text-slate-300">нэг машин хоёр удаа
          бүртгэгдэхгүй</b>.
        </p>
      </div>

      <label className="flex items-start gap-2 text-sm cursor-pointer">
        <input type="checkbox" className="mt-0.5" checked={rules.enabled}
          onChange={(e) => set('enabled', e.target.checked)} />
        <span>Автоматаар нөхөх
          <span className="block text-xs text-slate-500">
            Унтраалттай үед зөвхөн доорх товчоор гараар ажиллуулна.
          </span>
        </span>
      </label>

      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Өдөрт хэдэн удаа">
          <input className="input font-mono" type="number" min="1" max="24"
            value={rules.times_per_day} disabled={!rules.enabled}
            onChange={(e) => set('times_per_day', Number(e.target.value))} />
          <span className="block text-[11px] text-slate-500 mt-1">
            4 = 6 цаг тутам. Тэмдэглэсэн цагаас хойшхийг л шалгана.
          </span>
        </Field>
        <Field label="Хамгийн ихдээ ухрах (цаг)">
          <input className="input font-mono" type="number" min="1" max="72"
            value={rules.lookback_hours} disabled={!rules.enabled}
            onChange={(e) => set('lookback_hours', Number(e.target.value))} />
          <span className="block text-[11px] text-slate-500 mt-1">
            Удаан унтарсан ч үүнээс хол ухрахгүй — хуучин, шийдэгдсэн машиныг
            дахин өр болгохоос сэргийлнэ.
          </span>
        </Field>
        <Field label="Сүүлийн N минутыг хөндөхгүй">
          <input className="input font-mono" type="number" min="0" max="180"
            value={rules.min_age_minutes} disabled={!rules.enabled}
            onChange={(e) => set('min_age_minutes', Number(e.target.value))} />
          <span className="block text-[11px] text-slate-500 mt-1">
            Яг одоо орж яваа машиныг давхар бүртгэхээс сэргийлнэ.
          </span>
        </Field>
        <div className="space-y-2 pt-6">
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={rules.create_debt}
              onChange={(e) => set('create_debt', e.target.checked)} />
            <span>Гарсан машинд <b>өр үүсгэх</b></span>
          </label>
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={rules.skip_invalid_plate}
              onChange={(e) => set('skip_invalid_plate', e.target.checked)} />
            <span>Буруу уншсан дугаарыг <b>алгасах</b></span>
          </label>
        </div>
      </div>

      {rules.watermarks?.length > 0 && (
        <details className="text-xs text-slate-400">
          <summary className="cursor-pointer">Сүүлд шалгасан цаг (зогсоолоор)</summary>
          <div className="mt-1 space-y-0.5 font-mono">
            {rules.watermarks.map((w, i) => (
              <div key={i}>{w.site}: {w.at?.replace('T', ' ').slice(0, 16)} UTC</div>
            ))}
          </div>
        </details>
      )}

      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" onClick={save} disabled={busy}>
          <Save size={15} /> Хадгалах
        </button>
        <button className="btn-secondary" onClick={() => run(true)} disabled={busy}>
          Урьдчилан харах
        </button>
        <button className="btn-secondary" onClick={() => run(false)} disabled={busy}>
          <PlayCircle size={15} /> Яг одоо нөхөх
        </button>
      </div>
    </div>
  )
}

export default function AutoCloseSection() {
  const toast = useToast()
  const [rules, setRules] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/api/admin/autoclose/rules').then(setRules).catch((e) => toast(e.message, 'error'))
  }, [])

  const save = async () => {
    setBusy(true)
    try {
      setRules(await api('/api/admin/autoclose/rules', { method: 'PUT', body: rules }))
      toast('Хадгалагдлаа — дараагийн цэвэрлэгээнээс эхлэн үйлчилнэ')
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const runNow = async () => {
    if (!window.confirm('Авто цэвэрлэгээг яг одоо ажиллуулах уу?\n\nОдоогийн дүрмээр гацсан бүртгэлүүд хаагдаж, зарим нь өр болно.')) return
    setBusy(true)
    try {
      const r = await api('/api/admin/autoclose/run', { method: 'POST' })
      toast(r.closed ? `${r.closed} бүртгэл хаагдлаа` : 'Хаах бүртгэл олдсонгүй')
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  if (!rules) return null
  const set = (k, v) => setRules({ ...rules, [k]: v })

  return (
    <div className="space-y-4">
      <CamSyncCard toast={toast} />
      <div className="card space-y-4">
        <div>
          <h2 className="font-semibold flex items-center gap-2">
            <Eraser size={16} className="text-accent" /> Зогсоолын авто цэвэрлэгээ
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Машин гарсан ч гарах камерт уншигдаагүй, эсвэл төлбөргүй өнгөрсөн үед бүртгэл
            зогсоолд «үлдэж» тоо худал өсдөг. Систем 30 минут тутам эдгээрийг шалгаж
            доорх дүрмээр хаана. <b className="text-slate-300">0 = тэр дүрмийг унтраах.</b>
          </p>
        </div>

        <label className="flex items-start gap-2 text-sm cursor-pointer">
          <input type="checkbox" className="mt-0.5" checked={rules.enabled}
            onChange={(e) => set('enabled', e.target.checked)} />
          <span>Авто цэвэрлэгээ <b>асаах</b>
            <span className="block text-xs text-slate-500">
              Унтраавал гацсан бүртгэл өөрөө хаагдахаа болино — зөвхөн Ээлж хаах/гараар цэвэрлэнэ.
            </span>
          </span>
        </label>

        <div className="grid sm:grid-cols-2 gap-4">
          {RULES.map(([key, label, help]) => (
            <Field key={key} label={`${label} (цаг)`}>
              <input className="input font-mono" type="number" min="0" value={rules[key]}
                disabled={!rules.enabled}
                onChange={(e) => set(key, Number(e.target.value))} />
              <span className="block text-[11px] text-slate-500 mt-1">{help}</span>
            </Field>
          ))}
        </div>

        <label className="flex items-start gap-2 text-sm cursor-pointer border-t border-surface-border/60 pt-3">
          <input type="checkbox" className="mt-0.5" checked={rules.create_debt}
            onChange={(e) => set('create_debt', e.target.checked)} />
          <span>Хаахдаа <b>өр (нөхөн төлбөр) үүсгэх</b>
            <span className="block text-xs text-slate-500">
              «Ерөнхий хугацаа хэтэрсэн» болон «төлөөгүй» дүрмээр хаагдсан машинд
              төлөгдөөгүй дүнгээр нэхэмжлэл үүснэ (дараа ирэхэд нь нэхэгдэнэ).
              Унтраавал бүртгэл зүгээр хаагдана.
            </span>
          </span>
        </label>

        <div className="flex flex-wrap gap-2">
          <button className="btn-primary" onClick={save} disabled={busy}>
            <Save size={15} /> {busy ? 'Хадгалж байна…' : 'Хадгалах'}
          </button>
          <button className="btn-secondary" onClick={runNow} disabled={busy}>
            <PlayCircle size={15} /> Яг одоо ажиллуулах
          </button>
        </div>
      </div>
    </div>
  )
}
