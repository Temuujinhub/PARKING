# Smart Parking MN 🅿️

Монголын зах зээлд зориулсан ухаалаг төлбөрт зогсоолын бүрэн систем.
Dahua ANPR камер + DZBL-A barrier + QPay + PAX POS + e-Barimt интеграцтай.

**Live:** http://152.42.235.199 · **API docs:** http://152.42.235.199/api/docs

## Урсгал

```
ОРОХ:  Камер дугаар уншина → session нээгдэнэ → хаалт нээгдэнэ
ГАРАХ: Камер дугаар уншина → төлбөр тооцоологдоно
       ├─ Үнэгүй хугацаанд / гэрээт → шууд нээгдэнэ
       ├─ QR → утсаараа /pay хуудас → QPay → нээгдэнэ
       ├─ Касс → бэлэн мөнгө / QPay → нээгдэнэ
       └─ PAX A9000 → банкны карт → нээгдэнэ
       Төлбөр бүрд НӨАТ 10% + e-Barimt автоматаар
```

## Бүтэц

```
backend/    FastAPI + PostgreSQL — API, billing, QPay, e-Barimt, WebSocket
frontend/   React + Vite + Tailwind — админ dashboard + /pay хуудас
docs/       API лавлах, Mobile/PAX хөгжүүлэгчийн заавар, hardware заавар
deploy/     nginx + systemd тохиргооны хуулбар
```

## Эрхийн түвшин

| Role | Эрх |
|---|---|
| SUPER_ADMIN | Бүгд + хэрэглэгчийн удирдлага |
| ADMIN | Тохиргоо (зогсоол, тариф, төхөөрөмж) + бүх үйл ажиллагаа |
| FINANCE | Тайлан, төлбөр, НӨАТ |
| OPERATOR | Касс, шалгах, түүх, хаалт нээх |

## Тариф

Шатлалтай (60мин→1000₮, 120мин→2000₮…), эхний N минут үнэгүй, төлбөрийн дараах
гарах хугацаа (grace), хоногийн дээд хязгаар, хөнгөлөлт (хувь/дүн/минут),
гэрээт жолооч (сарын эрх) — бүгд **Тохиргоо** хуудаснаас удирдана.

## Deploy (сервер дээр аль хэдийн хийгдсэн)

```bash
# Backend
cd backend && python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python -m app.seed          # анхны өгөгдөл
systemctl enable --now parking-backend

# Frontend
cd frontend && npm install && npm run build
cp -r dist/* /var/www/parking/

# Nginx: deploy/nginx.conf → /etc/nginx/sites-available/parking
```

Орчны тохиргоо: `backend/.env` (QPay, e-Barimt, barrier холболт).
Дэлгэрэнгүй: [docs/HARDWARE_SETUP.md](docs/HARDWARE_SETUP.md)

## Хөгжүүлэгчдэд

- [docs/API_REFERENCE.md](docs/API_REFERENCE.md) — бүх endpoint
- [docs/MOBILE_APP_GUIDE.md](docs/MOBILE_APP_GUIDE.md) — жолоочийн mobile апп
- [docs/PAX_POS_APP_GUIDE.md](docs/PAX_POS_APP_GUIDE.md) — PAX A9000 картын терминал
