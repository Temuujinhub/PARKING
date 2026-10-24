// QR төлбөрийн хуудасны дугаар хайлтын туслах (2026-10-25).
//
// Продын лог (10-25 шөнө): «олдсонгүй» хайлтын 23-аас 18 нь ЗӨВХӨН ЦИФР («5583», «7063») —
// жолооч эхний тоогоо бичээд «хайх» дарахад жагсаалт гарахгүй шууд «олдсонгүй» гардаг байв
// (жагсаалт зөвхөн 4-өөс урт оруулгад санал болгогддог байсан). Мөн нэг машиныг 2 минутад
// 47 удаа хайсан (Enter дарж барих / олон товшилт) — давхар хүсэлтээс сэргийлнэ.

const PLATE_SEPARATORS = /[\s-]/g

/** Олдоогүй үед жагсаалт санал болгох угтвар: цифрээр эхэлсэн бол цифрийн эхний ≤4
 *  (камер үсгийг өөрөөр уншсан байж болно), эс бөгөөс эхний 4 тэмдэгт. 2-оос богино → null. */
export function fallbackPrefix(target) {
  const t = (target || '').toUpperCase().replace(PLATE_SEPARATORS, '')
  if (t.length < 2) return null
  const digits = (t.match(/^\d+/) || [''])[0]
  return digits.length >= 2 ? digits.slice(0, 4) : t.slice(0, 4)
}

/** Олдоогүй хайлтын дараах мессеж — жагсаалт байгаа эсэхээс хамаарна. */
export function fallbackMessage(target, count) {
  const t = (target || '').toUpperCase().replace(PLATE_SEPARATORS, '')
  if (count > 0) {
    return /^\d+$/.test(t)
      ? 'Доорх жагсаалтаас машинаа сонгоно уу.'
      : 'Яг энэ дугаараар олдсонгүй — камер үсгийг өөрөөр уншсан байж магадгүй. Доорх жагсаалтаас машинаа сонгоно уу.'
  }
  const q = fallbackPrefix(t) || t
  return `«${q}»-ээр эхэлсэн машин энэ зогсоолд бүртгэлгүй байна. Дугаараа шалгах эсвэл зогсоолын ажилтанд хандана уу.`
}

/** Нэг зэрэг ганц хүсэлт: `ref.current` үнэн байхад дахин дуудахыг алгасна (React useRef).
 *  Ажилласан бол true, алгассан бол false. */
export async function runOnce(ref, fn) {
  if (ref.current) return false
  ref.current = true
  try {
    await fn()
    return true
  } finally {
    ref.current = false
  }
}
