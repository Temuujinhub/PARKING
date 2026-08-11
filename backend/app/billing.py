"""Төлбөр тооцооллын цөм.

Дүрэм:
  0. Зогсоол «төлбөр авахгүй» (no_charge) бол — 0₮.
  1. Бүртгэлтэй (гэрээт) жолооч хүчинтэй бол — 0₮.
  1.5 Доторх (nested) зогсоолд өнгөрүүлсэн хугацаа нийт хугацаанаас ХАСАГДАНА —
     дараагийн бүх дүрэм үлдсэн хугацаан дээр ажиллана.
  2. free_minutes дотор гарвал — 0₮.
  3. Шатлалын хүснэгтээс (кумулятив) үнэ авна: жишээ 60мин→1000₮, 120мин→2000₮, 180мин→5000₮.
  4. Сүүлийн шатлалаас хэтэрвэл эхэлсэн цаг тутамд extra_hour_price нэмнэ.
  5. daily_cap тохируулсан бол хоног тутмын дүн дээд хязгаараас хэтрэхгүй.
  6. Хөнгөлөлт: PERCENT (%), FIXED (₮), FREE_MINUTES (хугацаанаас хасна).
  7. НӨАТ: vat_inclusive=True үед үнэд багтсан (vat = total * r/(1+r)),
     False үед нэмж тооцно (total = base * (1+r)).
"""
import math
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .config import settings
from .models import Discount, TariffTemplate

D = Decimal


def _round(x: Decimal) -> Decimal:
    return x.quantize(D("1"), rounding=ROUND_HALF_UP)


def tier_price(template: TariffTemplate, minutes: int) -> Decimal:
    """Нэг хоногийн (эсвэл нэг үргэлжилсэн хугацааны) шатлалын үнэ."""
    if minutes <= 0:
        return D(0)
    tiers = sorted(template.tiers, key=lambda t: t.upto_minutes)
    if not tiers:
        # Шатлалгүй бол цаг тутмын үнээр
        hours = math.ceil(minutes / 60)
        return D(template.extra_hour_price or 0) * hours
    for t in tiers:
        if minutes <= t.upto_minutes:
            return D(t.price)
    # Сүүлийн шатлалаас хэтэрсэн
    last = tiers[-1]
    over_minutes = minutes - last.upto_minutes
    extra_hours = math.ceil(over_minutes / 60)
    return D(last.price) + D(template.extra_hour_price or 0) * extra_hours


def free_window_minutes(entry: datetime, until: datetime,
                        w_from: str, w_until: str, tz_hours: int = 8) -> int:
    """[entry, until] (UTC) интервалын өдөр бүрийн [w_from, w_until] (локал цаг,
    "HH:MM") цонхтой давхцах минут — гэрээт машины «үнэгүй цагийн цонх»-д
    хамаарах хугацааг тоолоход хэрэглэнэ.

    Цонх шөнө дамнахгүй (from < until) гэж үзнэ; буруу утгад 0 буцаана —
    төлбөрийн тооцоо унахгүй, зүгээр л цонх үйлчлэхгүй."""
    try:
        fh, fm = (int(x) for x in (w_from or "").split(":"))
        uh, um = (int(x) for x in (w_until or "").split(":"))
    except (ValueError, AttributeError):
        return 0
    start_min, end_min = fh * 60 + fm, uh * 60 + um
    if not (0 <= start_min < end_min <= 24 * 60):
        return 0
    tz = timedelta(hours=tz_hours)
    lo, hi = entry + tz, until + tz
    if hi <= lo:
        return 0
    total = 0
    day = lo.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < hi:
        s = max(lo, day + timedelta(minutes=start_min))
        e = min(hi, day + timedelta(minutes=end_min))
        if e > s:
            total += int((e - s).total_seconds() // 60)
        day += timedelta(days=1)
    return total


def calculate_fee(
    template: TariffTemplate | None,
    entry_time: datetime,
    exit_time: datetime | None = None,
    discount: Discount | None = None,
    is_registered: bool = False,
    paused_minutes: int = 0,
    no_charge: bool = False,
) -> dict:
    """Session-ийн төлбөрийг тооцоолно. Бүх дүн ₮ (бүхэл).

    paused_minutes — доторх (nested) зогсоолд өнгөрүүлсэн хугацаа. Гадна
    зогсоолын төлбөрөөс хасагдана: машин доторх зогсоолд байх хугацаанд гадна
    талын тоолуур зогсох ёстой. `duration_minutes` нь БОДИТ хугацаа хэвээр
    үлдэнэ (тайлан/жагсаалтад бодит зогсолтыг харуулна), зөвхөн төлбөр
    тооцогдох хугацаа багасна.

    no_charge — энэ зогсоол огт төлбөр авдаггүй (ажилчдын/дотоод зогсоол).
    """
    exit_time = exit_time or datetime.utcnow()
    total_minutes = max(0, int((exit_time - entry_time).total_seconds() // 60))
    paused = max(0, min(int(paused_minutes or 0), total_minutes))
    billable = total_minutes - paused

    result = {
        "duration_minutes": total_minutes,
        "paused_minutes": paused,
        "chargeable_minutes": billable,
        "base_fee": 0.0,
        "discount_amount": 0.0,
        "vat_amount": 0.0,
        "total_fee": 0.0,
        "is_free": True,
        "reason": "",
    }

    if no_charge:
        result["reason"] = "Төлбөргүй зогсоол"
        return result
    if is_registered:
        result["reason"] = "Бүртгэлтэй жолооч"
        return result
    if template is None:
        result["reason"] = "Тариф тохируулаагүй"
        return result

    chargeable = billable
    # Үнэгүй эхний минут — ДАМЖИН хугацааг хассаны ДАРАА шалгана. Тиймээс
    # доторх зогсоолд удаан байсан машин гадна талдаа үнэгүй хугацаандаа багтана.
    if template.free_minutes and billable <= template.free_minutes:
        result["reason"] = (f"Эхний {template.free_minutes} минут үнэгүй"
                            + (f" (дамжин {paused} мин хасагдсан)" if paused else ""))
        return result

    # FREE_MINUTES төрлийн хөнгөлөлт хугацаанаас хасагдана
    if discount and discount.discount_type == "FREE_MINUTES":
        chargeable = max(0, chargeable - int(discount.value))
        if chargeable == 0:
            result["reason"] = f"Хөнгөлөлт: {discount.name}"
            return result

    result["chargeable_minutes"] = chargeable

    # Хоног хуваах: 24 цагаас урт зогссон бол хоног тус бүрд daily_cap хэрэглэнэ
    day_minutes = 24 * 60
    full_days, rem = divmod(chargeable, day_minutes)
    fee = D(0)
    if full_days and template.daily_cap:
        fee += D(template.daily_cap) * full_days
        fee += min(tier_price(template, rem), D(template.daily_cap)) if rem else D(0)
    else:
        fee = tier_price(template, chargeable)
        if template.daily_cap and full_days == 0:
            fee = min(fee, D(template.daily_cap))

    # Дүнгийн хөнгөлөлт
    disc_amt = D(0)
    if discount and discount.discount_type == "PERCENT":
        disc_amt = fee * D(discount.value) / 100
    elif discount and discount.discount_type == "FIXED":
        disc_amt = min(D(discount.value), fee)
    fee_after = max(D(0), fee - disc_amt)

    # НӨАТ
    r = D(str(settings.vat_rate))
    if settings.vat_inclusive:
        total = fee_after
        vat = total * r / (1 + r)
        base = total - vat
    else:
        base = fee_after
        vat = base * r
        total = base + vat

    result.update(
        base_fee=float(_round(base)),
        discount_amount=float(_round(disc_amt)),
        vat_amount=float(_round(vat)),
        total_fee=float(_round(total)),
        is_free=float(total) == 0.0,
        reason="",
    )
    return result
