"""Төлбөр тооцооллын цөм.

Дүрэм:
  1. Бүртгэлтэй (гэрээт) жолооч хүчинтэй бол — 0₮.
  2. free_minutes дотор гарвал — 0₮.
  3. Шатлалын хүснэгтээс (кумулятив) үнэ авна: жишээ 60мин→1000₮, 120мин→2000₮, 180мин→5000₮.
  4. Сүүлийн шатлалаас хэтэрвэл эхэлсэн цаг тутамд extra_hour_price нэмнэ.
  5. daily_cap тохируулсан бол хоног тутмын дүн дээд хязгаараас хэтрэхгүй.
  6. Хөнгөлөлт: PERCENT (%), FIXED (₮), FREE_MINUTES (хугацаанаас хасна).
  7. НӨАТ: vat_inclusive=True үед үнэд багтсан (vat = total * r/(1+r)),
     False үед нэмж тооцно (total = base * (1+r)).
"""
import math
from datetime import datetime
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


def calculate_fee(
    template: TariffTemplate | None,
    entry_time: datetime,
    exit_time: datetime | None = None,
    discount: Discount | None = None,
    is_registered: bool = False,
) -> dict:
    """Session-ийн төлбөрийг тооцоолно. Бүх дүн ₮ (бүхэл)."""
    exit_time = exit_time or datetime.utcnow()
    total_minutes = max(0, int((exit_time - entry_time).total_seconds() // 60))

    result = {
        "duration_minutes": total_minutes,
        "chargeable_minutes": total_minutes,
        "base_fee": 0.0,
        "discount_amount": 0.0,
        "vat_amount": 0.0,
        "total_fee": 0.0,
        "is_free": True,
        "reason": "",
    }

    if is_registered:
        result["reason"] = "Бүртгэлтэй жолооч"
        return result
    if template is None:
        result["reason"] = "Тариф тохируулаагүй"
        return result

    chargeable = total_minutes
    # Үнэгүй эхний минут
    if template.free_minutes and total_minutes <= template.free_minutes:
        result["reason"] = f"Эхний {template.free_minutes} минут үнэгүй"
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
