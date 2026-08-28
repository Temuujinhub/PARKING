"""QPay v2 merchant интеграц + Ebarimt 3.0 (merchant.qpay.mn).

Урсгал (QR төлбөр + e-Barimt):
  1. POST /v2/invoice — НӨАТ-ийн мэдээлэлтэй нэхэмжлэхийн кодоор (EB_..._INVOICE),
     lines (бүтээгдэхүүн бүрээр задлан), tax_type, district_code-той нэхэмжлэл үүсгэнэ
     → invoice_id, qr_text, qr_image, urls (банкны deeplink-ууд).
  2. Жолооч QR-ийг банкны/QPay апп-аар уншиж төлнө.
  3. QPay callback (GET, "SUCCESS" буцаана) ЭСВЭЛ POST /v2/payment/check-ээр төлөгдсөнийг
     баталгаажуулж g_payment_id (QPay-ийн payment_id)-г авна.
  4. POST /v2/ebarimt_v3/create — payment_id + ebarimt_receiver_type-аар e-Barimt үүсгэнэ
     → ebarimt_qr_data (QR болгон хэвлэнэ), ebarimt_lottery (сугалаа), ebarimt_receipt_id (ДДТД).

Endpoint-ууд (docs — 2026.3.17 V2 API with Ebarimt 3.0):
  POST /v2/auth/token          — Basic auth → access_token, refresh_token, expires_in
  POST /v2/auth/refresh        — Bearer refresh_token → шинэ access_token
  POST /v2/invoice             — нэхэмжлэл үүсгэх → invoice_id, qr_text, qr_image, urls
  POST /v2/payment/check       — төлбөр шалгах → count, paid_amount, rows[].payment_id
  GET  /v2/payment/{id}        — төлбөрийн дэлгэрэнгүй
  POST /v2/ebarimt_v3/create   — e-Barimt үүсгэх → id, ebarimt_qr_data, ebarimt_lottery, ...
  DELETE /v2/ebarimt_v3/{id}   — e-Barimt цуцлах

qpay_mock=True үед бодит QPay руу хандахгүй — туршилтын QR/invoice/ebarimt буцаана.
Бодит: PARKING_QPAY_MOCK=false, PARKING_QPAY_SANDBOX (true/false),
       PARKING_QPAY_USERNAME/PASSWORD/INVOICE_CODE.
"""
import asyncio
import base64
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

import httpx

from ..config import settings

log = logging.getLogger("parking.qpay")

# ─────────────── Найдвартай байдлын тохиргоо ───────────────
# QPay унах нь ЖОЛООЧИД шууд харагддаг (QR гарахгүй) тул нэг удаагийн саатал,
# хүчингүй болсон токен зэргийг ДОТООДОО даван туулна.
_MAX_ATTEMPTS = 3                                    # эхнийх + 2 давталт
_BACKOFF_SEC = (0.4, 1.2)                            # давталт хоорондын хүлээлт
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}  # түр зуурын алдаанууд
# Токеныг QPay-ийн хэлсэн хугацаанаас үл хамааран энэ хугацаанаас удаан
# кэшлэхгүй (доорх `_parse_expiry`-ийн тайлбарыг үз).
TOKEN_MAX_LIFETIME = timedelta(minutes=50)
# QPay v2-ийн `sender_invoice_no` талбарын дээд урт.
SENDER_INVOICE_NO_MAX = 45


def fit_bytes(text: str, budget: int) -> str:
    """UTF-8-аар `budget` байтад багтахаар ТЭМДЭГТЭЭР тайрна (тэмдэгт хагалахгүй).

    Монгол дугаарын кирилл үсэг бүр 2 байт эзэлдэг тул зөвхөн `len()`-ээр
    хэмжвэл хязгаараас халих эрсдэлтэй. QPay-ийн хэмжих нэгж (тэмдэгт үү, байт
    уу) баримтжаагүй тул хоёуланг нь хангахын тулд байтаар барина."""
    if budget <= 0:
        return ""
    while text and len(text.encode()) > budget:
        text = text[:-1]
    return text


@dataclass(frozen=True)
class QpayAccount:
    """Нэг QPay мерчант данс. Зогсоол бүр өөрийн гэрээтэй байж болно — төлбөр нь
    тухайн түрээслэгчийн данс руу орж, e-Barimt нь тэдний ТТД-ээр үүснэ."""
    username: str
    password: str
    invoice_code: str
    branch_code: str
    district_code: str
    tax_type: str
    classification_code: str
    base_url: str
    mock: bool

    @property
    def cache_key(self) -> tuple[str, str]:
        return (self.base_url, self.username)


def global_account() -> QpayAccount:
    """.env-ийн глобал QPay данс (өөрийн данс тохируулаагүй зогсоолуудад)."""
    return QpayAccount(
        username=settings.qpay_username,
        password=settings.qpay_password,
        invoice_code=settings.qpay_invoice_code,
        branch_code=settings.qpay_branch_code,
        district_code=settings.qpay_district_code,
        tax_type=settings.qpay_tax_type,
        classification_code=settings.qpay_classification_code,
        base_url=settings.qpay_base_url,
        mock=settings.qpay_mock,
    )


def _tenant_of(site):
    """Зогсоолын түрээслэгчийн бичлэг (байхгүй/session-гүй бол None)."""
    tid = getattr(site, "tenant_id", None)
    if not tid:
        return None
    from sqlalchemy.orm import object_session
    db = object_session(site)
    if db is None:
        return None
    from ..models import Tenant
    return db.get(Tenant, tid)


def account_for(site) -> QpayAccount:
    """Зогсоолын QPay данс — гурван шатлалтай: ЗОГСООЛ (онцгой override) →
    ТҮРЭЭСЛЭГЧ (үндсэн байрлал: Тохиргоо → Түрээслэгч, бүх зогсоолд нь үйлчилнэ)
    → глобал .env. Талбар тус бүр тусад нь уналт хийдэг тул зөвхөн дүүргийн
    кодоо өөрчлөх гэх мэт хэсэгчилсэн тохиргоо бас болно.

    ЧУХАЛ: өөрийн данстай (зогсоол/түрээслэгчийн аль нэгд) үед mock=False —
    глобал mock=true байсан ч бодит гэрээний төлбөрийг хуурамчаар боловсруулахгүй."""
    g = global_account()
    if site is None:
        return g
    from ..secretbox import decrypt_secret
    ten = _tenant_of(site)

    def _f(field):
        """Талбарын утга: зогсоол → түрээслэгч → None."""
        v = (getattr(site, field, None) or "").strip()
        if not v and ten is not None:
            v = (getattr(ten, field, None) or "").strip()
        return v or None

    # Нэвтрэх хос НЭГ шатлалаас бүтнээрээ ирнэ (зогсоолын нэр + түрээслэгчийн
    # нууц үг хольж болохгүй — мөнгө буруу данс руу орох эрсдэл)
    user = (getattr(site, "qpay_username", None) or "").strip()
    pwd = decrypt_secret((getattr(site, "qpay_password", None) or "").strip())
    if not (user and pwd) and ten is not None:
        user = (getattr(ten, "qpay_username", None) or "").strip()
        pwd = decrypt_secret((getattr(ten, "qpay_password", None) or "").strip())
    if not (user and pwd):
        # Хэсэгчилсэн тохиргоо (зөвхөн дүүрэг/салбар) — данс нь глобал хэвээр
        return QpayAccount(
            username=g.username, password=g.password,
            invoice_code=(_f("qpay_invoice_code") or g.invoice_code),
            branch_code=(_f("qpay_branch_code") or g.branch_code),
            district_code=(_f("qpay_district_code") or g.district_code),
            tax_type=g.tax_type, classification_code=g.classification_code,
            base_url=g.base_url, mock=g.mock,
        )
    return QpayAccount(
        username=user, password=pwd,
        invoice_code=(_f("qpay_invoice_code") or g.invoice_code),
        branch_code=(_f("qpay_branch_code") or g.branch_code),
        district_code=(_f("qpay_district_code") or g.district_code),
        tax_type=g.tax_type, classification_code=g.classification_code,
        base_url=g.base_url, mock=False,
    )


# Токены cache — мерчант данс бүрд ТУСДАА (нэг процесст олон гэрээ зэрэг ажиллана).
# QPay-ийн заавар: токеныг хугацаанд нь нэг л удаа авч, дуусах хүртэл дахин ашиглана.
_tokens: dict[tuple[str, str], dict] = {}


def _cache(acc: QpayAccount) -> dict:
    return _tokens.setdefault(acc.cache_key,
                              {"access": None, "refresh": None, "access_exp": datetime.min})


async def _auth_basic(acc: QpayAccount) -> dict:
    """POST /v2/auth/token — client_id:client_secret Basic auth.

    Нэвтрэлт унавал нэхэмжлэл ОГТ үүсэхгүй тул сүлжээний түр зуурын саатал,
    QPay-ийн 5xx-д богино хүлээлттэйгээр дахин оролдоно. Нэр/нууц үг буруу
    (401) бол давтах утгагүй — шууд дээшээ."""
    basic = base64.b64encode(f"{acc.username}:{acc.password}".encode()).decode()
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{acc.base_url}/auth/token",
                                         headers={"Authorization": f"Basic {basic}"})
        except httpx.HTTPError as e:
            if attempt >= _MAX_ATTEMPTS:
                raise
            log.warning("QPay нэвтрэлт сүлжээний алдаа (%d/%d, %s): %r",
                        attempt, _MAX_ATTEMPTS, acc.username, e)
            await asyncio.sleep(_backoff(attempt))
            continue
        if resp.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS:
            log.warning("QPay нэвтрэлт → HTTP %s (%s): %d/%d дахин оролдоно",
                        resp.status_code, acc.username, attempt, _MAX_ATTEMPTS)
            await asyncio.sleep(_backoff(attempt))
            continue
        resp.raise_for_status()
        return resp.json()


async def _auth_refresh(acc: QpayAccount) -> dict:
    """POST /v2/auth/refresh — Bearer refresh_token."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{acc.base_url}/auth/refresh",
                                 headers={"Authorization": f"Bearer {_cache(acc)['refresh']}"})
        resp.raise_for_status()
        return resp.json()


def _backoff(attempt: int) -> float:
    return _BACKOFF_SEC[min(attempt, len(_BACKOFF_SEC)) - 1]


def invalidate_token(acc: QpayAccount) -> None:
    """Кэшлэсэн токеныг хаяна — QPay 401 буцаасан нь «энэ токеныг би аль хэдийн
    хүчингүй болгосон» гэсэн үг тул кэшэнд хадгалах нь зөвхөн хор хөнөөлтэй."""
    _tokens.pop(acc.cache_key, None)


async def _get_token(acc: QpayAccount | None = None, force: bool = False) -> str:
    """Хүчинтэй access_token буцаана. Дуусах дөхсөн бол refresh, боломжгүй бол дахин auth.

    force=True үед кэш БОЛОН refresh_token-ыг үл тоомсорлож, дансны нэр/нууц
    үгээр ШИНЭЭР нэвтэрнэ. 401-ийн дараа зөвхөн энэ л арга сэргээнэ: хүчингүй
    болсон access-ийн хамт refresh нь ч хүчингүй болсон байдаг тул refresh-ээр
    оролдвол дахин 401 авч, гогцоо үргэлжилнэ."""
    acc = acc or global_account()
    if force:
        invalidate_token(acc)
    tok = _cache(acc)
    now = datetime.utcnow()
    if not force and tok["access"] and tok["access_exp"] > now:
        return tok["access"]
    if force:
        data = await _auth_basic(acc)
    else:
        try:
            data = await _auth_refresh(acc) if tok["refresh"] else await _auth_basic(acc)
        except Exception:
            data = await _auth_basic(acc)  # refresh амжилтгүй бол шинээр
    tok["access"] = data["access_token"]
    tok["refresh"] = data.get("refresh_token", tok["refresh"])
    tok["access_exp"] = _parse_expiry(data.get("expires_in"), now)
    return tok["access"]


# Данс тус бүрийн эрүүл мэндийн тоолуур — health хуудас болон watchdog уншина.
_stats: dict[tuple[str, str], dict] = {}


def _stat(acc: QpayAccount) -> dict:
    return _stats.setdefault(acc.cache_key, {
        "username": acc.username, "ok": 0, "fail": 0, "consecutive_fail": 0,
        "last_error": "", "last_error_at": None, "last_ok_at": None,
    })


def health_snapshot() -> list[dict]:
    """Мерчант данс бүрийн сүүлийн үеийн байдал (health endpoint уншина).
    consecutive_fail > 0 гэдэг нь тухайн дансаар QR үүсэхгүй байна гэсэн үг."""
    out = []
    for st in _stats.values():
        out.append({
            "username": st["username"], "ok": st["ok"], "fail": st["fail"],
            "consecutive_fail": st["consecutive_fail"],
            "last_error": st["last_error"][:200],
            "last_error_at": st["last_error_at"].isoformat() if st["last_error_at"] else None,
            "last_ok_at": st["last_ok_at"].isoformat() if st["last_ok_at"] else None,
        })
    return sorted(out, key=lambda r: -r["consecutive_fail"])


async def _api(method: str, path: str, acc: QpayAccount, *,
               json: dict | None = None, timeout: float = 20.0) -> httpx.Response:
    """QPay API руу нэг дуудлага — токен, 401 сэргээлт, түр зуурын алдааны давталттай.

    ЯАГААД (2026-08-28): өмнө нь дуудлага бүр «токен ав → НЭГ УДАА POST →
    raise_for_status» байсан. Хоёр нүх байв:

      1. **401 үхлийн гогцоо.** QPay нэг мерчант дансанд нэг л access_token
         амьд байлгадаг. Хэд хэдэн сервер (TEST + зогсоол бүрийн PROD) НЭГ
         дансаар ажилладаг тул аль нэг нь шинэ токен авмагц бусдын кэшлэсэн
         токен QPay талд ҮХНЭ. Кэш нь дуусах хугацаагаараа (QPay `expires_in`
         epoch-оор ~24ц) хүчинтэй мэт харагдсаар байдаг тул дараагийн БҮХ
         нэхэмжлэл 401 болж, тухайн серверийн БҮХ зогсоол дээр «QPay-тэй
         холбогдож чадсангүй» гэж QR ОГТ үүсэхгүй болно — backend-ийг гараар
         restart хийх (эсвэл 24ц өнгөрөх) хүртэл.
      2. **Түр зуурын саатал = алдаа.** QPay-ийн 502/504 эсвэл нэг timeout
         шууд жолоочийн нүүрэн дээр гарч, кассын дараалал үүсгэдэг байв.

    Одоо: 401/403 → токеныг хаяж, ШИНЭЭР нэвтэрч дахин илгээнэ (нэг дуудлагын
    дотор, жолооч мэдэхгүй). 408/425/429/5xx/сүлжээний алдаа → богино
    хүлээлттэйгээр дахин илгээнэ. Бусад 4xx (ж: VAT_AMOUNT_INVALID) нь БОДИТ
    алдаа тул давтахгүй — шууд дээшээ гаргана."""
    url = f"{acc.base_url}{path}"
    st = _stat(acc)
    force_auth = False
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            token = await _get_token(acc, force=force_auth)
        except Exception as e:
            st["fail"] += 1
            st["consecutive_fail"] += 1
            st["last_error"] = f"нэвтрэлт: {type(e).__name__}: {e}"
            st["last_error_at"] = datetime.utcnow()
            raise
        force_auth = False
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, json=json,
                                            headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            if attempt >= _MAX_ATTEMPTS:
                st["fail"] += 1
                st["consecutive_fail"] += 1
                st["last_error"] = f"{method} {path}: {type(e).__name__}: {e}"
                st["last_error_at"] = datetime.utcnow()
                raise
            log.warning("QPay %s %s сүлжээний алдаа (%d/%d, %s): %r",
                        method, path, attempt, _MAX_ATTEMPTS, acc.username, e)
            await asyncio.sleep(_backoff(attempt))
            continue
        if resp.status_code in (401, 403) and attempt < _MAX_ATTEMPTS:
            log.warning("QPay %s %s → HTTP %s (%s): токен хүчингүй болсон — "
                        "шинээр нэвтэрч дахин илгээнэ", method, path,
                        resp.status_code, acc.username)
            force_auth = True
            continue
        if resp.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS:
            log.warning("QPay %s %s → HTTP %s (%s): түр зуурын алдаа — %d/%d дахин оролдоно",
                        method, path, resp.status_code, acc.username, attempt, _MAX_ATTEMPTS)
            await asyncio.sleep(_backoff(attempt))
            continue
        if resp.status_code >= 400:
            st["fail"] += 1
            st["consecutive_fail"] += 1
            st["last_error"] = f"{method} {path}: HTTP {resp.status_code} {resp.text[:200]}"
            st["last_error_at"] = datetime.utcnow()
            # Дараалсан бүтэлгүйтэл = тухайн дансаар ХЭН Ч төлж чадахгүй байна.
            # Ганц алдаа биш, ХЭВ ШИНЖ болмогц лог дээр тод анхааруулна (Health
            # хуудас мөн улаан болно) — жолооч гомдол мэдүүлэхээс өмнө барихын тулд.
            if st["consecutive_fail"] in (3, 10, 50):
                log.error("QPay данс «%s» ДАРААЛАН %d удаа унав — энэ дансны бүх "
                          "зогсоол дээр QR үүсэхгүй байна. Сүүлийн алдаа: %s",
                          acc.username, st["consecutive_fail"], st["last_error"])
            resp.raise_for_status()
        st["ok"] += 1
        st["consecutive_fail"] = 0
        st["last_ok_at"] = datetime.utcnow()
        return resp
    raise RuntimeError("_api: давталт дуусав")  # pragma: no cover


def _parse_expiry(raw, now: datetime) -> datetime:
    """QPay-ийн expires_in нь баримт бичигт «секунд» гэсэн ч бодит хариунд Unix
    epoch (жишээ: 1785561353) ирдэг — секунд гэж уншвал кэш «56 жил хүчинтэй»
    болж, QPay талд токен дуусмагц бүх дуудлага 401 «Хандах эрхгүй байна» болдог
    (production дээр өдөр бүр гардаг байсан гацаа). Хоёр хэлбэрийг хоёуланг нь
    дэмжиж, 60с аюулгүйн зайтай; уншигдахгүй бол 1 цаг гэж үзнэ."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = 3600
    if val > 1e9:  # Unix epoch timestamp
        exp = datetime.utcfromtimestamp(val)
    else:  # харьцангуй секунд
        exp = now + timedelta(seconds=val)
    # 60с аюулгүйн зай + ДЭЭД ХЯЗГААР. QPay «24 цаг хүчинтэй» гэж хэлдэг ч нэг
    # дансыг олон сервер хуваалцахад хуучин токеныг ЧИМЭЭГҮЙ хүчингүй болгодог.
    # Хязгаар нь хамгийн муудаа 50 минутын дотор өөрөө эдгээх хоёр дахь давхарга
    # (эхнийх нь `_api`-ийн 401 сэргээлт — тэр нь эхний хүсэлт дээрээ засна).
    return min(exp - timedelta(seconds=60), now + TOKEN_MAX_LIFETIME)


def _vat_units(price: float) -> int:
    """НӨАТ-ыг 1/10000 нэгжээр, БҮХЭЛ тоогоор. 4 орны нарийвчлалтай ТАСЛАНА
    (round биш truncate — QPay-ийн docs жишээ).

    Тооцоог ЗААВАЛ `Decimal`-аар хийнэ. float-оор бодоход нарийвчлалын алдаа
    ГАРЦААГҮЙ дүнг ч доогуур тавьдаг:
        11000 * 0.1 / 1.1 = 999.9999999999999  → тасалбал 999.9999
        зөв утга нь                              1000.0000
    Ингээд QPay `VAT_AMOUNT_INVALID` буцааж, QR ОГТ үүсдэггүй байв. Энэ нь
    11-т хуваагддаг БҮХ дүнд (1,100 / 5,500 / 11,000 / 22,000 …) тохиолддог —
    Хангарьд дээр 11,000₮ болмогц жолооч QR-аар төлж чадахгүй болсон нь энэ
    (2026-08-21, `MONNIS_PROPERTIES` дансаар туршиж баталсан)."""
    r = Decimal(str(settings.vat_rate))
    v = Decimal(str(price)) * r / (1 + r) * 10000
    return int(v.to_integral_value(rounding=ROUND_DOWN))


def _vat_of(price: float) -> float:
    """НӨАТ багтсан үнээс НӨАТ-ыг гаргана.
    QPay жишээ: 50₮ → 50*0.1/1.1 = 4.54545… → 4.5454 (round биш truncate)."""
    return _vat_units(price) / 10000


def build_lines(items: list[dict], acc: QpayAccount | None = None) -> list[dict]:
    """e-Barimt нэхэмжлэхийн lines-ийг бүтээгдэхүүн бүрээр байгуулна.

    items: [{"description", "unit_price", "quantity"(=1), "classification_code"(optional),
             "barcode"(optional), "note"(optional)}]
    tax_type=1 (НӨАТ тооцогдох) үед мөр бүрт VAT taxes нэмнэ. 2/3 үед VAT тооцохгүй.
    """
    acc = acc or global_account()
    vat_able = acc.tax_type == "1"

    # 0₮ мөрийг ОГТ илгээхгүй. Бодит тохиолдол: үнэгүй хугацаанд багтсан ч
    # ӨМНӨХ ӨРТЭЙ машинд «одоогийн төлбөр 0₮» + «өр N₮» гэсэн хоёр мөр үүсдэг
    # бөгөөд QPay 0 дүнтэй мөрийг татгалздаг → QR огт үүсэхгүй. 0₮ мөрийг хаях
    # нь нийт дүнг өөрчлөхгүй тул аюулгүй. Сөрөг дүн бол логикийн алдаа —
    # QPay руу илгээхээс өмнө энд барина.
    priced = []
    for it in items:
        price = round(float(it["unit_price"]), 2)
        qty = float(it.get("quantity", 1) or 1)
        total = round(price * qty, 4)
        if total < 0:
            raise ValueError(
                f"Нэхэмжлэлийн мөр сөрөг дүнтэй: {str(it.get('description'))[:60]} = {total}")
        if total == 0:
            log.info("QPay нэхэмжлэлээс 0₮ мөрийг хаялаа: %s",
                     str(it.get("description"))[:60])
            continue
        priced.append((it, price, qty, total))
    if not priced:
        raise ValueError("Нэхэмжлэлийн бүх мөр 0₮ — QPay нэхэмжлэл үүсгэх боломжгүй")
    items = [it for (it, _, _, _) in priced]
    totals = [t for (_, _, _, t) in priced]

    # QPay нь НӨАТ-ыг МӨР БҮРЭЭР шалгадаг — мөрийн дүнгээс өөрөө бодоод
    # илгээсэнтэй маань тулгана. Тиймээс мөр бүрийнхийг ТУСАД НЬ бодно.
    #
    # Өмнө нь мөрүүдийн нийлбэрийг НИЙТ дүнгийн НӨАТ-д таарган «үлдэгдлийг
    # хамгийн том мөрд» нэмдэг байв. Тэр нь нийлбэрийг зөв болгодог ч ТУХАЙН
    # МӨРИЙН утгыг буруу болгож QPay-гээр татгалзуулна. 2026-08-21-нд
    # `MONNIS_PROPERTIES` дансаар туршихад:
    #     мөр бүр тусад нь (Decimal)        → ✅ хүлээж авав
    #     нийлбэрт тэнцүүлж хуваарилсан     → ❌ VAT_AMOUNT_INVALID
    # Жинхэнэ шалтгаан нь тэнцвэржүүлэлт биш, `_vat_units`-ийн float алдаа
    # байсан — түүнийг Decimal болгож зассан.
    vat_units: list[int] = [_vat_units(t) for t in totals] if vat_able else []

    lines = []
    for idx, it in enumerate(items):
        price = round(float(it["unit_price"]), 2)
        qty = float(it.get("quantity", 1) or 1)
        line = {
            "tax_product_code": it.get("tax_product_code", ""),
            "line_description": it["description"][:255],
            "line_quantity": f"{qty:.2f}",
            "line_unit_price": f"{price:.2f}",
            "note": it.get("note", ""),
            "classification_code": it.get("classification_code") or acc.classification_code,
        }
        if it.get("barcode"):
            line["barcode"] = str(it["barcode"])
        if vat_able:
            line["taxes"] = [{
                "tax_code": "VAT",
                "description": "НӨАТ",
                "amount": vat_units[idx] / 10000,
                "note": "НӨАТ",
            }]
        lines.append(line)
    return lines


def qr_png_b64(text: str) -> str:
    """`qr_text`-ээс QR зургийг сервер дээр үүсгэж base64 PNG болгоно.

    ЯАГААД: QPay-ийн хариунд `qr_image` үе үе хоосон ирдэг бөгөөд тэр үед
    жолоочийн утсанд QR-ийн оронд түүхий текст гарч, төлбөр хийх боломжгүй
    болдог байв (2026-08-14 гомдол). QR-ийн агуулга нь `qr_text` дотор бүрэн
    байдаг тул зургийг өөрсдөө зурж болно."""
    if not text:
        return ""
    try:
        import base64
        import io
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
        qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=8, border=2)
        qr.add_data(text)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger("parking.qpay").warning(
            "QR зураг үүсгэж чадсангүй: %s: %s", type(e).__name__, e)
        return ""


def pick_qpay_deeplink(urls: list[dict]) -> str:
    """ЗӨВХӨН qPay хэтэвчний өөрийнх нь deeplink-ийг сонгоно. АНХААР: банк бүрийн
    линк "...://q?qPay_QRcode=..." хэлбэртэй тул "qpay" substring-ээр хайвал
    ЭХНИЙ ДУРЫН апп (ж: eBarimt) таарч утсан дээр буруу апп руу үсэргэдэг байсан —
    scheme (://-ийн өмнөх хэсэг) болон нэрээр нь шүүнэ. Тохирох нь олдоогүй бол
    хоосон буцаана — frontend автоматаар үсэргэхгүй, QR + жагсаалтаас сонгуулна."""
    for u in urls:
        link = u.get("link") or ""
        scheme = link.split("://", 1)[0].lower() if "://" in link else ""
        name = (u.get("name") or "").lower().replace(" ", "")
        if "qpay" in scheme or "qpay" in name:
            return link
    return ""


async def create_invoice(sender_invoice_no: str, description: str, receiver_code: str,
                         callback_url: str, lines: list[dict],
                         receiver_data: dict | None = None,
                         acc: QpayAccount | None = None) -> dict:
    """POST /v2/invoice — e-Barimt-тэй нэхэмжлэл үүсгэнэ (lines бүтээгдэхүүн бүрээр задлагдсан).

    Буцаах: invoice_id, qr_text, qr_image (base64 PNG), deep_link, urls (банкны жагсаалт)."""
    acc = acc or global_account()
    # QPay-ийн талбарын хязгаар — хэтэрвэл HTTP 400 `MAX_LENGTH` болж нэхэмжлэл
    # ОГТ үүсэхгүй (2026-08-28: «Их Монгол ресторан» кодын урт 49 тэмдэгт болж
    # тэр зогсоолын бүх жолооч QR-аар төлж чадахгүй байв). Дугаарыг энд ЧИМЭЭГҮЙ
    # тайрч болохгүй — DB-д хадгалсантай зөрвөл webhook тулгалт сална. Тиймээс
    # дуудагч тал (payments_router._invoice_no) баталгаажуулах ёстой; энэ бол
    # хэрэв тэр эвдэрвэл ЧИМЭЭГҮЙ өнгөрөхгүй байх хамгаалалт.
    if len(sender_invoice_no) > SENDER_INVOICE_NO_MAX:
        raise ValueError(
            f"sender_invoice_no {len(sender_invoice_no)} тэмдэгт — QPay-ийн "
            f"{SENDER_INVOICE_NO_MAX} тэмдэгтийн хязгаараас хэтэрлээ: {sender_invoice_no}")
    if acc.mock:
        mock_id = f"MOCK-INV-{uuid.uuid4().hex[:10].upper()}"
        return {"invoice_id": mock_id, "qr_text": f"https://qpay.mn/q/MOCK/{mock_id}",
                "qr_image": "", "deep_link": f"qpay://q?invoice={mock_id}", "urls": [], "mock": True}

    payload = {
        "invoice_code": acc.invoice_code,
        "sender_invoice_no": sender_invoice_no,
        "invoice_receiver_code": receiver_code or "terminal",
        "sender_branch_code": acc.branch_code,
        "invoice_description": description,
        "tax_type": acc.tax_type,
        "district_code": acc.district_code,
        "callback_url": callback_url,
        "lines": lines,
    }
    if receiver_data:
        payload["invoice_receiver_data"] = receiver_data
    resp = await _api("POST", "/invoice", acc, json=payload, timeout=20.0)
    data = resp.json()

    urls = data.get("urls") or []
    deep_link = pick_qpay_deeplink(urls)
    return {
        "invoice_id": data.get("invoice_id"),
        "qr_text": data.get("qr_text", ""),
        # QPay `qr_image`-ыг үе үе ХООСОН буцаадаг — тэр үед жолоочийн утсанд
        # QR-ийн оронд түүхий текст гардаг байв. qr_text-ээс өөрсдөө зурна.
        "qr_image": data.get("qr_image") or qr_png_b64(data.get("qr_text", "")),
        "deep_link": deep_link,
        "urls": urls,  # бүх банкны deeplink (нэр, лого, линк) — апп/веб сонголт харуулна
        "mock": False,
    }


async def check_payment(invoice_id: str, acc: QpayAccount | None = None) -> dict:
    """POST /v2/payment/check — invoice-ийн төлбөр төлөгдсөн эсэх (webhook ирээгүй үед polling).
    Хариу: paid, paid_amount, count, rows, payment_id (эхний төлбөрийн g_payment_id — ebarimt-д)."""
    acc = acc or global_account()
    if acc.mock:
        return {"paid": False, "mock": True}
    resp = await _api("POST", "/payment/check", acc, timeout=15.0,
                      json={"object_type": "INVOICE", "object_id": invoice_id,
                            "offset": {"page_number": 1, "page_limit": 100}})
    data = resp.json()
    paid_amount = float(data.get("paid_amount") or 0)
    rows = data.get("rows", []) or []
    # QPay-ийн payment_id — e-Barimt үүсгэхэд шаардлагатай (эхний амжилттай гүйлгээнээс)
    payment_id = None
    for row in rows:
        pid = row.get("payment_id") or row.get("id")
        if pid and (row.get("payment_status") or "PAID") == "PAID":
            payment_id = str(pid)
            break
    if not payment_id and rows:
        payment_id = str(rows[0].get("payment_id") or rows[0].get("id") or "")
    return {"paid": paid_amount > 0, "paid_amount": paid_amount,
            "count": int(data.get("count") or 0), "rows": rows,
            "payment_id": payment_id, "raw": data}


async def create_ebarimt(payment_id: str, receiver_type: str = "CITIZEN",
                         receiver: str | None = None,
                         district_code: str | None = None,
                         acc: QpayAccount | None = None) -> dict:
    """POST /v2/ebarimt_v3/create — төлөгдсөн төлбөр дээр e-Barimt баримт үүсгэнэ.

    payment_id: QPay-ийн g_payment_id (payment/check-ээс ирсэн).
    receiver_type: CITIZEN (иргэн) | COMPANY (ААН).
    receiver: CITIZEN үед ebarimt апп-д бүртгэлтэй утас (сонголт); COMPANY үед ААН регистр.

    Буцаах (стандартчилсан): {status, billId(=ebarimt_receipt_id), id, lottery, qrData, date, raw}."""
    acc = acc or global_account()
    if acc.mock:
        return _mock_ebarimt()

    payload = {"payment_id": payment_id, "ebarimt_receiver_type": receiver_type}
    if receiver:
        payload["ebarimt_receiver"] = receiver
    payload["district_code"] = district_code or acc.district_code
    resp = await _api("POST", "/ebarimt_v3/create", acc, json=payload, timeout=20.0)
    data = resp.json()
    return _normalize_ebarimt(data)


async def cancel_ebarimt(payment_id: str, note: str = "Гүйлгээ буцаав",
                         acc: QpayAccount | None = None) -> bool:
    """DELETE /v2/ebarimt_v3/{payment_id} — e-Barimt цуцлах.

    Шинэ спек (2026.3.17 V2 API with Ebarimt 3.0): path параметр нь БАРИМТ ҮҮСГЭСЭН
    ТӨЛБӨРИЙН payment_id (ebarimt id биш), body-д note заавал."""
    acc = acc or global_account()
    if acc.mock:
        return True
    try:
        resp = await _api("DELETE", f"/ebarimt_v3/{payment_id}", acc,
                          json={"note": note}, timeout=15.0)
    except httpx.HTTPError as e:
        log.warning("e-Barimt цуцлах амжилтгүй (%s): %r", payment_id, e)
        return False
    return resp.status_code in (200, 204)


async def cancel_payment(payment_id: str, acc: QpayAccount | None = None) -> bool:
    """DELETE /v2/payment/cancel/{id} — картын гүйлгээ цуцлах."""
    acc = acc or global_account()
    if acc.mock:
        return True
    try:
        resp = await _api("DELETE", f"/payment/cancel/{payment_id}", acc, timeout=15.0)
    except httpx.HTTPError as e:
        log.warning("QPay гүйлгээ цуцлах амжилтгүй (%s): %r", payment_id, e)
        return False
    return resp.status_code in (200, 204)


def _normalize_ebarimt(data: dict) -> dict:
    """QPay ebarimt_v3 хариуг локал ebarimt-тэй нэгэн ижил бүтэц рүү хөрвүүлнэ."""
    return {
        "status": "SUCCESS" if data.get("status", True) else "FAILED",
        # ДДТД (баримтын дугаар) — receipt_id, эс бол ebarimt ID
        "billId": data.get("ebarimt_receipt_id") or data.get("id"),
        "id": data.get("id"),
        "lottery": data.get("ebarimt_lottery") or data.get("lottery"),
        "qrData": data.get("ebarimt_qr_data") or data.get("qr_data"),
        "date": data.get("barimt_status_date") or data.get("ebarimt_status_date") or data.get("created_date"),
        "raw": data,
    }


def _mock_ebarimt() -> dict:
    """Туршилтын e-Barimt хариу (QPay холбогдоогүй үед)."""
    receipt_id = "".join(random.choices("0123456789", k=33))
    lottery = "".join(random.choices("АБВГДЕЁЖЗ", k=2)) + " " + "".join(random.choices("0123456789", k=8))
    qr = "".join(random.choices("0123456789", k=160))
    return {"status": "SUCCESS", "billId": receipt_id, "id": str(uuid.uuid4()),
            "lottery": lottery, "qrData": qr,
            "date": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"), "mock": True, "raw": {}}
