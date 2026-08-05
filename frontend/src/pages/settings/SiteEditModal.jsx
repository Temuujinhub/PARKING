// Зогсоол засах modal — үндсэн мэдээлэл + QPay дансны тохиргоо
import { Field, Modal } from '../../components/ui'

export default function SiteEditModal({ editing, setEditing, templates, tenants, onSubmit, onTest }) {
  return (
    <Modal open={!!editing} onClose={() => setEditing(null)} title="Зогсоол засах">
      {editing && (
        <form onSubmit={onSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Нэр" required>
              <input className="input" value={editing.name} required
                onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </Field>
            <Field label="Код (QR URL-д)" required>
              <input className="input font-mono" value={editing.site_code} required
                onChange={(e) => setEditing({ ...editing, site_code: e.target.value.toUpperCase() })} />
            </Field>
            <Field label="Бүс">
              <select className="input" value={editing.zone_code}
                onChange={(e) => setEditing({ ...editing, zone_code: e.target.value })}>
                {['A', 'B', 'C'].map((z) => <option key={z}>{z}</option>)}
              </select>
            </Field>
            {/* Зогсоолыг ТҮР идэвхгүй болгох — тухайн талбай өөр системд шилжсэн,
                эсвэл засвартай үед. Идэвхгүй үед камерын event сонсохоо болино,
                хаалт автоматаар нээгдэхгүй. Өгөгдөл, түүх ХЭВЭЭР үлдэнэ. */}
            <Field label="Төлөв">
              <label className="flex items-center gap-2 cursor-pointer select-none py-2">
                <input type="checkbox" className="w-4 h-4 accent-accent"
                  checked={editing.is_active !== false}
                  onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
                <span className="text-sm">
                  {editing.is_active !== false ? 'Идэвхтэй' : 'ИДЭВХГҮЙ — камер сонсохгүй, хаалт авто нээгдэхгүй'}
                </span>
              </label>
            </Field>
            <Field label="Багтаамж">
              <input className="input" type="number" min="1" value={editing.unlimited ? '' : editing.capacity}
                disabled={editing.unlimited} placeholder={editing.unlimited ? 'Хязгааргүй' : ''}
                onChange={(e) => setEditing({ ...editing, capacity: e.target.value })} />
              <label className="flex items-center gap-2 mt-1.5 text-xs text-slate-400 cursor-pointer">
                <input type="checkbox" className="cursor-pointer" checked={!!editing.unlimited}
                  onChange={(e) => setEditing({ ...editing, unlimited: e.target.checked, capacity: e.target.checked ? 0 : (editing.capacity || 50) })} />
                Дүүргэлтгүй (багтаамжийн хязгааргүй)
              </label>
            </Field>
          </div>
          <Field label="Хаяг">
            <input className="input" value={editing.address || ''}
              onChange={(e) => setEditing({ ...editing, address: e.target.value })} />
          </Field>
          {/* Зогсоолыг түрээслэгчид оноох — зөвхөн SUPER_ADMIN (tenants prop ирсэн үед).
              Оноогоогүй зогсоол tenant-аар хамардаг хэрэглэгчдийн жагсаалтад харагдахгүй
              тул "өнчин" үлдэхээс сэргийлж эндээс засна. */}
          {tenants && (
            <Field label="Түрээслэгч">
              <select className="input" value={editing.tenant_id || ''}
                onChange={(e) => setEditing({ ...editing, tenant_id: e.target.value || null })}>
                <option value="">— Оноогоогүй (хэний ч жагсаалтад харагдахгүй!) —</option>
                {tenants.map((t) => <option key={t.id} value={t.id}>{t.name} ({t.code})</option>)}
              </select>
            </Field>
          )}
          <Field label="Хэвлэгдсэн самбарын QR линк">
            <input className="input font-mono text-xs" value={editing.qr_url || ''}
              placeholder="Хоосон бол автоматаар /pay?site=КОД"
              onChange={(e) => setEditing({ ...editing, qr_url: e.target.value })} />
            <div className="text-xs text-slate-400 mt-1.5">
              Талбайд хэвлэгдчихсэн самбар өөр линктэй бол ЯГ тэр линкийг энд бичнэ —
              систем үүсгэх QR тэр самбартай ижил болно. Самбарыг солихгүйгээр
              үргэлжлүүлэн ашиглана.
            </div>
          </Field>
          <Field label="Тарифын загвар">
            <select className="input" value={editing.tariff_template_id || ''}
              onChange={(e) => setEditing({ ...editing, tariff_template_id: e.target.value })}>
              <option value="">Сонгоогүй</option>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
          <Field label="Гацсан машины авто хаалт (цаг)">
            <input className="input" type="number" min="0" placeholder="12 (default)"
              value={editing.auto_close_hours ?? ''}
              onChange={(e) => setEditing({ ...editing, auto_close_hours: e.target.value })} />
            <div className="text-[11px] text-slate-500 mt-1">
              Энэ цагаас дээш идэвхтэй үлдсэн машиныг систем автоматаар хасаж, төлөгдөөгүй
              дүнгээр өр үүсгэнэ. Хоосон = default, 0 = унтраах.
            </div>
          </Field>
          <Field label="Зөвхөн орох уншилттай машиныг үнэгүй хаах (цаг)">
            <input className="input" type="number" min="0" placeholder="72 (default)"
              value={editing.entry_only_free_hours ?? ''}
              onChange={(e) => setEditing({ ...editing, entry_only_free_hours: e.target.value })} />
            <div className="text-[11px] text-slate-500 mt-1">
              Гарах камерт огт уншигдаагүй (ихэвчлэн гарах уншилт алдагдсан) машиныг
              энэ цагийн дараа ӨР ҮҮСГЭЛГҮЙ үнэгүй хасна. Хоосон = 72ц, 0 = унтраах.
            </div>
          </Field>

          <details className="rounded-lg border border-slate-700 px-3 py-2" open>
            <summary className="cursor-pointer text-sm font-medium py-1">
              Төлбөрийн данс (QPay)
              {editing.qpay_mode === 'own'
                ? <span className="ml-2 text-xs text-accent">· энэ зогсоолын өөрийн данс</span>
                : <span className="ml-2 text-xs text-amber-400">· системийн ерөнхий данс</span>}
            </summary>

            {/* Аль данс руу төлөгдөхийг ИЛТ сонгуулна — талбар хоосон байхад
                жишээ текст (placeholder) бөглөсөн мэт харагдаж, ерөнхий данс
                руу төлөгдөж байгааг анзаараагүй тохиолдол гарсан. */}
            <div className="my-2 space-y-1.5">
              {[['global',
                 editing.tenant_qpay_set
                   ? `Түрээслэгчийн данс: ${editing.tenant_name} (автомат)`
                   : 'Системийн ерөнхий данс (EasyParking)',
                 editing.tenant_qpay_set
                   ? 'Энэ зогсоол түрээслэгчийнхээ QPay дансаар ажиллана — тусад нь юу ч оруулах шаардлагагүй'
                   : 'Түрээслэгчид данс тохируулаагүй тул системийн ерөнхий данс. Түрээслэгчийн данс тавихдаа Тохиргоо → Түрээслэгч.'],
                ['own', 'Зөвхөн энэ зогсоолын ТУСГАЙ данс (ховор)', 'Нэг зогсоол л өөр дансаар ажиллах ёстой онцгой тохиолдолд гараас оруулна']]
                .map(([v, label, hint]) => (
                <label key={v} className={`flex items-start gap-2.5 px-3 py-2 rounded-lg border cursor-pointer transition-colors
                  ${editing.qpay_mode === v ? 'border-accent/50 bg-accent/5' : 'border-surface-border hover:border-slate-600'}`}>
                  <input type="radio" name="qpay_mode" className="mt-0.5 cursor-pointer"
                    checked={editing.qpay_mode === v}
                    onChange={() => setEditing({ ...editing, qpay_mode: v })} />
                  <span>
                    <span className="text-sm">{label}</span>
                    <span className="block text-[11px] text-slate-400">{hint}</span>
                  </span>
                </label>
              ))}
            </div>

            <div className={`grid grid-cols-2 gap-3 ${editing.qpay_mode === 'own' ? '' : 'hidden'}`}>
              <Field label="Нэвтрэх нэр (username)">
                <input className="input font-mono text-xs" autoComplete="off"
                  value={editing.qpay_username || ''} placeholder="жишээ: MONNIS_PROPERTIES"
                  onChange={(e) => setEditing({ ...editing, qpay_username: e.target.value })} />
              </Field>
              <Field label="Нууц үг (password)">
                <input className="input font-mono text-xs" type="password" autoComplete="new-password"
                  value={editing.qpay_password ?? ''}
                  placeholder={editing.qpay_password_set ? '•••••• (хадгалагдсан)' : 'заавал бөглөнө'}
                  onChange={(e) => setEditing({ ...editing, qpay_password: e.target.value })} />
              </Field>
              <Field label="Нэхэмжлэхийн код (invoice_code)">
                <input className="input font-mono text-xs" value={editing.qpay_invoice_code || ''}
                  placeholder="жишээ: MONNIS_PROPERTIES_INVOICE"
                  onChange={(e) => setEditing({ ...editing, qpay_invoice_code: e.target.value })} />
              </Field>
              <Field label="НӨАТ-ын дүүрэг+хороо (4 орон)">
                <input className="input font-mono text-xs" value={editing.qpay_district_code || ''}
                  placeholder="жишээ: 2318 (Хан-Уул 18-р хороо)"
                  onChange={(e) => setEditing({ ...editing, qpay_district_code: e.target.value })} />
              </Field>
            </div>
            {editing.id && (
              <button type="button" className="btn-secondary w-full justify-center mt-3"
                onClick={onTest}>
                Дансыг турших (жижиг дүнгээр бодит төлбөр)
              </button>
            )}
            <div className="text-[11px] text-slate-500 mt-1.5">
              <b className="text-amber-400">Эхлээд «Хадгалах» дарна</b>, дараа нь турших.
              Хадгалахгүйгээр туршвал хуучин (ерөнхий) данс гарч ирнэ. Туршилт нь машин
              орох шаардлагагүй — QR гарч ирэх ба төлсний дараа e-Barimt хэний ТТД-ээр
              үүссэнийг харуулна.
            </div>
          </details>

          <button className="btn-primary w-full justify-center">Хадгалах</button>
        </form>
      )}
    </Modal>
  )
}
