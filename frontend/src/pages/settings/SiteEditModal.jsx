// Зогсоол засах modal — үндсэн мэдээлэл + QPay дансны тохиргоо
import { Field, Modal } from '../../components/ui'

export default function SiteEditModal({ editing, setEditing, templates, tenants, sites = [], onSubmit, onTest }) {
  // Эцэг болж болох зогсоолууд: өөрөө биш, өөрөө дотор зогсоол биш (хоёр давхар
  // үүрлэлт дэмжигдэхгүй), мөн энэ зогсоол дотроо зогсоол агуулж байвал огт үгүй.
  const isParentItself = (editing?.child_site_count || 0) > 0
  const parentOptions = isParentItself ? [] : sites.filter(
    (s) => s.id !== editing?.id && !s.parent_site_id)
  const parentName = sites.find((s) => s.id === editing?.parent_site_id)?.name
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
          {/* Хаалт онгорхой гацвал зогсоол хяналтгүй болж машин төлбөргүй гарна
              (2026-08-09 NIC). Мэдрэгч ажиллахгүй байх нь ховор биш тул сервер
              талаас давхар хамгаалалт болгож тогтмол «хаах» команд илгээнэ. */}
          <Field label="Гарах хаалтыг тогтмол хаах (минут)">
            <input className="input" type="number" min="0" placeholder="0 = унтраалттай"
              value={editing.barrier_close_sweep_min ?? ''}
              onChange={(e) => setEditing({ ...editing, barrier_close_sweep_min: e.target.value })} />
            <div className="text-[11px] text-slate-500 mt-1">
              Хаалт онгорхой гацвал (мэдрэгч ажиллаагүй, албадан нээсэн г.м) энэ минут
              тутам «хаах» команд илгээж хэвийн байдалд оруулна. Хөдөлгөөнтэй үед
              ХЭЗЭЭ Ч хаахгүй: сүүлийн 5 минутад гарах уншилт, хаалтны команд болоогүй,
              төлбөр хүлээж буй машин байхгүй үед л ажиллана. 30 эсвэл 60 гэж өгвөл
              хангалттай. Хоосон/0 = унтраалттай.
            </div>
          </Field>
          {/* Хаалттай зогсоол (Monnis ажилчдын зогсоол г.м): бүртгэлгүй машинд
              орох хаалт автоматаар нээгдэхгүй, самбарт мэдэгдэл гарна. */}
          <Field label="Хаалттай зогсоол">
            <label className="flex items-center gap-2 cursor-pointer select-none py-2">
              <input type="checkbox" className="w-4 h-4 accent-accent"
                checked={!!editing.registered_only}
                onChange={(e) => setEditing({ ...editing, registered_only: e.target.checked })} />
              <span className="text-sm">Зөвхөн бүртгэлтэй машин нэвтэрнэ</span>
            </label>
            <div className="text-[11px] text-slate-500 mt-1">
              Идэвхжүүлбэл «Бүртгэлтэй машин» жагсаалтад байгаа машинд л орох хаалт
              нээгдэнэ. Бусад машинд хаалт нээгдэхгүй, дэлгэцэнд «Бүртгэлгүй машин»
              гэж гарна.
            </div>
          </Field>

          {/* Nested (дамжин) зогсоол: том зогсоолын ДОТОР байрлах жижиг зогсоол.
              Машин дотор байх хугацаанд ГАДНА зогсоолын төлбөрийн тоолуур зогсоно. */}
          <details className="rounded-lg border border-slate-700 px-3 py-2"
            open={!!editing.parent_site_id}>
            <summary className="cursor-pointer text-sm font-medium py-1">
              Дамжин зогсоол (энэ зогсоол өөр зогсоолын дотор байна)
              {editing.parent_site_id
                ? <span className="ml-2 text-xs text-accent">· {parentName || 'холбогдсон'}</span>
                : <span className="ml-2 text-xs text-slate-500">· тохируулаагүй</span>}
            </summary>
            <div className="mt-2 space-y-3">
              <Field label="Гадна (эцэг) зогсоол">
                <select className="input" value={editing.parent_site_id || ''} disabled={isParentItself}
                  onChange={(e) => setEditing({ ...editing, parent_site_id: e.target.value || null })}>
                  <option value="">— Энгийн бие даасан зогсоол —</option>
                  {parentOptions.map((s) => (
                    <option key={s.id} value={s.id}>{s.name} ({s.site_code})</option>
                  ))}
                </select>
                {isParentItself ? (
                  <div className="text-[11px] text-amber-400 mt-1">
                    Энэ зогсоол дотроо {editing.child_site_count} зогсоол агуулж байна —
                    өөрөө өөр зогсоолын дотор орох боломжгүй (хоёр давхар үүрлэлт).
                  </div>
                ) : (
                  <div className="text-[11px] text-slate-500 mt-1">
                    Сонгосон үед: машин ЭНЭ зогсоолд байх хугацаанд гадна зогсоолын
                    төлбөр гүйхгүй. Гадна зогсоолоор дамжиж ороогүй машины төлбөр
                    энгийнээр бодогдоно. Жагсаалтад аль хэдийн өөр зогсоолын дотор
                    байгаа зогсоол харагдахгүй.
                  </div>
                )}
              </Field>
              {editing.parent_site_id && (
                <Field label="Дамжин зогсох дээд хугацаа (цаг)">
                  <input className="input" type="number" min="0" placeholder="4 (default)"
                    value={editing.transit_max_hours ?? ''}
                    onChange={(e) => setEditing({ ...editing, transit_max_hours: e.target.value })} />
                  <div className="text-[11px] text-slate-500 mt-1">
                    Энэ зогсоолын ГАРАХ камерт уншигдалгүй үлдвэл гадна талын тоолуур
                    мөнхөд зогсож машин 0₮-өөр гарах эрсдэлтэй. Тиймээс тоолуур зогсох
                    хугацааг таслана — түүнээс хэтэрсэн хугацаа гадна талд төлбөртэй.
                    Хоосон = 4 цаг, 0 = хязгааргүй (болгоомжтой).
                  </div>
                </Field>
              )}
            </div>
          </details>

          {/* Дотоод/ажилчдын зогсоол — цаг тооцохгүй. Дамжин зогсоолд ихэвчлэн
              хамт асаана: доторх зогсоол өөрөө төлбөр авахгүй. */}
          <Field label="Төлбөр авахгүй зогсоол">
            <label className="flex items-center gap-2 cursor-pointer select-none py-2">
              <input type="checkbox" className="w-4 h-4 accent-accent"
                checked={!!editing.no_charge}
                onChange={(e) => setEditing({ ...editing, no_charge: e.target.checked })} />
              <span className="text-sm">Энэ зогсоол цаг тооцохгүй, төлбөр авахгүй</span>
            </label>
            <div className="text-[11px] text-slate-500 mt-1">
              Ажилчдын/дотоод зогсоолд. Бүх машин 0₮-өөр гарна (тариф хамаарахгүй),
              зогсолт нь тайланд бүртгэгдсэн хэвээр.
            </div>
          </Field>

          {/* Дансаар (шилжүүлгээр) төлбөр хүлээн авах данс — online operator-ын
              кассын «Дансаар» товчны хажууд харагдаж, жолоочид хэлж өгнө. */}
          <details className="rounded-lg border border-slate-700 px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium py-1">
              Шилжүүлэг хүлээн авах данс (Дансаар төлбөр)
              {editing.bank_account
                ? <span className="ml-2 text-xs text-accent">· {editing.bank_name} {editing.bank_account}</span>
                : <span className="ml-2 text-xs text-slate-500">· тохируулаагүй</span>}
            </summary>
            <div className="grid grid-cols-2 gap-3 mt-2">
              <Field label="Банк">
                <input className="input" value={editing.bank_name || ''} placeholder="жишээ: Хаан банк"
                  onChange={(e) => setEditing({ ...editing, bank_name: e.target.value })} />
              </Field>
              <Field label="Дансны дугаар">
                <input className="input font-mono" value={editing.bank_account || ''} placeholder="жишээ: 5123456789"
                  onChange={(e) => setEditing({ ...editing, bank_account: e.target.value })} />
              </Field>
              <Field label="Данс эзэмшигч">
                <input className="input" value={editing.bank_account_name || ''} placeholder="жишээ: Моннис Пропертис ХХК"
                  onChange={(e) => setEditing({ ...editing, bank_account_name: e.target.value })} />
              </Field>
            </div>
            <div className="text-[11px] text-slate-500 mt-1.5">
              «Дансаар» эрхтэй (pay_transfer) оператор кассаас энэ дансыг жолоочид хэлж,
              шилжүүлэг орж ирснийг хуулгаас шалгаад төлбөрийг баталгаажуулна.
            </div>
          </details>

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
