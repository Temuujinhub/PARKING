# Mobile апп хөгжүүлэгчийн заавар

Жолоочид зориулсан mobile апп (iOS/Android) хөгжүүлэхэд зориулсан заавар.
Одоогоор жолоочийн үндсэн урсгал **web (/pay хуудас)**-аар бүрэн ажилладаг тул mobile апп нь
нэмэлт боломжуудыг (түүх, сарын эрх, push мэдэгдэл) өгөх зорилготой.

## 1. Архитектурын байр суурь

```
[Mobile App] ──HTTPS──► /api/public/*  (нэвтрэлтгүй хэсэг)
             ──HTTPS──► /api/*         (хэрэглэгчийн бүртгэлтэй хэсэг — Phase 2)
             ──WSS────► /ws/sites/*    (real-time төлөв)
```

API бүрэн лавлах: [API_REFERENCE.md](API_REFERENCE.md)

## 2. Гол урсгал — зогсоолын төлбөр төлөх

```
1. Хэрэглэгч машины дугаараа апп-д бүртгэнэ (local storage)
2. Зогсоол сонгох / QR уншуулах → site_code
3. GET /api/public/sessions?plate={дугаар}&site={site_code}
   → session_id, total_fee, duration_minutes
4. POST /api/payments/qpay/invoice {"session_id": "..."}
   → deep_link (qpay://...), qr_text, payment_id
5. deep_link-ээр QPay апп руу шилжүүлнэ (иOS: canOpenURL, Android: Intent)
6. Апп руу буцаж ирмэгц POST /api/payments/qpay/check/{payment_id}
   5 сек тутам, дээд тал нь 3 минут polling
7. status=PAID → "Хаалт нээгдлээ, сайн замаараа!" дэлгэц
```

### Жишээ (pseudo-код, React Native / Flutter аль алинд тохирно)

```dart
// 1. Session хайх
final s = await api.get('/api/public/sessions', query: {'plate': plate, 'site': siteCode});
if (s['is_free']) return showFreeExit(s['free_reason']);

// 2. Invoice
final inv = await api.post('/api/payments/qpay/invoice', body: {'session_id': s['session_id']});

// 3. QPay руу шилжих
await launchUrl(Uri.parse(inv['deep_link']));  // qpay://q?invoice=...

// 4. Polling
final timer = Timer.periodic(Duration(seconds: 5), (t) async {
  final st = await api.post('/api/payments/qpay/check/${inv['payment_id']}');
  if (st['status'] == 'PAID') { t.cancel(); showSuccess(); }
});
```

## 3. UX шаардлага

- Дугаар оруулах: кирилл том үсэг + тоо, автоматаар uppercase, зайг арилгана
- Сүүлд ашигласан дугааруудыг хадгалж нэг товшилтоор сонгоно
- Төлбөрийн задаргаа: орсон цаг, хугацаа, үндсэн дүн, хөнгөлөлт, НӨАТ, нийт — бүгд харагдана
- `is_free=true` үед төлбөрийн товч нуугдаж "Шууд гарна уу" харагдана
- Төлсний дараа `grace_minutes` (default 15 мин) дотор гарахыг сануулна
- Offline үед сүүлийн session-ийг cache-ээс харуулж, "Интернэт шаардлагатай" гэж анхааруулна
- Touch target ≥ 44×44pt, dark mode дэмжинэ

## 4. Push мэдэгдэл (Phase 2, серверт нэмэлт хөгжүүлэлт шаардана)

Төлөвлөсөн event-үүд: төлбөр амжилттай, гарах хугацаа дуусах дөхөж буй, сарын эрх сунгах сануулга.

## 5. Хэрэглэгчийн бүртгэл (Phase 2)

Одоогийн API нэвтрэлтгүй public урсгалыг бүрэн дэмжинэ. Утасны дугаараар OTP бүртгэл,
төлбөрийн түүх, сарын эрх худалдан авалт зэрэг нь backend-д нэмэлт endpoint шаардана —
backend багтай хамтарч тодорхойлно.

## 6. Туршилтын орчин

- Base URL: `https://test.easy-parking.mn`
- Туршилтын машин оруулах: `POST /api/lpr/simulate {"device_key": "cam-entry-site01", "plate": "ТАНЫДУГААР"}`
- Гарах: `{"device_key": "cam-exit-site01", ...}`
- QPay MOCK горимд: invoice үүсгэсний дараа `POST /api/payments/qpay/webhook?payment_id={id}`
  body `{"payment_status": "PAID", "amount": {дүн}}` дуудвал төлөгдсөнтэй адилхан болно.
