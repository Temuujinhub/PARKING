// Тохиргоо → LED дэлгэц: зогсоол бүрийн орох/гарах LED-ийн 4 мөрөнд юу
// харуулахыг сонгоно. Мөр бүр: Цаг / Машины дугаар / Хугацаа / Дүн / Текст,
// гарах дэлгэцэд нэмээд Төлбөрийн төрөл ба Үнэгүй гарсан шалтгаан.
// Хадгалагдсан тохиргоо PUT /api/admin/sites/{id} {screen_config}-оор явна;
// хоосон бол глобал .env-ийн template-ууд хэвээр үйлчилнэ.
import { Monitor } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'
import { useToast } from '../../components/ui'

const ENTRY_TYPES = [
  ['none', '— Хоосон —'],
  ['time', 'Цаг (орсон цаг)'],
  ['plate', 'Машины дугаар'],
  ['text', 'Текст (гараас)'],
]
const EXIT_TYPES = [
  ['none', '— Хоосон —'],
  ['time', 'Цаг (гарсан цаг)'],
  ['plate', 'Машины дугаар'],
  ['duration', 'Зогссон хугацаа'],
  ['amount', 'Төлбөрийн дүн'],
  ['payment', 'Төлбөрийн төрөл (QPay/Карт/Бэлэн)'],
  ['reason', 'Үнэгүй гарсан шалтгаан'],
  ['text', 'Текст (гараас)'],
]

// Урьдчилан харах жишээ утгууд
const SAMPLE = {
  time: '14:05', plate: '1234 УБА', duration: '2ts 05min', amount: '3000T',
  payment: 'QPay', reason: 'Гэрээт',
}

const emptyLines = () => Array.from({ length: 4 }, () => ({ type: 'none', text: '' }))

// DB-ээс ирсэн (богино байж болох) жагсаалтыг ямагт 4 мөр болгоно
const pad4 = (lines) => {
  const out = (Array.isArray(lines) ? lines : []).slice(0, 4).map((l) => ({
    type: l?.type || 'none', text: l?.text || '',
  }))
  while (out.length < 4) out.push({ type: 'none' })
  return out
}

function LanePanel({ title, hint, types, lines, setLines }) {
  const setLine = (i, patch) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  return (
    <div className="bg-surface border border-surface-border rounded-xl p-4 space-y-3 flex-1 min-w-[300px]">
      <div className="flex items-center gap-2 font-semibold"><Monitor size={16} className="text-accent" />{title}</div>
      <p className="text-xs text-slate-400">{hint}</p>
      {lines.map((l, i) => (
        <div key={i} className="flex gap-2 items-center">
          <span className="text-xs text-slate-500 w-10 shrink-0">{i + 1}-р мөр</span>
          <select value={l.type} onChange={(e) => setLine(i, { type: e.target.value })}
            className="input flex-1">
            {types.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          {l.type === 'text' && (
            <input value={l.text || ''} maxLength={40} placeholder="LED дээр гарах текст"
              onChange={(e) => setLine(i, { text: e.target.value })} className="input flex-1" />
          )}
        </div>
      ))}
      {/* Урьдчилан харах — LED шиг хар дэвсгэр дээр */}
      <div className="bg-black rounded-lg p-3 font-mono text-green-400 text-sm min-h-[5.5rem] space-y-0.5">
        {lines.filter((l) => l.type !== 'none' && (l.type !== 'text' || (l.text || '').trim()))
          .map((l, i) => <div key={i}>{l.type === 'text' ? l.text : SAMPLE[l.type]}</div>)}
        {lines.every((l) => l.type === 'none' || (l.type === 'text' && !(l.text || '').trim())) && (
          <div className="text-slate-600">(тохиргоогүй — системийн үндсэн текст гарна)</div>
        )}
      </div>
    </div>
  )
}

export default function ScreenSection() {
  const toast = useToast()
  const [sites, setSites] = useState([])
  const [siteId, setSiteId] = useState('')
  const [entry, setEntry] = useState(emptyLines())
  const [exit, setExit] = useState(emptyLines())
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/api/admin/sites').then((rows) => {
      setSites(rows)
      if (rows.length && !siteId) setSiteId(rows[0].id)
    })
  }, [])

  // Зогсоол солиход тухайн зогсоолын хадгалагдсан тохиргоог дүүргэнэ
  useEffect(() => {
    const s = sites.find((x) => x.id === siteId)
    const cfg = s?.screen_config || {}
    setEntry(pad4(cfg.entry))
    setExit(pad4(cfg.exit))
  }, [siteId, sites])

  const save = async () => {
    setBusy(true)
    try {
      const clean = (lines) => lines.map((l) =>
        l.type === 'text' ? { type: 'text', text: (l.text || '').trim() } : { type: l.type })
      await api(`/api/admin/sites/${siteId}`, {
        method: 'PUT', body: { screen_config: { entry: clean(entry), exit: clean(exit) } },
      })
      toast('LED дэлгэцийн тохиргоо хадгалагдлаа')
      api('/api/admin/sites').then(setSites) // preview-д шинэ утгыг тусгана
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  const reset = async () => {
    if (!confirm('Энэ зогсоолын LED тохиргоог арилгаж, системийн үндсэн текст рүү буцаах уу?')) return
    setBusy(true)
    try {
      await api(`/api/admin/sites/${siteId}`, { method: 'PUT', body: { screen_config: null } })
      toast('Үндсэн тохиргоонд буцлаа')
      setEntry(emptyLines()); setExit(emptyLines())
      api('/api/admin/sites').then(setSites)
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-sm text-slate-300">Зогсоол:</label>
        <select value={siteId} onChange={(e) => setSiteId(e.target.value)} className="input w-64">
          {sites.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.site_code})</option>)}
        </select>
      </div>
      <p className="text-xs text-slate-400 max-w-3xl">
        Машин орох/гарах мөчид LED дэлгэцэд гарах мөрүүд. Хоосон утгатай мөр (ж: төлбөртэй
        гарахад «Үнэгүй гарсан шалтгаан») автоматаар хасагдана. Бодит LED дээр туршихдаа
        Хаалтны удирдлага → «LED дэлгэц тест»-ийг ашиглана.
      </p>
      <div className="flex gap-4 flex-wrap">
        <LanePanel title="Орох дэлгэц" types={ENTRY_TYPES}
          hint="Машин орж ирэхэд харуулах мөрүүд" lines={entry} setLines={setEntry} />
        <LanePanel title="Гарах дэлгэц" types={EXIT_TYPES}
          hint="Төлбөр төлөгдөж/үнэгүй нөхцөлөөр гарахад харуулах мөрүүд" lines={exit} setLines={setExit} />
      </div>
      <div className="flex gap-2">
        <button onClick={save} disabled={busy || !siteId} className="btn-primary">Хадгалах</button>
        <button onClick={reset} disabled={busy || !siteId}
          className="px-4 py-2 text-sm rounded-lg border border-surface-border text-slate-300 hover:text-white cursor-pointer">
          Үндсэн тохиргоонд буцаах
        </button>
      </div>
    </div>
  )
}
