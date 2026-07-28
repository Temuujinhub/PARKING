#!/usr/bin/env python
"""LED дэлгэцэд БОДИТ датагаар 3 мөрийг ~6 секунд турших.

    # Гарах дэлгэц: дугаар / зогссон хугацаа / дүн (сүүлийн идэвхтэй session-оос)
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/screen_demo.py exit 192.168.6.11

    # Орох дэлгэц: орсон цаг / дугаар / Tavtai moril
    sudo /root/PARKING/backend/venv/bin/python /root/PARKING/tools/screen_demo.py entry 192.168.6.10

    # Тодорхой дугаараар (жагсаалтаас хамгийн сүүлийнх биш):
    sudo ... screen_demo.py exit 192.168.6.11 1234УБА

Юу хийдэг: тухайн камерын зогсоолын хамгийн сүүлийн идэвхтэй (OPEN/AWAITING_
PAYMENT) session-ий бодит дугаар, зогссон хугацаа, төлөх дүнг аваад ажиллаж буй
системтэй ЯГ ИЖИЛ замаар (display_on_screen: 4 давталт × 1.5с ≈ 6 секунд)
дэлгэцэд харуулна. Session олдохгүй бол жишээ дата ашиглана. Backend-д
нөлөөгүй — restart шаардлагагүй, зөвхөн дэлгэцэд бичнэ."""
import asyncio
import os
import sys
from datetime import datetime, timedelta

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Device, ParkingSession  # noqa: E402
from app.services.barrier import display_on_screen, render_screen_text  # noqa: E402
from app.services.device_auth import camera_credentials  # noqa: E402
from app.session_logic import amount_due, session_fee_info  # noqa: E402


async def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in ("exit", "entry"):
        print(__doc__)
        return 1
    mode, ip = sys.argv[1], sys.argv[2]
    want_plate = sys.argv[3].upper() if len(sys.argv) > 3 else None

    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.ip_address == ip).first()
        if not device:
            print(f"✗ {ip} хаягтай төхөөрөмж бүртгэлгүй байна")
            return 1
        q = db.query(ParkingSession).filter(
            ParkingSession.site_id == device.site_id,
            ParkingSession.status.in_(["OPEN", "AWAITING_PAYMENT", "PAID"]))
        if want_plate:
            q = q.filter(ParkingSession.plate_number == want_plate)
        s = q.order_by(ParkingSession.entry_time.desc()).first()

        if s:
            fee = session_fee_info(db, s, at=datetime.utcnow())
            due = amount_due(db, s, fee)
            plate, dur = s.plate_number, fee["duration_minutes"]
            entry_local = s.entry_time + timedelta(hours=settings.tz_offset_hours)
            print(f"Бодит дата: {plate}, орсон {entry_local:%H:%M}, "
                  f"{dur} мин, төлөх {due:.0f}")
        else:
            plate, dur, due = "1234УБА", 125, 5000
            entry_local = datetime.utcnow() + timedelta(hours=settings.tz_offset_hours)
            print("Идэвхтэй session олдсонгүй — жишээ дата ашиглана")

        if mode == "exit":
            text = render_screen_text(settings.screen_fee_text, amount=due,
                                      plate=plate, duration_minutes=dur)
        else:
            text = render_screen_text(settings.screen_welcome_text, plate=plate,
                                      time_str=entry_local.strftime("%H:%M"))
        print(f"Илгээж буй {len(text.splitlines())} мөр:")
        for i, ln in enumerate(text.splitlines(), 1):
            print(f"  {i}: {ln}")
        err = await display_on_screen(ip, text, creds=camera_credentials(device))
        if err:
            print(f"✗ Дэлгэцэд бичиж чадсангүй: {err}")
            return 1
        print("✓ Илгээлээ — LED дээр ~6 секунд харагдана (3 мөр тус тусдаа гарсан эсэхийг ажиглана уу)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
