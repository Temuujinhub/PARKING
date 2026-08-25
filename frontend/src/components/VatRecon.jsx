// ТЕГ тулгалт — Ибаримт хуудасны тусдаа таб.
//
// Яагаад тусдаа таб: өмнө нь толгойн ганц товч + modal байсан тул (1) алдаа
// гарахад 3 секундын toast л харагдаж «ЮУНЫ алдаа вэ» гэдэг ойлгогдохгүй,
// (2) түрээслэгч/багана/цагийн тохиргоо тавих газаргүй байв. Одоо алдаа
// ЗӨВ ХЭВЭЭР үлдэж, дэлгэрэнгүй оношилгоотойгоо хамт харагдана.
import { AlertTriangle, FileCheck2, FileSpreadsheet, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { api, fmt } from '../api'
import { useToast } from './ui'

const TZ_OPTS = [['', 'Авто (өөрөө тааруулна)'], ['0', 'Файл UTC цагаар'], ['-8', 'Файл УБ (локал) цагаар']]

export default function VatRecon({ tenants = [] }) {
  const [tenantId, setTenantId] = useState('')
  const [tz, setTz] = useState('')
  const [tol, setTol] = useState(3)
  const [cols, setCols] = useState({ col_ddtd: '', col_dt: '', col_amount: '' })
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [recon, setRecon] = useState(null)
  const [drag, setDrag] = useState(false)
  const fileRef = useRef(null)
  const toast = useToast()

  const query = () => {
    const p = new URLSearchParams()
    if (tenantId) p.set('tenant_id', tenantId)
    if (tz !== '') p.set('tz_shift', tz)
    if (Number(tol) !== 3) p.set('tol', String(tol))
    for (const [k, v] of Object.entries(cols)) if (v.trim()) p.set(k, v.trim())
    return p
  }

  // ТЕГ-ийн мерчант порталын экспортыг манай баримттай тулгана.
  // ДДТД-ээр тулгадаггүй (суваг ба ТЕГ өөр дугаарладаг) — цаг+дүнгээр.
  const reconcile = async (f) => {
    if (!f) return
    setFile(f); setBusy(true); setErr(null); setRecon(null)
    try {
      const fd = new FormData()
      fd.append('file', f)
      const p = query()
      const data = await api(`/api/reports/vat-reconcile${p.toString() ? `?${p}` : ''}`,
        { method: 'POST', formData: fd })
      setRecon(data)
    } catch (e) {
      setErr(e.message)                 // toast биш — уншиж амжихгүй урт мессеж
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  // Санхүүд илгээх НЭГТГЭСЭН Excel — ТЕГ мөр бүрийн хажууд манай баримт
  const reconExcel = async () => {
    if (!file) return
    try {
      const fd = new FormData()
      fd.append('file', file)
      const p = query()
      p.set('excel', '1')
      const blob = await api(`/api/reports/vat-reconcile?${p}`, { method: 'POST', formData: fd, blob: true })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = 'ebarimt-tulgalt.xlsx'
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) { toast(e.message, 'error') }
  }

  const d = recon?.diag
  return (
    <div className="space-y-4">
      <div className="card space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            <div className="text-slate-400 mb-1">Түрээслэгч (хэний баримттай тулгах вэ)</div>
            <select className="input w-auto min-w-56" value={tenantId}
              onChange={(e) => setTenantId(e.target.value)} aria-label="Түрээслэгч сонгох">
              <option value="">Бүх зогсоол (шүүлтгүй)</option>
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}{t.tin ? ` · ТТД ${t.tin}` : ''} ({t.site_count})
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <div className="text-slate-400 mb-1">Файлын цаг</div>
            <select className="input w-auto" value={tz} onChange={(e) => setTz(e.target.value)}
              aria-label="Файлын цагийн бүс">
              {TZ_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="text-sm">
            <div className="text-slate-400 mb-1">Зөвшөөрөх зөрүү (сек)</div>
            <input className="input w-24" type="number" min="0" max="600" value={tol}
              onChange={(e) => setTol(e.target.value)} />
          </label>
        </div>
        <div className="text-xs text-slate-500">
          ТЕГ портал ТТД тус бүрээр экспорт өгдөг тул түрээслэгчээ сонгоно уу —
          эс бөгөөс бусад ТТД-ийн баримт «манайд алга» болж хуурамч зөрүү харагдана.
        </div>

        {/* Файл сонгох / чирж оруулах */}
        <div onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); reconcile(e.dataTransfer.files?.[0]) }}
          className={`rounded-xl border-2 border-dashed p-6 text-center transition-colors
            ${drag ? 'border-accent bg-accent/5' : 'border-surface-border/70'}`}>
          <input ref={fileRef} type="file" accept=".xlsx,.xlsm,.csv" className="hidden"
            onChange={(e) => reconcile(e.target.files?.[0])} />
          <FileSpreadsheet size={26} className="mx-auto mb-2 text-slate-500" />
          <div className="text-sm text-slate-400 mb-3">
            ТЕГ-ийн мерчант порталын баримтын экспортыг (.xlsx / .csv) энд чирж оруулна уу
          </div>
          <button className="btn-secondary" disabled={busy} onClick={() => fileRef.current?.click()}>
            <Upload size={15} /> {busy ? 'Тулгаж байна…' : 'Файл сонгох'}
          </button>
          {file && !busy && <div className="text-xs text-slate-500 mt-2 font-mono">{file.name}</div>}
        </div>

        {/* Багана автоматаар танигдаагүй үеийн гар тохиргоо */}
        <details className="text-sm">
          <summary className="cursor-pointer text-slate-400 hover:text-slate-200">
            Багана гараар заах (автоматаар танигдаагүй үед)
          </summary>
          <div className="flex flex-wrap gap-3 mt-2">
            {[['col_ddtd', 'ДДТД багана'], ['col_dt', 'Огнооны багана'], ['col_amount', 'Дүнгийн багана']].map(([k, l]) => (
              <label key={k} className="text-xs">
                <div className="text-slate-500 mb-1">{l}</div>
                <input className="input w-24 font-mono" placeholder="ж: B" value={cols[k]}
                  onChange={(e) => setCols({ ...cols, [k]: e.target.value })} />
              </label>
            ))}
          </div>
          <div className="text-xs text-slate-500 mt-2">
            Excel-ийн баганын үсгийг (B, C, D…) бичээд файлаа дахин сонгоно уу.
          </div>
        </details>
      </div>

      {/* Алдаа — toast биш, уншиж дуустал байрандаа үлдэнэ */}
      {err && (
        <div className="card border-red-500/50 bg-red-500/5 space-y-2" role="alert">
          <div className="flex items-center gap-2 text-red-400 font-semibold text-sm">
            <AlertTriangle size={15} /> Тулгалт амжилтгүй
          </div>
          <pre className="text-xs whitespace-pre-wrap break-words text-slate-300 font-mono leading-relaxed">{err}</pre>
        </div>
      )}

      {recon && (
        <div className="card space-y-3 text-sm">
          <div className="flex flex-wrap gap-4">
            <span>ТЕГ файлд: <b className="font-mono">{recon.tax_total}</b></span>
            <span>Манайд (тухайн хугацаанд): <b className="font-mono">{recon.ours_total}</b></span>
            <span className="text-accent">Таарсан: <b className="font-mono">{recon.matched}</b></span>
            <span className="text-amber-400">Манайд бий/ТЕГ-д алга: <b className="font-mono">{recon.unmatched_ours_total}</b></span>
            <span className="text-amber-400">ТЕГ-д бий/манайд алга: <b className="font-mono">{recon.unmatched_tax_total}</b></span>
          </div>
          {/* Хоёр «зөрүү» хоорондоо ТААРАХ ЁСГҮЙ — эдгээр нь тулгалтын хүрээнээс
              гадуур үлдсэн, огт өөр шалтгаантай баримтууд. Тэр шалтгааныг задалж
              харуулна (өмнө нь зөвхөн эцсийн тоо харагдаж, «яагаад?» нь далд байв) */}
          {(recon.outside_window > 0 || recon.cancelled > 0) && (
            <div className="text-xs text-slate-400 flex flex-wrap gap-4">
              {recon.outside_window > 0 && (
                <span title="Асуулга нь цагийн шилжилтэд зориулж ±9 цагийн нөөцтэй татдаг. Файл тэр хугацааг хамраагүй тул эдгээр нь зөрүү БИШ.">
                  Файлын хамрах хугацаанаас гадуур: <b className="font-mono">{recon.outside_window}</b> (тооцоогүй)
                </span>
              )}
              {recon.cancelled > 0 && (
                <span title="Цуцлагдсан баримт ТЕГ-д байхгүй нь хэвийн">
                  Цуцлагдсан: <b className="font-mono">{recon.cancelled}</b> (алгассан)
                </span>
              )}
            </div>
          )}
          {/* Кассын (POS) дугаараар — ДДТД-ийн сүүлийн 8 орон нь баримт гаргасан
              кассыг заана. Файлд ямар касс байна, манайд ямар байна вэ. */}
          {Object.keys(recon.tax_pos || {}).length > 0 && (
            <div className="text-[11px] text-slate-400 space-y-0.5">
              <div>ТЕГ файлын касс (ДДТД-ийн сүүлийн 8 орон): {Object.entries(recon.tax_pos)
                .sort((a, b) => b[1] - a[1]).map(([k, v]) => `…${k}: ${v}`).join(' · ')}</div>
              <div>Манай баримтын касс: {Object.entries(recon.ours_pos || {})
                .sort((a, b) => b[1] - a[1]).map(([k, v]) => `…${k}: ${v}`).join(' · ') || '—'}</div>
              {Object.keys(recon.unmatched_tax_pos || {}).length > 0 && (
                <div className="text-amber-400">Таараагүй мөрийн касс: {Object.entries(recon.unmatched_tax_pos)
                  .sort((a, b) => b[1] - a[1]).map(([k, v]) => `…${k}: ${v}`).join(' · ')}</div>
              )}
            </div>
          )}
          {recon.matched > 0 && (
            <div className="rounded-lg border border-surface-border/60 p-3 space-y-2">
              <div className="text-xs font-semibold">
                ДДТД манайх ↔ ТЕГ-ийнх:{' '}
                <b className={recon.ddtd_equal === recon.matched ? 'text-accent' : 'text-amber-400'}>
                  {recon.ddtd_equal} / {recon.matched} ижил
                </b>
              </div>
              <div className="text-[11px] text-slate-500">
                Тулгалт ДДТД-ээр хийгддэггүй (цаг+дүнгээр) — энэ мөр нь «манай хадгалсан
                дугаар ТЕГ-ийнхтэй таарч байна уу» гэдгийг ХЭМЖИНЭ.
              </div>
              {Object.entries(recon.ddtd_by_provider || {}).map(([prov, st]) => (
                <div key={prov} className="text-[11px] font-mono">
                  <span className="text-slate-300">{prov}</span>: {st.equal}/{st.matched} ижил
                  {st.sample && (
                    <div className="pl-3 text-slate-500 break-all">
                      манайх: {st.sample.ours}<br />ТЕГ:&nbsp;&nbsp;&nbsp; {st.sample.tax}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {d && (
            <div className="text-xs text-slate-500 font-mono">
              {d.file} · {d.kind} · {d.bytes ? `${(d.bytes / 1024).toFixed(0)}KB · ` : ''}
              хуудас «{d.sheet}» · {d.rows} мөр → {d.parsed} баримт ·
              багана ДДТД={d.columns?.ddtd || '?'}/огноо={d.columns?.dt || '?'}/дүн={d.columns?.amount || '?'}
              {recon.tenant ? ` · түрээслэгч: ${recon.tenant}` : ''}
              {recon.tz_shift ? ` · цагийн шилжилт ${recon.tz_shift > 0 ? '+' : ''}${recon.tz_shift}ц` : ''}
            </div>
          )}
          {(d?.warnings || []).map((w, i) => (
            <div key={i} className="flex items-start gap-2 text-xs text-amber-400">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" /> {w}
            </div>
          ))}
          {Object.keys(d?.skipped || {}).length > 0 && (
            <div className="text-xs text-amber-400">
              Алгассан мөр: {Object.entries(d.skipped).map(([k, v]) => `${k} уншигдаагүй ${v}`).join(', ')}
            </div>
          )}
          <button className="btn-primary py-1.5 text-sm" onClick={reconExcel}>
            <FileCheck2 size={15} /> Нэгтгэсэн Excel татах (санхүүд илгээх)
          </button>
          <div className="text-xs text-slate-500">
            {recon.note} · ТЕГ эх сурвалж: {Object.entries(recon.tax_sources || {})
              .map(([k, v]) => `${k || '(хоосон)'}: ${v}`).join(', ')}
          </div>
          {recon.unmatched_ours?.length > 0 && (
            <div>
              <div className="font-semibold text-xs mb-1">
                Манайд бий, ТЕГ-д алга (эхний {recon.unmatched_ours.length}):
              </div>
              <div className="max-h-64 overflow-auto text-xs font-mono space-y-0.5">
                {recon.unmatched_ours.map((r, i) => (
                  <div key={i}>
                    {r.paid_at?.slice(0, 19)} {r.plate || '—'} {fmt(r.amount)}₮ {r.site_name || ''} {r.provider} {r.status}
                  </div>
                ))}
              </div>
            </div>
          )}
          {recon.unmatched_tax?.length > 0 && (
            <div>
              <div className="font-semibold text-xs mb-1">
                ТЕГ-д бий, манайд алга ({recon.unmatched_tax_total}) — мөр бүрийг манай
                ТӨЛБӨРийн бүртгэлтэй тулгаж шалтгааныг нь тодорхойлов:
              </div>
              {/* Шалтгааны хураангуй — «баримтгүй төлбөр авсан уу, давхар баримт уу»
                  гэдгийг гараар хөөх ажлыг орлоно */}
              <div className="flex flex-wrap gap-2 mb-2">
                {Object.entries(recon.tax_verdicts || {}).sort((a, b) => b[1] - a[1]).map(([v, n]) => (
                  <span key={v} title={recon.verdict_labels?.[v] || v}
                    className={`text-[11px] px-2 py-1 rounded-md border ${
                      v === 'DUPLICATE' || v === 'PAYMENT_NO_RECEIPT'
                        ? 'border-red-500/50 bg-red-500/10 text-red-300'
                        : 'border-surface-border/60 text-slate-400'}`}>
                    {(recon.verdict_labels?.[v] || v).split(' — ')[0]}: <b className="font-mono">{n}</b>
                  </span>
                ))}
              </div>
              <div className="max-h-64 overflow-auto text-xs font-mono space-y-0.5">
                {recon.unmatched_tax.map((r, i) => (
                  <div key={i} className={r.verdict === 'DUPLICATE' || r.verdict === 'PAYMENT_NO_RECEIPT'
                    ? 'text-red-300' : ''}>
                    {r.dt?.slice(0, 19)} {fmt(r.amount)}₮ {r.ddtd}
                    {r.plate ? ` · ${r.plate} ${r.site_name} ${r.method}` : ''}
                    {' · '}{(recon.verdict_labels?.[r.verdict] || r.verdict).split(' — ')[0]}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
