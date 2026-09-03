// Тохиргоо → Төлбөрийн дүрэм.
//
// Яагаад энэ таб (2026-09-03): төлбөр тооцох, хаалт нээх шийдвэрт нөлөөлдөг
// дүрмүүд .env, app_settings, зогсоолын багана, тарифын загвар гэсэн ДӨРВӨН
// өөр газар тархсан байсан. Үүнээс болж нэг зогсоолд тохирсон утга нөгөөг нь
// гацаадаг, «төлбөрөө төлсөн атлаа хаалт нээгдэхгүй» тохиолдлыг ямар тохиргоо
// үүсгэснийг хэн ч хэлж чаддаггүй байв.
//
// Энэ хуудас: (1) дүрэм бүрийг ЗОГСООЛ БҮРЭЭР дарж тохируулах, (2) тухайн
// зогсоолд ҮЙЛЧИЛЖ БУЙ бодит утгыг эх сурвалжтай нь харуулах, (3) машиныг
// гацаах тохиргооны ХОСЛОЛУУДЫГ урьдчилан илрүүлэх.
import { AlertTriangle, Info, RotateCcw, Save, Settings2, ShieldAlert } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'
import { useToast } from '../../components/ui'

const UNIT_LABEL = { hour: 'цаг', min: 'мин', sec: 'сек', mnt: '₮', count: 'ш' }

const POLICY_LABEL = {
  open: 'Шууд нээнэ',
  hold: 'Түр барина, дараа нь нээнэ',
  strict: 'Түр барина, нээхгүй',
}

const LEVEL_STYLE = {
  high: { box: 'border-red-500/40 bg-red-500/5', text: 'text-red-300', Icon: ShieldAlert },
  warn: { box: 'border-amber-500/40 bg-amber-500/5', text: 'text-amber-300', Icon: AlertTriangle },
  info: { box: 'border-slate-500/30 bg-slate-500/5', text: 'text-slate-300', Icon: Info },
}

const SOURCE_BADGE = {
  site: ['Энэ зогсоолд', 'bg-accent/15 text-accent border-accent/30'],
  global: ['Ерөнхий', 'bg-slate-500/10 text-slate-400 border-slate-600/40'],
  site_column: ['«Зогсоол» таб', 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30'],
}

/** Нэг дүрмийн мөр — үйлчилж буй утга, эх сурвалж, засварын талбар. */
function RuleRow({ row, draft, onChange, onReset }) {
  const edited = draft !== undefined
  const shown = edited ? draft : row.value
  const locked = row.source === 'site_column'
  const [badgeText, badgeCls] = SOURCE_BADGE[edited ? 'site' : row.source] || SOURCE_BADGE.global
  const overridden = row.source === 'site' || edited

  const control = () => {
    if (locked) {
      return <span className="font-mono text-cyan-300 text-sm">{String(shown)}</span>
    }
    if (row.unit === 'bool') {
      return (
        <input type="checkbox" className="mt-1" checked={!!shown}
          onChange={(e) => onChange(e.target.checked)} />
      )
    }
    if (row.unit === 'choice') {
      return (
        <select className="input w-56 text-sm" value={shown}
          onChange={(e) => onChange(e.target.value)}>
          {Object.entries(POLICY_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      )
    }
    if (row.unit === 'time') {
      return (
        <input className="input w-28 font-mono text-sm" type="time" value={shown || ''}
          onChange={(e) => onChange(e.target.value)} />
      )
    }
    return (
      <div className="flex items-center gap-1.5">
        <input className="input w-28 font-mono text-sm" type="number" min="0" value={shown ?? 0}
          onChange={(e) => onChange(Number(e.target.value))} />
        <span className="text-xs text-slate-500">{UNIT_LABEL[row.unit] || ''}</span>
      </div>
    )
  }

  return (
    <div className="py-3 border-t border-surface-border/40 first:border-t-0 grid md:grid-cols-[1fr_auto] gap-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{row.name}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded border ${badgeCls}`}>{badgeText}</span>
          {!row.per_site && !locked && (
            <span className="text-[10px] px-1.5 py-0.5 rounded border border-slate-600/40 text-slate-500">
              зөвхөн ерөнхий
            </span>
          )}
        </div>
        <p className="text-xs text-slate-400 mt-1">{row.desc}</p>
        <div className="text-[11px] text-slate-500 mt-1 space-y-0.5">
          <div><span className="text-slate-600">Хэзээ үйлчилнэ:</span> {row.applies}</div>
          {row.not_applied && row.not_applied !== '—' && (
            <div><span className="text-slate-600">Үйлчлэхгүй:</span> {row.not_applied}</div>
          )}
          {row.site_column_note && (
            <div className="text-cyan-400/80">{row.site_column_note}</div>
          )}
        </div>
      </div>
      <div className="flex items-start gap-2 md:justify-end">
        <div className="text-right">
          {control()}
          {overridden && !locked && (
            <div className="text-[10px] text-slate-500 mt-1">
              ерөнхий: <span className="font-mono">{String(row.global_value)}</span>
            </div>
          )}
        </div>
        {overridden && !locked && (
          <button type="button" title="Ерөнхий утга руу буцаах"
            className="text-slate-500 hover:text-slate-200 cursor-pointer mt-1.5"
            onClick={onReset}><RotateCcw size={14} /></button>
        )}
      </div>
    </div>
  )
}

export default function PaymentRulesSection() {
  const toast = useToast()
  const [index, setIndex] = useState(null)
  const [siteId, setSiteId] = useState('')
  const [report, setReport] = useState(null)
  const [draft, setDraft] = useState({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/api/admin/payment-rules').then((d) => {
      setIndex(d)
      if (d.sites?.length) setSiteId((cur) => cur || d.sites[0].id)
    }).catch((e) => toast(e.message, 'error'))
  }, [])

  const load = useCallback((id) => {
    if (!id) return
    setReport(null)
    setDraft({})
    api(`/api/admin/payment-rules/${id}`).then(setReport).catch((e) => toast(e.message, 'error'))
  }, [])

  useEffect(() => { load(siteId) }, [siteId, load])

  const setVal = (group, key, v) =>
    setDraft((d) => ({ ...d, [group]: { ...(d[group] || {}), [key]: v } }))

  // Ерөнхий рүү буцаах = null илгээж давхаргаас устгана
  const resetVal = (group, key) =>
    setDraft((d) => ({ ...d, [group]: { ...(d[group] || {}), [key]: null } }))

  const dirty = Object.values(draft).some((g) => Object.keys(g).length > 0)

  const save = async () => {
    setBusy(true)
    try {
      const fresh = await api(`/api/admin/payment-rules/${siteId}`, { method: 'PUT', body: draft })
      setReport(fresh)
      setDraft({})
      api('/api/admin/payment-rules').then(setIndex).catch(() => {})
      toast('Хадгалагдлаа — дараагийн уншилтаас эхлэн үйлчилнэ')
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  if (!index) return <div className="text-sm text-slate-500">Ачаалж байна…</div>
  if (!index.sites.length) return <div className="text-sm text-slate-500">Зогсоол алга.</div>

  const groups = report
    ? Object.entries(index.groups).map(([g, name]) => ([
      g, name, report.rules.filter((r) => r.group === g)]))
    : []

  return (
    <div className="space-y-5">
      <div className="card space-y-3">
        <div>
          <h2 className="font-semibold flex items-center gap-2">
            <Settings2 size={16} className="text-accent" /> Төлбөр ба хаалтны дүрэм
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Төлбөр тооцох, хаалт нээх шийдвэрт нөлөөлдөг <b className="text-slate-300">бүх
            дүрэм</b> энд байна. Дүрэм бүр анхдагчаар <b className="text-slate-300">ерөнхий
            (систем даяар)</b> утгаараа ажиллах бөгөөд зогсоол бүрд өөрөөр дарж
            тохируулж болно. Утгыг <RotateCcw size={11} className="inline" /> товчоор
            ерөнхий рүү нь буцаана.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select className="input w-auto min-w-[16rem]" value={siteId}
            onChange={(e) => setSiteId(e.target.value)} aria-label="Зогсоол">
            {index.sites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}{s.override_count ? ` — ${s.override_count} тусгай дүрэм` : ''}
              </option>
            ))}
          </select>
          {report && (
            <span className="text-xs text-slate-500">
              Тариф: <b className="text-slate-300">{report.tariff?.name || 'холбоогүй'}</b>
              {report.site_flags?.no_charge && ' · төлбөр авахгүй зогсоол'}
              {report.site_flags?.registered_only && ' · зөвхөн гэрээт'}
            </span>
          )}
        </div>
      </div>

      {/* ── Зөрчлийн шалгалт: «төлсөн ч хаалт нээгдэхгүй» тохиолдлын урьдчилсан илрүүлэлт ── */}
      {report && (
        <div className="card space-y-2">
          <h3 className="font-semibold text-sm flex items-center gap-2">
            <ShieldAlert size={15} className="text-amber-400" /> Зөрчлийн шалгалт
            <span className="text-xs font-normal text-slate-500">
              ({report.conflicts.length} олдол)
            </span>
          </h3>
          {report.conflicts.length === 0 ? (
            <p className="text-xs text-accent">
              Машиныг гацаах тохиргооны хослол илрээгүй.
            </p>
          ) : report.conflicts.map((c, i) => {
            const st = LEVEL_STYLE[c.level] || LEVEL_STYLE.info
            return (
              <div key={i} className={`rounded-lg border px-3 py-2 ${st.box}`}>
                <div className={`text-sm font-medium flex items-center gap-2 ${st.text}`}>
                  <st.Icon size={14} /> {c.title}
                </div>
                <p className="text-xs text-slate-400 mt-1">{c.detail}</p>
                <p className="text-xs text-slate-500 mt-1">→ {c.fix}</p>
              </div>
            )
          })}
        </div>
      )}

      {/* ── Дүрмүүд бүлгээр ── */}
      {groups.map(([g, name, rows]) => rows.length > 0 && (
        <div key={g} className="card">
          <h3 className="font-semibold text-sm mb-1">{name}</h3>
          {rows.map((r) => (
            <RuleRow key={`${r.group}.${r.key}`} row={r}
              draft={draft[r.group]?.[r.key] === undefined ? undefined
                : (draft[r.group][r.key] === null ? r.global_value : draft[r.group][r.key])}
              onChange={(v) => setVal(r.group, r.key, v)}
              onReset={() => resetVal(r.group, r.key)} />
          ))}
        </div>
      ))}

      {/* ── Энд БИШ тохируулагддаг дүрмүүд — хаанаас засахыг заана ── */}
      {report && (
        <div className="card space-y-3">
          <h3 className="font-semibold text-sm">Өөр хуудсанд тохируулагддаг дүрмүүд</h3>
          <div>
            <div className="text-xs text-slate-500 mb-1.5">
              <b className="text-slate-300">«Зогсоол» таб → засах цонх</b> — эдгээр нь
              дээрх ерөнхий дүрмийг ДАРНА.
            </div>
            <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1">
              {index.site_column_rules.map((f) => (
                <div key={f.key} className="text-xs flex justify-between gap-2 border-b border-surface-border/30 py-1">
                  <span className="text-slate-400" title={f.desc}>{f.name}</span>
                  <span className="font-mono text-slate-200">
                    {report.site_flags[f.key] === null || report.site_flags[f.key] === undefined
                      ? '—' : String(report.site_flags[f.key])}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="text-xs text-slate-500 mb-1.5">
              <b className="text-slate-300">«Тариф» хуудас</b> — үнийн шатлал ба хугацаа.
              {!report.tariff && <span className="text-red-400"> Тариф холбоогүй!</span>}
            </div>
            {report.tariff && (
              <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1">
                {index.tariff_rules.map((f) => (
                  <div key={f.key} className="text-xs flex justify-between gap-2 border-b border-surface-border/30 py-1">
                    <span className="text-slate-400" title={f.desc}>{f.name}</span>
                    <span className="font-mono text-slate-200">
                      {report.tariff[f.key] === null || report.tariff[f.key] === undefined
                        ? '—' : String(report.tariff[f.key])}
                    </span>
                  </div>
                ))}
                <div className="text-xs flex justify-between gap-2 border-b border-surface-border/30 py-1 sm:col-span-2">
                  <span className="text-slate-400">Үнийн шатлал</span>
                  <span className="font-mono text-slate-200">
                    {report.tariff.tiers.length
                      ? report.tariff.tiers.map((t) => `${t.upto_minutes}м→${t.price}₮`).join(' · ')
                      : '—'}
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="sticky bottom-4 flex justify-end">
        <button className="btn-primary shadow-lg" onClick={save} disabled={busy || !dirty}>
          <Save size={15} /> {busy ? 'Хадгалж байна…' : dirty ? 'Хадгалах' : 'Өөрчлөлт алга'}
        </button>
      </div>
    </div>
  )
}
