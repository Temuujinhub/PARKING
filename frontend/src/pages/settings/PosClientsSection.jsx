// Тохиргоо → Холбогдсон POS (2026-10-15).
//
// Яагаад: «100айл барилгачин» хуучин POS апп build-ээр Ontime e-Barimt-ыг өөрөө хэвлэж
// давхардал үүсгэсээр байхад ХЭН (хэрэглэгч/терминал) ЯМАР хувилбартай, ямар төлөвтэй вэ
// гэдгийг харах газар байгаагүй. Прод дээр PAX терминал Төхөөрөмжид огт бүртгэгдээгүй, олон
// апп зааврын жишээ terminal_id ('TDB-PAX-SITE01-01')-г хуулж ашигладаг.
//
// Энэ таб: (1) POS аппын хүсэлтээс автоматаар (апп өөрчлөхгүйгээр) цугласан «холбогдсон POS»-ын
// жагсаалт, (2) POS-той холбоотой ОДОО БАЙГАА тохиргоог нэг дор (засварыг өөрийн газарт нь),
// (3) аппын хамгийн бага хувилбар (зөвхөн тодруулга). «Онлайн»/«сүүлд» нь бодит цагаар (серверийн
// санах ой); тоолуур/төлөв ≤60с, зөвхөн heartbeat-тэй терминалынх ≤4ц хоцорч болно (бичилтийн throttle).
// 10-15: heartbeat 60с → 4ц (өдөрт ≤6, эзний шийдвэр) тул «Холбоотой» (heartbeat-ийн хугацаанд)
// төлөв нэмэв — сул терминал 5 минутын дараа худал «Холбогдоогүй» болохгүй. Сервер дээр
// PARKING_POS_CLIENTS_ENABLED=false бол жагсаалт хоосон, мэдэгдэл л харагдана.
import { AlertTriangle, ListChecks, RefreshCw, Save, Smartphone, Users } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtMDHM } from '../../api'
import { Table, useToast } from '../../components/ui'

const PRESENCE = {
  online: ['Онлайн', 'bg-emerald-400'],
  idle: ['Холбоотой', 'bg-sky-400'],
  offline: ['Холбогдоогүй', 'bg-slate-500'],
  stale: ['Сураггүй', 'bg-red-500'],
}

/** 14400 → «4 цаг», 18000 → «5 цаг», 4500 → «1 цаг 15 мин», 300 → «5 мин» */
function dur(sec) {
  if (sec === null || sec === undefined) return '—'
  const h = Math.floor(sec / 3600)
  const m = Math.round((sec % 3600) / 60)
  if (!h) return `${m} мин`
  return m ? `${h} цаг ${m} мин` : `${h} цаг`
}

/** Төлөвийн шошго + тайлбар (серверийн thresholds-оор — heartbeat-ийн давтамж .env-ээс). */
function presenceInfo(p, th) {
  const [label, dot] = PRESENCE[p] || PRESENCE.offline
  const tips = {
    online: `Сүүлийн ${dur(th.online_sec)}-д POS аппын хүсэлт ирсэн`,
    idle: `Хүсэлтгүй ч heartbeat-ийн хугацаанд (${dur(th.heartbeat_sec)} + зөвшөөрөл = ${dur(th.idle_sec)}) сураг гарсан — асаалттай, сул`,
    offline: `${dur(th.idle_sec)}-аас дээш сураггүй — heartbeat ирэх ёстой байсан ч ирээгүй (унтраалттай / сүлжээгүй / хуучин апп)`,
    stale: `${th.stale_hours} цагаас дээш сураггүй`,
  }
  return [p === 'stale' ? `${th.stale_hours}ц+ сураггүй` : label, dot, tips[p] || '']
}

// Аппын мэдээлэх төлөв — чөлөөт текст; мэдэгдэх утгуудыг Монголоор
const STATUS_LABEL = {
  ONLINE: 'Хэвийн', IDLE: 'Сул зогсолт', PRINTER_ERROR: 'Принтерийн алдаа', PAPER_OUT: 'Цаас дууссан',
  OFFLINE_QUEUE: 'Офлайн дараалалтай', LOW_BATTERY: 'Цэнэг бага', ERROR: 'Алдаа',
}

const RECEIPT_LABEL = {
  SENT: ['ДДТД-тэй', 'text-emerald-300'],
  PENDING: ['Хүлээгдэж буй', 'text-amber-300'],
  FAILED: ['Алдаа', 'text-red-300'],
  EXTERNAL: ['Терминал гаргасан', 'text-amber-300'],
  CANCELLED: ['Цуцалсан', 'text-slate-400'],
}

const VERSION_CLS = {
  ok: 'text-slate-200',
  behind: 'text-amber-300',
  unknown: 'text-amber-300',
  outdated: 'text-red-300 font-semibold',
}

const VIA_LABEL = {
  role: 'role POS', user_list: 'тохиргооны жагсаалт', card_history: '≥ карт', connected: 'холбогдсон',
}

/** «3 мин», «2 цаг», «5 хоног» — UTC naive мөрийг одоогийн цагтай харьцуулна. */
function ago(iso, nowMs) {
  if (!iso) return '—'
  const sec = Math.max(0, Math.round((nowMs - new Date(iso + (iso.endsWith('Z') ? '' : 'Z'))) / 1000))
  if (sec < 60) return `${sec}с`
  if (sec < 3600) return `${Math.floor(sec / 60)} мин`
  if (sec < 86400) return `${Math.floor(sec / 3600)} цаг`
  return `${Math.floor(sec / 86400)} хоног`
}

function Chip({ children, cls = 'border-slate-600/40 text-slate-400', title }) {
  return (
    <span title={title} className={`text-[10px] px-1.5 py-0.5 rounded border whitespace-nowrap ${cls}`}>
      {children}
    </span>
  )
}

function ClientRow({ c, nowMs, th }) {
  const [pLabel, pDot, pTip] = presenceInfo(c.presence, th)
  const rc = c.last_receipt ? (RECEIPT_LABEL[c.last_receipt.status] || [c.last_receipt.status, 'text-slate-300']) : null
  return (
    <tr className={c.presence === 'stale' ? 'opacity-60' : ''}>
      <td className="td">
        <div className="font-mono text-xs">{c.terminal_id || <span className="text-slate-500">тодорхойгүй</span>}</div>
        <div className="flex flex-wrap gap-1 mt-1">
          {c.terminal_is_example && (
            <Chip cls="border-red-500/40 text-red-300"
              title="Зааврын ЖИШЭЭ дугаар — апп бүр хуулж тавьсан тул терминалыг ялгахгүй. Бодит терминалын дугаар тавиулна.">
              жишээ ID
            </Chip>
          )}
          {c.terminal_shared && !c.terminal_is_example && (
            <Chip cls="border-amber-500/40 text-amber-300" title="Энэ terminal_id-г өөр хэрэглэгч ч илгээдэг">давхардсан</Chip>
          )}
          {c.registered_terminal
            ? <Chip cls={c.registered_terminal.site_mismatch ? 'border-red-500/40 text-red-300' : 'border-cyan-500/30 text-cyan-300'}
              title={c.registered_terminal.site_mismatch ? 'Бүртгэлийн зогсоол хэрэглэгчийн зогсоолоос өөр' : ''}>
              бүртгэлтэй{c.registered_terminal.site_mismatch ? ' · өөр зогсоол' : ''}
            </Chip>
            : c.terminal_id && <Chip title="Тохиргоо → Төхөөрөмжид pax_terminal төрлөөр бүртгээгүй">бүртгэлгүй</Chip>}
        </div>
      </td>
      <td className="td">
        <div className={`font-mono text-xs ${c.user_active ? '' : 'line-through text-slate-500'}`}>{c.username}</div>
        <div className="text-[10px] text-slate-500">{c.user_role}</div>
      </td>
      <td className="td text-xs">
        {c.site_name || (c.user_site_count > 1 ? <span className="text-slate-500">{c.user_site_count} зогсоол</span> : '—')}
        {c.site_source === 'payment' && <div className="text-[10px] text-slate-500">сүүлийн төлбөрөөр</div>}
      </td>
      <td className="td">
        <span className={`font-mono text-xs ${VERSION_CLS[c.version_state] || ''}`}
          title={c.prev_pos_version ? `${c.prev_pos_version} → ${c.pos_version} (${fmtMDHM(c.version_changed_at)})` : ''}>
          {c.pos_version || 'толгойгүй'}
        </span>
        {c.version_state === 'outdated' && <div className="text-[10px] text-red-300">шинэчлэх</div>}
        {c.version_state === 'unknown' && <div className="text-[10px] text-amber-300">хуучин build?</div>}
      </td>
      <td className="td text-xs">
        <div className="flex items-center gap-1.5 whitespace-nowrap" title={pTip}>
          <span className={`inline-block w-2 h-2 rounded-full ${pDot}`} /> {pLabel}
        </div>
        {c.status && (
          <div className={`text-[11px] ${/ERROR|OUT|OFFLINE|LOW/.test(c.status) ? 'text-amber-300' : 'text-slate-400'}`}
            title={c.status_at ? `мэдээлсэн: ${fmtMDHM(c.status_at)}` : ''}>
            {STATUS_LABEL[c.status] || c.status}
          </div>
        )}
        {(c.printer || c.battery !== null || c.network) && (
          <div className="text-[10px] text-slate-500">
            {[c.printer && `принтер: ${c.printer}`, c.battery !== null && c.battery !== undefined && `${c.battery}%`,
              c.network].filter(Boolean).join(' · ')}
          </div>
        )}
      </td>
      <td className="td text-xs whitespace-nowrap">
        <div>{ago(c.last_seen_at, nowMs)} өмнө</div>
        <div className="text-[10px] text-slate-500" title={c.last_endpoint || ''}>{fmtMDHM(c.last_seen_at)}</div>
      </td>
      <td className="td font-mono text-[11px] text-slate-400">{c.last_ip || '—'}</td>
      <td className="td text-xs whitespace-nowrap">
        <span className="font-mono font-semibold">{c.payments_24h}</span>
        <span className="text-slate-500"> / 7х {c.payments_7d}</span>
      </td>
      <td className="td text-xs">
        {c.receipt_mode === 'terminal'
          ? <span className="text-amber-300" title="Тохиргоо → Төлбөрийн дүрэм: «POS терминал өөрөө e-Barimt гаргадаг» асаалттай — систем баримт гаргахгүй">
            Терминал ({c.receipt_methods.join(',')})
          </span>
          : <span className="text-slate-300">Систем (msgbill)</span>}
        {c.external_7d > 0 && <div className="text-[10px] text-amber-300">7х «терминал гаргасан»: {c.external_7d}</div>}
      </td>
      <td className="td text-xs whitespace-nowrap">
        {rc ? <>
          <span className={rc[1]}>{rc[0]}</span>
          <div className="text-[10px] text-slate-500">{fmtMDHM(c.last_paid_at)}</div>
        </> : <span className="text-slate-500">—</span>}
      </td>
    </tr>
  )
}

function SettingsSummary({ s, onGotoRules, onGotoDevices, onSaved, toast }) {
  const [minVer, setMinVer] = useState(s.pos_app_rules.min_app_version || '')
  const [busy, setBusy] = useState(false)
  useEffect(() => { setMinVer(s.pos_app_rules.min_app_version || '') }, [s.pos_app_rules.min_app_version])
  const save = async () => {
    setBusy(true)
    try {
      onSaved(await api('/api/admin/pos-settings', { method: 'PUT', body: { min_app_version: minVer.trim() } }))
      toast('Хадгалагдлаа')
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }
  const eb = s.ebarimt
  const overridden = eb.sites.filter((x) => Object.keys(x.ebarimt_overrides).length > 0)
  const termSites = eb.sites.filter((x) => x.receipt_mode === 'terminal')
  const holdOver = eb.sites.filter((x) => x.payment_hold_overridden)
  const gotoRules = onGotoRules && (
    <button type="button" className="text-xs underline text-accent cursor-pointer" onClick={onGotoRules}>
      Төлбөрийн дүрэм таб руу
    </button>
  )

  return (
    <div className="card space-y-5">
      <div>
        <h2 className="font-semibold flex items-center gap-2">
          <ListChecks size={16} className="text-accent" /> POS-той холбоотой тохиргоо
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Өөр өөр табд тархсан POS-ын тохиргоог нэг дор <b className="text-slate-300">харуулна</b>.
          Засварыг өөрийн газарт нь (эрхийн шалгалт тэнд) — доорх холбоосоор.
        </p>
      </div>

      {/* Аппын доод хувилбар — ШИНЭ, зөвхөн тодруулга */}
      <section className="space-y-2">
        <h3 className="text-sm font-medium">Аппын хамгийн бага хувилбар</h3>
        <div className="flex flex-wrap items-center gap-2">
          <input className="input w-32 font-mono text-sm" value={minVer} maxLength={20} placeholder="ж: 1.5.3"
            aria-label="Аппын хамгийн бага хувилбар" disabled={!s.can_edit_global || busy}
            onChange={(e) => setMinVer(e.target.value.replace(/[^0-9.]/g, ''))} />
          {s.can_edit_global && (
            <button className="btn-primary py-1.5" onClick={save}
              disabled={busy || minVer.trim() === (s.pos_app_rules.min_app_version || '')}>
              <Save size={14} /> Хадгалах
            </button>
          )}
        </div>
        <p className="text-[11px] text-slate-500">
          Үүнээс доош хувилбартай POS улаанаар тодорно; heartbeat-ийн хариунд <span className="font-mono">update_required</span>.
          Хүсэлтийг ХААХГҮЙ. Хоосон бол хамгийн сүүлд харагдсан хувилбараас хоцорсныг шараар.
          {!s.can_edit_global && ' Бүх түрээслэгчид нийтлэг тул зөвхөн харах.'}
        </p>
      </section>

      {/* e-Barimt: терминал өөрөө баримт гаргадаг уу */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-medium">e-Barimt — POS терминал өөрөө гаргадаг уу</h3>
          {gotoRules}
        </div>
        <div className="text-xs text-slate-400 space-y-0.5">
          <div>Ерөнхий: <b className={eb.global.pos_terminal_issues ? 'text-amber-300' : 'text-slate-200'}>
            {eb.global.pos_terminal_issues ? `асаалттай (${eb.global_receipt_methods.join(',')}) — систем баримт гаргахгүй` : 'унтраалттай — систем (msgbill) гаргана'}
          </b></div>
          <div>POS аппын хэрэглэгчид (толгойгүй хуучин build-д): <span className="font-mono text-slate-300">
            {eb.global.pos_terminal_users || '—'}</span></div>
          <div>Терминал баримт гаргадаг зогсоол: {termSites.length
            ? <b className="text-amber-300">{termSites.map((x) => `${x.site_name} (${x.receipt_methods.join(',')})`).join(', ')}</b>
            : <span className="text-slate-300">алга</span>}</div>
          {overridden.length > 0 && (
            <div>Зогсоолоор дарсан: {overridden.map((x) => (
              <span key={x.site_id} className="mr-2">
                {x.site_name}: <span className="font-mono">{Object.entries(x.ebarimt_overrides).map(([k, v]) => `${k}=${v}`).join(', ')}</span>
              </span>
            ))}</div>
          )}
        </div>
        <p className="text-[11px] text-slate-500">
          Баримтын хариуны хүлээлт (POS дээр ДДТД/QR шууд хэвлэх): <b className="text-slate-300">{s.receipt_inline_wait_sec}с</b>
          {' '}(.env <span className="font-mono">PARKING_RECEIPT_INLINE_WAIT_SEC</span>, зөвхөн харах).
        </p>
      </section>

      {/* Зогсоол бүрээр: e-Barimt горим · үнэ барих · POS bootstrap-ын эгнээ */}
      <section className="space-y-2">
        <h3 className="text-sm font-medium">Зогсоол бүрийн POS тохиргоо</h3>
        <Table headers={['Зогсоол', 'e-Barimt', 'Үнэ барих', 'POS эгнээ (bootstrap)']} empty={eb.sites.length === 0}>
          {eb.sites.map((x) => (
            <tr key={x.site_id}>
              <td className="td text-xs">{x.site_name}</td>
              <td className="td text-xs">
                {x.receipt_mode === 'terminal'
                  ? <span className="text-amber-300">Терминал ({x.receipt_methods.join(',')})</span>
                  : 'Систем (msgbill)'}
                {Object.keys(x.ebarimt_overrides).length > 0 && <> <Chip cls="border-accent/30 text-accent">зогсоолоор</Chip></>}
              </td>
              <td className="td text-xs whitespace-nowrap">
                {x.payment_hold_minutes} мин
                {x.payment_hold_overridden && <> <Chip cls="border-accent/30 text-accent">зогсоолоор</Chip></>}
              </td>
              <td className="td text-[11px]">
                {x.lanes.length === 0 ? <span className="text-slate-500">—</span> : (
                  <div className="flex flex-wrap gap-1">
                    {x.lanes.map((ln) => (
                      <Chip key={`${ln.lane_dir}${ln.lane_no}${ln.nested_inner}`}
                        cls={ln.barrier && ln.camera ? undefined : 'border-amber-500/40 text-amber-300'}
                        title={ln.barrier ? (ln.camera ? '' : 'камергүй') : 'хаалт бүртгэгдээгүй — POS сонгуулахгүй'}>
                        {{ entry: 'Орох', exit: 'Гарах', both: 'Хоёр' }[ln.lane_dir] || ln.lane_dir} {ln.lane_no}
                        {ln.nested_inner ? ' (дотор)' : ''}{!ln.barrier ? ' · хаалтгүй' : (!ln.camera ? ' · камергүй' : '')}
                      </Chip>
                    ))}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </section>

      {/* Бүртгэлтэй PAX терминал */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-medium">Төхөөрөмжид бүртгэлтэй PAX терминал</h3>
          {onGotoDevices && (
            <button type="button" className="text-xs underline text-accent cursor-pointer" onClick={onGotoDevices}>
              Төхөөрөмж таб руу
            </button>
          )}
        </div>
        {s.terminals.length === 0 ? (
          <p className="text-xs text-amber-300/90 border border-amber-500/30 bg-amber-500/5 rounded-lg px-3 py-2">
            Бүртгэлтэй терминал алга. Апп-ууд хуучин урсгалаар (prepare-гүй /pos/confirm) ажиллаж байна;
            <span className="font-mono"> /pos/terminal/&#123;id&#125;</span> 404, <span className="font-mono">/pos/prepare</span> 403 өгнө.
            Терминалыг <b>pax_terminal</b> төрлөөр, <b>device_key = бодит terminal_id</b>-аар бүртгэвэл зогсоолыг
            терминалаар нь танина.
          </p>
        ) : (
          <Table headers={['Нэр', 'terminal_id', 'Зогсоол', 'Төлөв', 'Хувилбар', 'Сүүлд']}>
            {s.terminals.map((t) => (
              <tr key={t.id}>
                <td className="td text-xs">{t.name}</td>
                <td className="td font-mono text-xs">{t.terminal_id}</td>
                <td className="td text-xs">{t.site_name}</td>
                <td className="td text-xs">{t.status}</td>
                <td className="td font-mono text-xs">{t.app_version || '—'}</td>
                <td className="td text-xs">{fmtMDHM(t.last_seen) || '—'}</td>
              </tr>
            ))}
          </Table>
        )}
      </section>

      {/* POS аппын хэрэглэгчид */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="text-sm font-medium flex items-center gap-1.5"><Users size={14} /> POS аппын хэрэглэгчид</h3>
          <Link to="/users" className="text-xs underline text-accent">Хэрэглэгчид хуудас руу</Link>
        </div>
        <Table headers={['Хэрэглэгч', 'Роль', 'Зогсоол', 'Танисан', 'POS карт', 'Эрх', 'Хувилбар', 'Сүүлд']}
          empty={s.pos_users.length === 0}>
          {s.pos_users.map((u) => (
            <tr key={u.id} className={u.is_active ? '' : 'opacity-50'}>
              <td className="td font-mono text-xs">{u.username}</td>
              <td className="td text-xs">{u.role}</td>
              <td className="td text-xs">{u.sites === null ? 'бүх зогсоол' : (u.sites.join(', ') || '—')}</td>
              <td className="td"><div className="flex flex-wrap gap-1">
                {u.recognized_by.map((v) => <Chip key={v}>{VIA_LABEL[v] || v}</Chip>)}
              </div></td>
              <td className="td font-mono text-xs" title={`≥${s.pos_app_min_card} бол POS аппын хэрэглэгч гэж автоматаар танина`}>
                {u.card_count}
              </td>
              <td className="td text-[11px] text-slate-400">
                {[u.free_exit && 'free_exit', u.pay_transfer && 'pay_transfer'].filter(Boolean).join(', ') || '—'}
              </td>
              <td className="td font-mono text-xs">{u.versions.join(', ') || '—'}</td>
              <td className="td text-xs">{fmtMDHM(u.last_seen_at) || '—'}</td>
            </tr>
          ))}
        </Table>
      </section>

      {/* Нээх шалтгаан / онцгой гаргалт / үнэ барих */}
      <section className="grid md:grid-cols-2 gap-5">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-sm font-medium">Төлбөргүй гаргах шалтгаан (POS bootstrap)</h3>
            {gotoRules}
          </div>
          <div className="flex flex-wrap gap-1">
            {s.open_reasons.map((r) => (
              <Chip key={r.code} cls={r.is_active === false ? 'border-slate-700 text-slate-600 line-through' : undefined}
                title={r.code}>{r.label}</Chip>
            ))}
          </div>
          <h3 className="text-sm font-medium pt-2">Онцгой гаргалт (free_exit эрхгүй POS)</h3>
          <div className="flex flex-wrap gap-1">
            {s.special_exit_kinds.map((k) => (
              <Chip key={k.kind} title={k.kind}>{k.label}{k.needs_snapshot ? ' · зурагтай' : ''}</Chip>
            ))}
          </div>
        </div>
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="text-sm font-medium">Төлбөрийн үнэ барих хугацаа</h3>
            {gotoRules}
          </div>
          <p className="text-xs text-slate-400">
            Ерөнхий: <b className="text-slate-200">{s.payment_hold_minutes_global} мин</b> — гарах камерт уншигдсан
            үеийн үнийг POS дээр төлөх хүртэл барина.
          </p>
          {holdOver.length > 0 && (
            <div className="text-xs text-slate-400">
              Зогсоолоор дарсан: {holdOver.length} (дээрх хүснэгтэд)
            </div>
          )}
          <p className="text-[11px] text-slate-500 pt-2">
            Апп → сервер: хүсэлт бүрд <span className="font-mono">X-App-Version</span>,{' '}
            <span className="font-mono">X-Terminal-Id</span>, (сонголттой) <span className="font-mono">X-POS-Status</span>{' '}
            (нэмэлт хүсэлт үүсгэхгүй); {dur(s.capture.heartbeat_sec)} тутам (өдөрт ≤{Math.ceil(86400 / s.capture.heartbeat_sec)}),
            мөн апп асах/нэвтрэх, төлөв солигдох үед (≤1 / {dur(s.capture.heartbeat_status_min_sec)}){' '}
            <span className="font-mono">POST {s.capture.heartbeat_path}</span> (docs/PAX_POS_APP_GUIDE.md §2.2;
            .env <span className="font-mono">PARKING_POS_HEARTBEAT_SEC</span>).
            {!s.capture.enabled && <b className="text-amber-300"> Бүртгэл серверт унтраалттай.</b>}
          </p>
        </div>
      </section>
    </div>
  )
}

export default function PosClientsSection({ onGotoRules, onGotoDevices }) {
  const toast = useToast()
  const [data, setData] = useState(null)
  const [settings, setSettings] = useState(null)
  const [error, setError] = useState('')
  const [siteId, setSiteId] = useState('')
  const [allHistory, setAllHistory] = useState(false)
  const [loading, setLoading] = useState(false)
  const [nowMs, setNowMs] = useState(Date.now())
  const reqId = useRef(0)

  const load = useCallback(() => {
    const id = ++reqId.current
    setLoading(true)
    const qs = new URLSearchParams({ days: allHistory ? '0' : '30', ...(siteId ? { site_id: siteId } : {}) })
    api(`/api/admin/pos-clients?${qs}`).then((d) => {
      if (id !== reqId.current) return
      setData(d); setError(''); setNowMs(Date.now())
    }).catch((e) => { if (id === reqId.current) setError(e.message) })
      .finally(() => { if (id === reqId.current) setLoading(false) })
  }, [siteId, allHistory])

  useEffect(() => { load() }, [load])
  // 60с тутам шинэчилнэ (7 хоногийн төлбөрийн query-тэй тайлан — DB ачаалал багасгахын тулд):
  // таб нуугдсан эсвэл серверт бүртгэл унтраалттай бол татахгүй
  const disabledRef = useRef(false)
  useEffect(() => { disabledRef.current = Boolean(data?.disabled) }, [data])
  useEffect(() => {
    const t = setInterval(() => {
      if (!document.hidden && !disabledRef.current) load()
    }, 60000)
    return () => clearInterval(t)
  }, [load])
  useEffect(() => {
    api('/api/admin/pos-settings').then(setSettings).catch((e) => toast(e.message, 'error'))
  }, [])

  if (!data) return error
    ? <p role="alert" className="text-sm text-slate-100">{error}</p>
    : <div role="status" className="text-sm text-slate-500">Ачаалж байна…</div>

  const sm = data.summary
  const th = data.thresholds
  const settingsCard = settings && (
    <SettingsSummary s={settings} onGotoRules={onGotoRules} onGotoDevices={onGotoDevices}
      onSaved={(s) => { setSettings(s); load() }} toast={toast} />
  )
  if (data.disabled) {
    return (
      <div className="space-y-5">
        <div className="card space-y-2">
          <h2 className="font-semibold flex items-center gap-2">
            <Smartphone size={16} className="text-accent" /> Холбогдсон POS
          </h2>
          <p role="status" className="text-sm text-amber-300/90 border border-amber-500/30 bg-amber-500/5 rounded-lg px-3 py-2">
            Серверт унтраалттай (<span className="font-mono">PARKING_POS_CLIENTS_ENABLED=false</span>) — POS аппын
            хүсэлтийг бүртгэхгүй, heartbeat-ийг хадгалахгүй. Төлбөр, хаалт, e-Barimt хэвийн ажиллана.
            Асаахад .env-д <span className="font-mono">true</span> болгоод backend-ийг restart хийнэ.
          </p>
        </div>
        {settingsCard}
      </div>
    )
  }
  return (
    <div className="space-y-5">
      {error && <p role="alert" className="text-sm text-slate-100 border border-brand-red rounded-lg p-3">{error}</p>}
      <div className="card space-y-3">
        <div>
          <h2 className="font-semibold flex items-center gap-2">
            <Smartphone size={16} className="text-accent" /> Холбогдсон POS
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            PAX POS аппын хүсэлтүүдээс автоматаар цуглуулна (хэрэглэгч × терминал): хувилбар
            (<span className="font-mono">X-App-Version</span>), төлөв, сүүлд холбогдсон цаг, IP. Онлайн = сүүлийн
            {' '}{dur(th.online_sec)}-д хүсэлттэй; Холбоотой = heartbeat-ийн хугацаанд ({dur(th.idle_sec)}) сураг
            гарсан; Холбогдоогүй = heartbeat хоцорсон. Тоолуур ≤{th.throttle_sec}с (зөвхөн heartbeat бол
            ≤{dur(th.heartbeat_sec)}) хоцорч болно.
            {data.min_app_version
              ? <> Доод хувилбар: <b className="text-slate-200 font-mono">{data.min_app_version}</b>.</>
              : data.latest_version && <> Хамгийн сүүлийн хувилбар: <b className="text-slate-200 font-mono">{data.latest_version}</b>.</>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="px-2 py-1 rounded bg-surface-muted">Нийт <b>{sm.total}</b></span>
          <span className="px-2 py-1 rounded bg-surface-muted">Онлайн <b className="text-emerald-300">{sm.online}</b></span>
          <span className="px-2 py-1 rounded bg-surface-muted" title={presenceInfo('idle', th)[2]}>
            Холбоотой <b className="text-sky-300">{sm.idle ?? 0}</b>
          </span>
          <span className="px-2 py-1 rounded bg-surface-muted">Хуучин/тодорхойгүй хувилбар <b className={sm.outdated ? 'text-amber-300' : ''}>{sm.outdated}</b></span>
          <span className="px-2 py-1 rounded bg-surface-muted">Терминал баримт гаргадаг <b className={sm.terminal_receipts ? 'text-amber-300' : ''}>{sm.terminal_receipts}</b></span>
          {sm.external_7d > 0 && (
            <span className="px-2 py-1 rounded bg-amber-500/10 text-amber-300">7х «терминал гаргасан» {sm.external_7d}</span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select className="input w-auto min-w-[14rem]" value={siteId} onChange={(e) => setSiteId(e.target.value)}
            aria-label="Зогсоол">
            <option value="">Бүх зогсоол</option>
            {data.sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <label className="flex items-center gap-1.5 text-sm cursor-pointer">
            <input type="checkbox" checked={allHistory} onChange={(e) => setAllHistory(e.target.checked)} />
            30 хоногоос өмнөх
          </label>
          <button className="btn-secondary py-1.5" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Шинэчлэх
          </button>
          <span className="text-[11px] text-slate-500">{fmtMDHM(data.generated_at)}</span>
        </div>
        {sm.shared_terminal_ids > 0 && (
          <p className="text-xs text-amber-300/90 flex items-start gap-1.5">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            {sm.shared_terminal_ids} terminal_id хэд хэдэн хэрэглэгчээс ирж байна (ихэвчлэн зааврын жишээ
            «TDB-PAX-SITE01-01») — терминалыг ялгахын тулд апп бүрт бодит терминалын дугаар тавиулна уу.
          </p>
        )}
        <Table maxH="65vh" empty={data.clients.length === 0}
          headers={['Терминал', 'Хэрэглэгч', 'Зогсоол', 'Хувилбар', 'Төлөв', 'Сүүлд холбогдсон', 'IP',
            '24ц төлбөр', 'e-Barimt горим', 'Сүүлийн баримт']}>
          {data.clients.map((c) => <ClientRow key={c.id} c={c} nowMs={nowMs} th={th} />)}
        </Table>
      </div>
      {settingsCard}
    </div>
  )
}
