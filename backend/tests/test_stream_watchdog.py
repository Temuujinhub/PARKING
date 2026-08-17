"""Event стримийн «чимээгүй үхэл»-ийн хамгаалалт.

    cd backend && venv/bin/python tests/test_stream_watchdog.py

ЯАГААД ЭНЭ ТЕСТ ЧУХАЛ ВЭ: `read` timeout нь ЯМАР Ч байт ирэхэд шинэчлэгддэг.
Dahua 5-10с тутам heartbeat илгээдэг тул ANPR event нь ЗОГССОН ч холболт
«эрүүл» хэвээр мөнхөд үлддэг. Хуучин код нь «event ирсэнгүй» гэдгийг ЗӨВХӨН
холболт тасрахад шалгадаг байсан — heartbeat байхад тасрал болдоггүй тул тэр
шалгалт хэзээ ч ажилладаггүй байв.

Production хэмжилт (2026-08-17): 11 зогсоолын БҮГД дээр зогсолтын 35-48% нь
камерын логоос нөхөгдөж байв (10,350-аас 4,713). Логт 19-84 минутын чимээгүй
завсрууд байв — яг энэ гэмтлийн ул мөр.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.services.cgi_poller import reconnect_delay, stream_idle  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  {'✓' if cond else '✗ <<< FAIL'} {name}{'' if cond else f'  [{extra}]'}")


def main():
    old = settings.camera_event_idle_reconnect_sec
    try:
        settings.camera_event_idle_reconnect_sec = 900   # 15 минут

        print("\nЧимээгүй хугацаагаар шийднэ:")
        check("саяхан event ирсэн — дахин холбогдохгүй", not stream_idle(1000.0, 1000.0))
        check("14 минут — хараахан үгүй", not stream_idle(1000.0, 1000.0 + 14 * 60))
        check("15 минут яг — хараахан үгүй", not stream_idle(1000.0, 1000.0 + 900))
        check("15 минутаас хэтэрсэн — ДАХИН ХОЛБОНО",
              stream_idle(1000.0, 1000.0 + 901))
        check("84 минут (прод дээр бодитоор тохиолдсон завсар) — ДАХИН ХОЛБОНО",
              stream_idle(1000.0, 1000.0 + 84 * 60))

        print("\nТохиргоогоор унтраах боломжтой:")
        settings.camera_event_idle_reconnect_sec = 0
        check("0 = хамгаалалт унтарна", not stream_idle(1000.0, 1000.0 + 10 * 3600))

        print("\nТасралтын дараах хүлээлт (reconnect_delay):")
        settings.camera_event_min_stable_sec = 60.0
        settings.camera_event_fast_reconnect_sec = 1.0
        settings.camera_event_reconnect_sec = 15
        check("тогтвортой (5 мин) ажиллаад тасарсан → ХУРДАН (1с)",
              reconnect_delay(300.0) == 1.0, str(reconnect_delay(300.0)))
        check("яг босго дээр (60с) → хурдан", reconnect_delay(60.0) == 1.0)
        check("шууд унасан (5с) → удаан (15с)", reconnect_delay(5.0) == 15.0)
        check("огт холбогдоогүй (None) → удаан", reconnect_delay(None) == 15.0)
        # 2026-08-17-ны алдагдлын арифметик: 20с ажиллаад тасардаг камер дээр
        # 15с хүлээлт = цагийн 43% сохор байв; 1с хүлээлт = 4.8% болно
        check("20с/15с мөчлөгийн сохор хувь 40%+ байсан",
              15 / (20 + 15) > 0.40)
        check("20с/1с мөчлөгийн сохор хувь <5% болно",
              1 / (20 + 1) < 0.05)

        print("\nАнхдагч утга ажиллах хэмжээнд байна:")
        settings.camera_event_idle_reconnect_sec = old
        check("анхдагч 0 биш (хамгаалалт асаалттай)", old > 0, str(old))
        check("анхдагч 5-60 минутын хооронд", 300 <= old <= 3600, str(old))
    finally:
        settings.camera_event_idle_reconnect_sec = old

    print(f"\n{PASS} PASS, {FAIL} FAIL")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
