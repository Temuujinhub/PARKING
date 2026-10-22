// Гарц дээрх QR (/pay) — «Төлбөр төлөх» дарсны дараах төлбөрийн хэрэгслийн сонголт (2026-10-22).
// Дараалал тогтмол: 1. QPay 2. EasyWallet 3. SocialPay 4. MonPay 5. Ард апп.
// ready: false — холболт хараахан бэлэн биш: бүдэг, «Coming soon», дарагдахгүй. Хэтэвч бэлэн
// болоход Pay.jsx-д тэр хэрэгслийн урсгалыг (deeplink/нэхэмжлэл) бичээд ready: true болгоно.
export const PAY_METHODS = [
  { code: 'QPAY', name: 'QPay', hint: 'Банкны апп эсвэл QR-аар', badge: 'Q', color: 'bg-blue-600', ready: true },
  { code: 'EASYWALLET', name: 'EasyWallet', hint: 'Easy Parking хэтэвч', badge: 'EW', color: 'bg-emerald-600', ready: false },
  { code: 'SOCIALPAY', name: 'SocialPay', hint: 'Голомт банк', badge: 'SP', color: 'bg-sky-700', ready: false },
  { code: 'MONPAY', name: 'MonPay', hint: 'Мобифинанс', badge: 'MP', color: 'bg-orange-500', ready: false },
  { code: 'ARDAPP', name: 'Ард апп', hint: 'Ард', badge: 'A', color: 'bg-red-600', ready: false },
]
