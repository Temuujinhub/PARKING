// Нээх шалтгааны удирдлагатай жагсаалт — оператор машиныг төлбөргүй гаргах /
// хаалт гараар нээхдээ эндээс сонгоно. Төлбөрийн шийдвэрийн нэг хэсэг тул
// «Төлбөрийн дүрэм» табын ерөнхий горимд харагдана.
import { ListChecks, Save } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../../api'

export default function OpenReasonsCard({ toast }) {
  const [items, setItems] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/api/admin/open-reasons').then(setItems).catch((e) => toast(e.message, 'error'))
  }, [])

  if (!items) return null
  const set = (i, k, v) => setItems(items.map((r, n) => (n === i ? { ...r, [k]: v } : r)))
  const add = () => setItems([...items, { code: '', label: '', is_active: true }])
  const save = async () => {
    setBusy(true)
    try {
      setItems(await api('/api/admin/open-reasons', { method: 'PUT', body: { items } }))
      toast('Хадгалагдлаа')
    } catch (e) { toast(e.message, 'error') } finally { setBusy(false) }
  }

  return (
    <div className="card space-y-4">
      <div>
        <h2 className="font-semibold flex items-center gap-2">
          <ListChecks size={16} className="text-accent" /> Нээх шалтгаан
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Оператор машиныг төлбөргүй гаргах / хаалт гараар нээхдээ энэ жагсаалтаас
          сонгоно. Чөлөөт текст байсан үед «хэн, ямар шалтгаанаар хэдэн удаа үнэгүй
          гаргасан» гэдгийг тоолох боломжгүй байв.
          <b className="text-slate-300"> Код нь тайлангийн түлхүүр — бүү өөрчил.</b>
        </p>
      </div>

      <div className="space-y-2">
        {items.map((r, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <input className="input font-mono w-36" value={r.code} maxLength={30}
              placeholder="код" aria-label="Код"
              onChange={(e) => set(i, 'code', e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))} />
            <input className="input flex-1 min-w-48" value={r.label} maxLength={80}
              placeholder="Операторт харагдах нэр" aria-label="Нэр"
              onChange={(e) => set(i, 'label', e.target.value)} />
            <label className="flex items-center gap-1.5 text-sm cursor-pointer whitespace-nowrap">
              <input type="checkbox" checked={r.is_active !== false}
                onChange={(e) => set(i, 'is_active', e.target.checked)} />
              идэвхтэй
            </label>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-2">
        <button className="btn-primary" onClick={save} disabled={busy}>
          <Save size={15} /> {busy ? 'Хадгалж байна…' : 'Хадгалах'}
        </button>
        <button className="btn-secondary" onClick={add}>+ Мөр нэмэх</button>
      </div>
      <p className="text-[11px] text-slate-500">
        Хэрэглэгдэж байсан шалтгааныг устгахын оронд «идэвхтэй»-г нь авбал хуучин
        тайлан бүтэн хэвээр үлдэнэ.
      </p>
    </div>
  )
}

