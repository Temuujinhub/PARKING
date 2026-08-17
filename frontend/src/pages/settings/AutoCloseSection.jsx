// Тохиргоо → Авто цэвэрлэгээ: зогсоолд гацсан бүртгэлийг ХЭЗЭЭ, ЯАЖ хаах дүрэм.
// Өмнө нь эдгээр нь .env-д хатуу бичигдсэн байсан тул өөрчлөхөд deploy шаарддаг байв.
import { Eraser, HeartPulse, PlayCircle, RotateCw, Save } from 'lucide-react'
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
            <input type="checkbox" className="mt-0.5" checked={rules.create_debt_log_exit}
              onChange={(e) => set('create_debt_log_exit', e.target.checked)} />
            <span>Логоор <b>гарсан нь тогтоогдсон</b> машинд өр үүсгэх
              <span className="block text-xs text-slate-500">
                Камерын өөрийн бичлэг гарсан цагийг гэрчилнэ — төлбөр нь тэр
                цагаар зөв бодогдох тул ЖИНХЭНЭ авлага. Унтраавал бүртгэл
                зүгээр хаагдаж, мөнгө нэхэгдэхгүй.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={rules.create_debt}
              onChange={(e) => set('create_debt', e.target.checked)} />
            <span>Гарах уншилтгүй машинд өр үүсгэх
              <span className="block text-xs text-slate-500">
                Машин хэзээ гарсныг МЭДЭХГҮЙ тул хугацаа нь таамаг — 2026-08-12-нд
                ийм дүрэм 1,786 хуурамч өр (3.3 сая₮) үүсгэсэн. Унтраалттай орхино уу.
              </span>
            </span>
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

// Камерын эрүүл мэнд — ГАЦСАН камерыг илрүүлж, шаардвал reboot хийнэ.
// Гацсан = event стрим АМЬД (машин бүртгэгддэг) атлаа snapshot.cgi ШУУД 400
// (2026-08-10 Рашбулаг дээр батлагдсан — reboot л засдаг).
const VERDICT = {
  healthy: ['✓ Эрүүл', 'text-emerald-400'],
  hung: ['⛔ Гацсан', 'text-rose-400'],
  busy: ['~ Завгүй', 'text-amber-400'],
  unreachable: ['✗ Хүрэхгүй', 'text-slate-400'],
  error: ['✗ Алдаа', 'text-slate-400'],
}

function CamHealthCard({ toast }) {
  const [rules, setRules] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = () => api('/api/admin/camhealth/rules').then(setRules).catch(() => {})
  useEffect(() => { load() }, [])

  const save = async () => {
    setBusy(true)
    try {
      const { last, ...body } = rules
      await api('/api/admin/camhealth/rules', { method: 'PUT', body })
      toast('Хадгалагдлаа'); load()
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const run = async (dryRun) => {
    if (!dryRun && rules.auto_reboot && !window.confirm(
      'Одоо шалгаад ГАЦСАН камер олдвол автоматаар REBOOT хийнэ.\n\n' +
      'Reboot хийгдэж буй камер 1-2 минут хаалт нээхгүй, дугаар танихгүй.\n' +
      'Хаалганы өмнө машингүй үед ажиллуулна уу. Үргэлжлүүлэх үү?')) return
    setBusy(true)
    try {
      const r = await api('/api/admin/camhealth/run', { method: 'POST', body: { dry_run: dryRun } })
      const c = r.counts || {}
      const reb = (r.rebooted || []).length
      toast(dryRun
        ? `Эрүүл ${c.healthy || 0} · гацсан ${c.hung || 0} · завгүй ${c.busy || 0} · хүрэхгүй ${c.unreachable || 0}`
        : (reb ? `${reb} гацсан камер reboot хийгдлээ` : `Гацсан камер олдсонгүй (эрүүл ${c.healthy || 0})`))
      load()
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  if (!rules) return null
  const set = (k, v) => setRules({ ...rules, [k]: v })
  const last = rules.last || {}
  const problems = last.problems || []

  return (
    <div className="card space-y-4">
      <div>
        <h2 className="font-semibold flex items-center gap-2">
          <HeartPulse size={16} className="text-accent" /> Камерын эрүүл мэнд
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Камерын веб/зургийн дэд систем хааяа <b className="text-slate-300">гацдаг</b> —
          event урсгал амьд (машин бүртгэгдсээр) атлаа зураг татах хүсэлт шууд
          «Bad Request» болно. Ийм камер зөвхөн <b className="text-slate-300">reboot</b>-оор
          сэргэдэг. Систем өдөрт хэдэн удаа шалгаж, гацсаныг илрүүлж (тохиргоо зөвшөөрвөл)
          автоматаар унтрааж асаана.
        </p>
      </div>

      <label className="flex items-start gap-2 text-sm cursor-pointer">
        <input type="checkbox" className="mt-0.5" checked={rules.enabled}
          onChange={(e) => set('enabled', e.target.checked)} />
        <span>Автоматаар шалгах
          <span className="block text-xs text-slate-500">
            Унтраалттай үед зөвхөн доорх товчоор гараар шалгана.
          </span>
        </span>
      </label>

      <label className="flex items-start gap-2 text-sm cursor-pointer">
        <input type="checkbox" className="mt-0.5" checked={rules.auto_reboot}
          disabled={!rules.enabled}
          onChange={(e) => set('auto_reboot', e.target.checked)} />
        <span>Гацсан камерыг <b>автоматаар reboot хийх</b>
          <span className="block text-xs text-amber-500/90">
            Reboot үед тэр хаалт 1-2 мин ажиллахгүй. Хаалт хүлээж буй үед хийхгүй,
            камер тутамд завсартай. Унтраавал зөвхөн илрүүлж, эндээс харуулна.
          </span>
        </span>
      </label>

      <div className="grid sm:grid-cols-3 gap-3">
        <Field label="Өдөрт хэдэн удаа">
          <input className="input font-mono" type="number" min="1" max="24"
            value={rules.times_per_day} disabled={!rules.enabled}
            onChange={(e) => set('times_per_day', Number(e.target.value))} />
          <span className="block text-[11px] text-slate-500 mt-1">4 = 6 цаг тутам.</span>
        </Field>
        <Field label="Дахин reboot-ын завсар (мин)">
          <input className="input font-mono" type="number" min="10" max="1440"
            value={rules.cooldown_min} disabled={!rules.enabled || !rules.auto_reboot}
            onChange={(e) => set('cooldown_min', Number(e.target.value))} />
          <span className="block text-[11px] text-slate-500 mt-1">
            Нэг камерыг энэ хугацаанд дахин reboot хийхгүй (шуурга гаргахгүй).
          </span>
        </Field>
        <Field label="Баталгаажуулах уншилт">
          <input className="input font-mono" type="number" min="1" max="10"
            value={rules.samples} disabled={!rules.enabled}
            onChange={(e) => set('samples', Number(e.target.value))} />
          <span className="block text-[11px] text-slate-500 mt-1">
            snapshot.cgi-г хэдэн удаа шалгаж «гацсан» гэдгийг батлах.
          </span>
        </Field>
      </div>

      {last.checked_at && (
        <div className="text-xs text-slate-400 border-t border-surface-border/60 pt-3">
          <div>
            Сүүлд шалгасан: <span className="font-mono">
              {last.checked_at.replace('T', ' ').slice(0, 16)} UTC</span>
            {last.counts && <> · эрүүл {last.counts.healthy || 0} · гацсан{' '}
              <b className={last.counts.hung ? 'text-rose-400' : ''}>{last.counts.hung || 0}</b>
              {' '}· завгүй {last.counts.busy || 0} · хүрэхгүй {last.counts.unreachable || 0}</>}
          </div>
          {(last.rebooted || []).length > 0 && (
            <div className="mt-1 text-rose-300">
              Reboot хийсэн: {last.rebooted.map((r) => `${r.name} (${r.ip})`).join(', ')}
            </div>
          )}
          {problems.length > 0 && (
            <div className="mt-1 space-y-0.5 font-mono">
              {problems.map((p, i) => {
                const [lbl, cls] = VERDICT[p.verdict] || ['?', '']
                return (
                  <div key={i}>
                    <span className={cls}>{lbl}</span> {p.ip} — {p.name}
                    {p.min_bad_lat != null && ` (${p.min_bad_lat.toFixed(2)}с)`}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" onClick={save} disabled={busy}>
          <Save size={15} /> Хадгалах
        </button>
        <button className="btn-secondary" onClick={() => run(true)} disabled={busy}>
          <PlayCircle size={15} /> Одоо шалгах (reboot хийхгүй)
        </button>
        <button className="btn-secondary" onClick={() => run(false)} disabled={busy}>
          <RotateCw size={15} /> Шалгаад засах
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
      <CamHealthCard toast={toast} />
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
