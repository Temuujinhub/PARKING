"""Nested (дамжин) зогсоол — доторх зогсоолд байх хугацаанд ГАДНА зогсоолын
төлбөрийн тоолуурыг зогсоох.

Жишээ урсгал:

    гадна орох  ─► [ГАДНА]  ─► дотор орох ─► [ДОТОР: тоолуур ЗОГСНО]
                      ▲                              │
    гадна гарах ◄─────┴───────────────────  дотор гарах

⚠ Аль IP нь «дотор орох», аль нь «дотор гарах» болохыг ЭНД БҮҮ таамаглаарай —
камер солигдож/дахин байрлуулагдаж болно. Чиглэл СОЛИГДВОЛ систем чимээгүй
буруу тооцно: гаднах ТӨЛБӨРТЭЙ талбарт зогссон машины тоолуур зогсож (дутуу
нэхэмжилнэ), доторх ҮНЭГҮЙ талбарынх ажиллана. Хаалт нээгдсээр байдаг тул
гомдол ирэхгүй. Уншилтын дарааллаас нотлох:
`venv/bin/python tools/nested_lanes.py <SITE>`.

Гадна зогсоолын төлбөр = (гарсан − орсон) − (дотор өнгөрүүлсэн хугацаа).
Доторх зогсоол руу огт ороогүй машин энгийнээр бодогдоно.

Загварын гол шийдвэрүүд:
  • Доторх зогсоол нь ТУСДАА ParkingSite бөгөөд `parent_site_id`-аар гадныхаа
    заана. Session-ий идэвхтэй unique индекс нь (site_id, plate_number) тул нэг
    машин гадна/дотор хоёуланд зэрэг нээлттэй session-тэй байж чадна.
  • Тоолуур зогсолтыг ГАДНА session дээр хадгална: `paused_since` (одоо дотор
    байгаа бол орсон цаг) + `paused_minutes` (хуримтлагдсан). Машин дотогш
    олон удаа орж болох тул хуримтлуулна.
  • Доторх ГАРАХ уншилт алдагдвал тоолуур мөнхөд зогсох тул `transit_max_hours`
    дээд хязгаар тавина — түүнээс хэтэрсэн хугацаа хасагдахгүй, төлбөртэй.

Хязгаарын дүрэм (2026-10-13; «доторх бүх БАТЛАГДСАН хугацааг хасна» — эзэн):
  • БАТЛАГДСАН зогсолт — доторх ГАРАХ уншилтаар (live `on_inner_exit`,
    `handle_inner_pass`-ийн гарах, camera_sync-ийн логийн дотоод/хүүхэд
    сешний гарах уншилт) хаагдсан: тохируулсан хязгаар
    (`child_cap_minutes`: хүүхэд → эцэг → глобал). 0 = ХЯЗГААРГҮЙ.
  • camera_sync/албадан хаалт хүүхэд сешнийг «хуурамч гарц»-ын босгоос
    (`fake_exit_minutes`) БОГИНО зогсолттой хаавал энэ нь батлагдсан гарц БИШ:
    доторх ГАРАХ камер дөнгөж ОРЖ буй машиныг уншдаг (cross-read, 15–104с).
    Эцгийн зогсолт нээлттэй үлдэнэ (`session_logic.close_session_forced`).
  • БАТЛАГДААГҮЙ зогсолт — доторх гарах уншилт ирээгүй, гадна гарц/албадан
    хаалт дээр барагдуулсан (`close_open_pause`), мөн одоо явж буй зогсолтын
    урьдчилсан дүн (`effective_paused_minutes`): `unverified_cap_minutes` —
    тохируулсан хязгаар > 0 бол тэр, 0 бол глобал `settings.transit_max_hours`
    (глобал ч 0 бол хязгааргүй). Шалтгаан: эцгийн 0-ийг хүүхэд өвлөснөөс
    (100 айл) доторх гарах уншилт АЛДАГДСАН машин (доторх зогсоолоос гарч
    гадна талбайд зогссон ч) бүх гадна хугацаагаа үнэгүй авах байв. Үнэ нь:
    1937УАЕ 10-09 үнэхээр 7ц дотор байсан ч гарах уншилт нь зөвхөн татгалзсан
    (итгэл 26) тул 4ц хасагдана — прод 8,000₮-тэй ижил (зөв дүн 0₮).
    Энэ дүрэм ЗӨВХӨН тусдаа site-тай (parent/child) загварт; НЭГ site доторх
    (`nested_inner` камер) загварт 0 = хязгааргүй хэвээр (Рашбулаг ЭТТ —
    2026-09-19 эзэн баталсан `owner_confirmed_unlimited_inner_time`).
  • Машин дотор байх үеийн урьдчилсан дүн нь «одоо гадна гарцад гарах уншилтгүй
    ирвэл» авах дүнтэй ЯГ ижил (гарц ч мөн `unverified_cap_minutes`-ээр
    барагдуулна). Доторх гарах уншилт ирвэл хасалт зөвхөн ӨСНӨ (дүн буурна).

Хэнд хасалт өгөх (2026-10-13):
  • Зөвхөн доторх хаалтаар НЭВТРЭХ эрхтэй машин (`_admitted` — live
    `handle_entry`-ийн `denied`/`black_blocks`-тэй ижил). Татгалзсан машинд ч
    доторх сешн үүсдэг (Рашбулаг шороо 10-12..13: 19 сешн, хаалтны команд 0) —
    сэргээлт/нөхөлтийн зам тэднийг «дотор байсан» гэж тооцохгүй.
  • Өмнөх айлчлалаас үлдсэн (гадна сешн эхлэхээс өмнө нээгдсэн) доторх сешн
    энэ айлчлалд зөвхөн шинэ доторх ОРОХ уншилтаас тоологдоно (8810УКР).

OCR-ойролцоо тохирол (2026-10-14; `match_parent`, `inner_plate_similar`):
  Гадна ба доторх ОРОХ камер нэг машиныг 1 тэмдэгтээр зөрүүтэй уншдаг —
  7969УАА/7969УА, 6238ӨМҮ/6238ӨМ, 2678УКР/2678УР, 9707УАС/9707УАО, 4171УЕА/4171УРА,
  0450УЕМ/0450УБ, 8333УАО/8333УАС. Яг таарах гадна сешн олдохгүй тул тоолуур
  зогсоогүй, дотор өнгөрүүлсэн бүх хугацаа нэхэгдэж байв (6238ӨМҮ ~1,348 мин дотор →
  25,000₮ + 2,000₮ өр; 7969УАА 280-аас 272 мин дотор → 11,000₮; 2678УКР 1,000₮).
  Дүрэм — ЯГ таарах идэвхтэй гадна сешн байхгүй үед л:
    • ойролцоо (`inner_plate_similar`: `plates_ocr_similar` ба тайрагдсан уншилт,
      эсвэл ижил 4 цифр + үсгийн хэсэг ≤1 засвар, OCR андуурлын хос 0-ээр) гадна
      идэвхтэй сешн ЯГ НЭГ байх — 0 эсвэл 2+ бол тохируулахгүй (өнөөдрийнх шиг);
    • тэр нь өөр доторх зогсолтоор аль хэдийн ЗОГСООГҮЙ байх;
    • гадна камерт сүүлд харагдсан (орох эсвэл гарах уншилт) нь доторх уншилтаас
      `_FUZZY_WINDOW` (10 мин) дотор ӨМНӨ байх. Хэмжилт (audit1014, 10-07..14):
      гадна орох → доторх орох хоцрогдол 100 айл медиан 17с, max 136с; Рашбулаг
      шороо медиан 44с, max 571с. 10 мин нь max-ыг багтаана; түүнээс урт
      хоцрогдол бол гадна талбайд зогсоод хожим орсон машин — ойролцоо уншилтаар
      ТААМАГЛАХГҮЙ (дутуу нэхэмжлэхээс илүү ихээр нэхэх нь аюулгүй тал).
  Зөвхөн OPEN гадна сешнд — AWAITING_PAYMENT/PAID сешнд яг дугаар л (машин гарсан
  байж болох тул буцаалт/зогсолт нь өрийг бүхэлд нь хасах эрсдэлтэй). Уншсан ЗӨВ
  дугаар эцэг зогсоолд сүүлийн 14 хоногт өөрийн сешнтэй бол тусдаа машин —
  тохируулахгүй (`_OWN_HISTORY`).
  Доторх уншилт формат буруу/тайрагдсан бол доторх сешний дугаарыг гадныхаар засна
  (тэмдэглэлтэй) — дараагийн доторх гарах уншилт тэр сешнтэй таарна. Зөв форматтай
  уншилт өөрийн дугаараа хадгална (гадна камер буруу уншсан байж болно). Засагдаагүй үлдсэн доторх сешн
  хаагдахад (`on_inner_exit`) — «ЗОГССОН ганц нэр дэвшигч» + тэр доторх сешн
  орохдоо цонхонд багтсан байх. `reapply_child_pauses` яг дугаараар хэвээр (хоёр
  уншилт хоёулаа алдагдсан тохиолдол — нотолгоо сул).

Доторх гарах уншилтын нөхөлт (`match_paused_parent`, session_logic.handle_exit):
  төлбөргүй (no_charge) доторх зогсоолын гарах камер доторх сешнгүй машин уншвал
  ЗОГССОН гадна сешнийг (яг, эсвэл дээрх ганц ойролцоо) хайна — олдвол батлагдсан
  доторх гарц, хаалт нээнэ. Олдохгүй (үл мэдэгдэх машин) бол review хэвээр.
  10-14 өглөө 100 айлын доторх гарах 26 уншилтын 25 нь review болж хаалт нээгдээгүй.

Гарцад татгалзсан машин дотогш буцвал (`_revert_refused_exit`): гадна сешн
  AWAITING_PAYMENT (төлбөргүй, төлбөрийн оролдлогогүй, мөр түгжээгүй) байхад
  татгалзсанаас 15 мин дотор (`_REVERT_WINDOW`) доторх ОРОХ уншилт (ЯГ дугаар) ирвэл
  гадна сешнийг OPEN болгож тоолуурыг зогсооно —
  дараагийн жинхэнэ гарцад ердийнхөөрөө бодогдоно. Зогсолт явж байсан бол (доторх
  гарах уншилт алдагдсан) гадна гарах уншилт хүртэлх нь батлагдаагүй хязгаараар
  барагдаж, шинэ зогсолт энэ уншилтаас. Өмнө нь гарах уншилтын үеийн дүн царцаж
  (6238ӨМҮ 10-14 06:44 → 25,000₮), 2 цагийн дараа өр болж, жинхэнэ гарцад нэмж
  2,000₮ «орох уншилтгүй» хураамж үүсдэг байв.

Хязгаар (2026-10-14 эзэн): 100 айлд доторх гарах уншилт алдагдвал 24 цаг хүртэл
  хасна — Тохиргоо → Төлбөрийн дүрэм → «Доторх гарах уншилт алдагдсан зогсолтын
  хязгаар» (`inner_unverified_cap_hours`, эцэг зогсоол дээр). Хүүхдийн
  `transit_max_hours`=24 нь БАТЛАГДСАН зогсолтыг ч 24ц-аар таслах тул (7685УБО 55ц,
  6238ӨМҮ 31ц батлагдсан) тусдаа тохиргоо болгов — `unverified_cap_minutes`.
"""
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..models import AuditLog, LprEvent, ParkingSession, ParkingSite

log = logging.getLogger("parking.nested")

_ACTIVE = ("OPEN", "AWAITING_PAYMENT", "PAID")
# Доторх сешн гадна сешнээс ийм хэмжээгээр ӨМНӨ эхэлсэн бол «өмнөх айлчлалынх»
# (камер/серверийн цагийн бага зөрүүг тэвчинэ)
_STALE_TOLERANCE = timedelta(minutes=2)
# OCR-ойролцоо тохиролд: гадна камерт сүүлд харагдсанаас доторх ОРОХ уншилт хүртэл
# (модулийн docstring — Рашбулаг шороо max 571с, 100 айл max 136с)
_FUZZY_WINDOW = timedelta(minutes=10)
_DIGITS_LETTERS = re.compile(r"^(\d{4})(\D+)$")
# Гарцад татгалзсанаас хойш доторх ОРОХ уншилт хүртэлх дээд хугацаа — буцаалт
# (`_revert_refused_exit`). Түүнээс хожуу бол 2 цагийн AWAITING → unpaid_exit өрийн
# дүрмийг тойрох эрсдэлтэй (хаалт дагаж гарсан машин 72ц дараа өргүй хаагдана).
_REVERT_WINDOW = timedelta(minutes=15)
# Уншсан ЗӨВ дугаар өөрөө эцэг зогсоолд сүүлийн N хоногт бүртгэлтэй бол тэр нь
# тусдаа (жинхэнэ) машин — ойролцоо тохирол хийхгүй (хяналт N1, 2026-10-14).
_OWN_HISTORY = timedelta(days=14)


def _letters_close(a: str, b: str) -> bool:
    """Үсгийн хэсэг ≤1 засвараар (оруулах/хасах/солих) ижил. OCR андуурлын хос
    (`_ocr_canon`, `_OCR_CONFUSABLE_PAIRS` — Б/Е, У/Ү г.м.) солилт үнэгүй."""
    from ..session_logic import _OCR_CONFUSABLE_PAIRS, _ocr_canon
    if abs(len(a) - len(b)) > 1:
        return False

    def same(x, y):
        return (x == y or _ocr_canon(x) == _ocr_canon(y)
                or frozenset((x, y)) in _OCR_CONFUSABLE_PAIRS)
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (0 if same(a[i - 1], b[j - 1]) else 1))
        prev = cur
    return prev[-1] <= 1


def inner_plate_similar(read: str, outer: str) -> bool:
    """Доторх камерын уншилт гадна сешний дугаартай НЭГ машин байж болох уу.

    `plates_ocr_similar` (1 солилт, жигдэлсэн ижил, тайрагдсан 7969УА ← 7969УАА)
    + ижил 4 цифр, үсгийн хэсэг ≤1 засвар (2678УР ← 2678УКР, 0450УБ ← 0450УЕМ).
    Дангаараа шийдвэр БИШ — `_fuzzy_parent` ганц нэр дэвшигч + цонхыг шаардана."""
    from ..session_logic import plates_ocr_similar
    if not read or not outer:
        return False
    if plates_ocr_similar(read, outer):
        return True
    a, b = _DIGITS_LETTERS.match(read), _DIGITS_LETTERS.match(outer)
    return bool(a and b and a.group(1) == b.group(1) and _letters_close(a.group(2), b.group(2)))


def _seen_outside(s: ParkingSession, at: datetime) -> bool:
    """Гадна ОРОХ уншилт (`entry_time`) `at`-аас өмнө `_FUZZY_WINDOW` дотор уу.
    Ойролцоо тохирол зөвхөн OPEN гадна сешнд (`_fuzzy_parent`) тул «сүүлд гадна
    камерт харагдсан» = орох уншилт."""
    t = s.entry_time
    return t is not None and at - _FUZZY_WINDOW <= t <= at + _STALE_TOLERANCE


def _fuzzy_parent(db: Session, site: ParkingSite, plate: str, at: datetime, *,
                  paused: bool, window: bool) -> ParkingSession | None:
    """ЯГ таарах гадна сешн байхгүй үеийн OCR-ойролцоо тохирол (модулийн docstring).

    Эцэг зогсоолын БҮХ идэвхтэй сешнээс ойролцоо нь ЯГ НЭГ байх (2+ = хоёрдмол,
    буруу машиныг зогсоож дутуу нэхэмжлэх эрсдэл — тохируулахгүй).
    paused=False (орох): тэр нь OPEN, зогсоогүй — гарцад төлбөр хүлээж буй /
    төлсөн (AWAITING_PAYMENT/PAID) сешнийг ойролцоо уншилтаар ХӨНДӨХГҮЙ: машин аль
    хэдийн гарсан байж болох ба зогсолт (+OPEN болгох буцаалт) нь өрийг нь бүхэлд
    нь хасах эрсдэлтэй — тэдэнд зөвхөн ЯГ дугаар. paused=True (гарах): `at`-аас
    өмнө зогссон. window: гадна орох уншилт `_FUZZY_WINDOW` дотор."""
    if not site or not site.parent_site_id or not plate:
        return None
    from ..session_logic import is_valid_plate
    if is_valid_plate(plate) and db.query(ParkingSession.id).filter(
            ParkingSession.site_id == site.parent_site_id,
            ParkingSession.plate_number == plate,
            ParkingSession.entry_time >= at - _OWN_HISTORY).first() is not None:
        # Зөв форматтай уншилт эцэг зогсоолд өөрийн түүхтэй — жинхэнэ өөр машин
        # (гадна орох уншилт нь энэ удаа алдагдсан байж болно). Формат буруу/
        # тайрагдсан уншилтад хамаарахгүй: давтагддаг хог уншилт машин биш.
        log.info("[nested] %s: «%s» — энэ дугаар эцэг зогсоолд өөрийн бүртгэлтэй (тусдаа "
                 "машин), ойролцоо тохирол хийсэнгүй", plate, site.name)
        return None
    cands = [s for s in (db.query(ParkingSession)
                         .filter(ParkingSession.site_id == site.parent_site_id,
                                 ParkingSession.status.in_(_ACTIVE)).all())
             if inner_plate_similar(plate, s.plate_number)]
    if len(cands) != 1:
        if cands:
            log.info("[nested] %s: «%s» — ойролцоо гадна сешн %d (%s), хоёрдмол тул "
                     "тохируулсангүй", plate, site.name, len(cands),
                     ", ".join(c.plate_number for c in cands[:5]))
        return None
    c = cands[0]
    if paused:
        if not c.paused_since or c.paused_since > at:
            return None
    elif c.paused_since or c.status != "OPEN":
        return None
    if window and not _seen_outside(c, at):
        return None
    return c


def match_parent(db: Session, site: ParkingSite, plate: str, at: datetime, *,
                 paused: bool) -> tuple[ParkingSession | None, bool]:
    """(гадна сешн, ойролцоо эсэх). ЯГ дугаар давуу (нөхцөлгүй — дуудагч шалгана);
    байхгүй бол `_fuzzy_parent` (paused=False үед цонхтой)."""
    exact = _parent_session(db, site, plate)
    if exact is not None:
        return exact, False
    fz = _fuzzy_parent(db, site, plate, at, paused=paused, window=not paused)
    return fz, fz is not None


def match_paused_parent(db: Session, site: ParkingSite, plate: str,
                        at: datetime) -> ParkingSession | None:
    """Доторх гарах уншилтын нөхөлтөд — `at`-аас өмнө ЗОГССОН гадна сешн (яг эсвэл
    ганц ойролцоо). Яг дугаартай сешн зогсоогүй бол None (машин дотор байсан нь
    бүртгэгдээгүй — review хэвээр)."""
    parent, _fz = match_parent(db, site, plate, at, paused=True)
    if parent is None or not parent.paused_since or parent.paused_since > at:
        return None
    return parent


def _note_fuzzy(db: Session, child_site: ParkingSite, parent: ParkingSession, read: str,
                child: ParkingSession | None, why: str) -> None:
    """Ойролцоо тохирлыг баримтжуулна. Доторх уншилт формат буруу/тайрагдсан
    (7969УА) бол доторх сешний дугаарыг гадныхаар засна (дараагийн доторх гарах
    уншилт таарна). ЗӨВ форматтай уншилт өөрийн дугаараа хадгална (хяналт F4):
    гадна камер буруу уншсан байж болно (прод 8810УКР ← гадна 8810УБР) — гадныхаар
    дарвал «зөвхөн бүртгэлтэй» доторх зогсоолд `_admitted` худал болж сэргээлт
    унана. Тэр сешн гарахад `on_inner_exit` (орох цонх) / доторх гарах нөхөлт таарна."""
    from ..session_logic import is_valid_plate
    renamed = False
    if (child is not None and child.plate_number != parent.plate_number
            and not is_valid_plate(read)
            and is_valid_plate(parent.plate_number)
            and not getattr(child, "hospital_grant_id", None)
            and db.query(ParkingSession.id)
            .filter(ParkingSession.site_id == child.site_id,
                    ParkingSession.plate_number == parent.plate_number,
                    ParkingSession.status.in_(_ACTIVE),
                    ParkingSession.id != child.id).first() is None):
        old = child.plate_number
        child.plate_number = parent.plate_number
        child.note = (f"{child.note + ' | ' if child.note else ''}Доторх OCR зөрүү «{old}» → "
                      f"гадна «{parent.plate_number}» (засав)")[:1000]
        renamed = True
    db.add(AuditLog(username="system", action="NESTED_OCR_MATCH", entity="session",
                    entity_id=parent.id,
                    detail={"read_plate": read, "parent_plate": parent.plate_number,
                            "child_site": child_site.name, "why": why,
                            "child_session_id": child.id if child is not None else None,
                            "child_renamed": renamed}))
    log.info("[nested] %s: «%s» — гадна «%s» сешнд OCR-ойролцоогоор тохов (%s)%s",
             read, child_site.name, parent.plate_number, why,
             ", доторх сешний дугаарыг засав" if renamed else "")


def _lock_parent(db: Session, parent: ParkingSession) -> ParkingSession | None:
    """Гадна мөрийг SKIP LOCKED-оор түгжинэ. Түгжээтэй (төлбөр/гарц яг тэр мөрийг
    боловсруулж буй) бол None — хүлээвэл lock_timeout (3с)-оор бүх транзакц
    (доторх сешн, орох хаалт) унана (2026-10-14 уралдааны тест)."""
    db.flush()
    return (db.query(ParkingSession).enable_eagerloads(False)
            .filter(ParkingSession.id == parent.id, ParkingSession.status.in_(_ACTIVE))
            .populate_existing().with_for_update(skip_locked=True).first())


def _revert_refused_exit(db: Session, parent: ParkingSession, at: datetime) -> bool:
    """Гадна гарцад төлбөр хүлээж (AWAITING_PAYMENT) байсан машин доторх зогсоол
    руу буцаж орлоо — гарах оролдлогыг цуцалж OPEN болгоно (модулийн docstring,
    6238ӨМҮ). Төлбөр/төлбөрийн оролдлого (QR нэхэмжлэл г.м) байвал эсвэл дүн
    царцсан (орох уншилтгүй суурь хураамж) бол ХҮРЭХГҮЙ — өмнөх зан төлөв хэвээр.
    Дуудагч мөрийг `_lock_parent`-аар түгжсэн байна (төлбөртэй уралдахгүй)."""
    from ..session_logic import paid_total
    from .checkout import active_attempts
    seen = parent.last_exit_seen_at or parent.payment_wait_started_at
    if (parent.status != "AWAITING_PAYMENT" or getattr(parent, "fee_locked", False)
            or parent.paid_at or seen is None or seen > at
            or at - seen > _REVERT_WINDOW):
        # `_REVERT_WINDOW`-оос хожуу: 2 цагийн AWAITING → unpaid_exit өрийн дүрэм
        # хэвээр (хяналт F2 — 115 мин-ын дараах уншилт өрийг тойрох байв)
        return False
    if active_attempts(db, parent.id) or paid_total(db, parent) > 0:
        return False
    quoted = float(parent.total_fee) if parent.total_fee is not None else None
    refused_snapshot = parent.exit_snapshot
    parent.status = "OPEN"
    parent.exit_time = None
    parent.exit_device_id = None
    parent.exit_deadline = None
    parent.last_exit_seen_at = None
    parent.payment_wait_started_at = None
    parent.payment_quote = None
    parent.payment_quote_until = None
    parent.duration_minutes = None
    parent.total_fee = parent.base_fee = parent.vat_amount = None
    # Татгалзсан оролдлогын гарах зураг жинхэнэ гарцынхыг хаахгүй (snapshot.attach_saved
    # эхний зургийг хадгалдаг) — замыг аудитад үлдээнэ.
    parent.exit_snapshot = None
    parent.exit_snapshot_source = None
    parent.note = (f"{parent.note + ' | ' if parent.note else ''}Гарцад төлбөр хүлээж байгаад "
                   f"доторх зогсоол руу буцаж орсон — гарах оролдлогыг цуцлав")[:1000]
    db.add(AuditLog(username="system", action="NESTED_EXIT_REVERT", entity="session",
                    entity_id=parent.id,
                    detail={"plate": parent.plate_number, "exit_seen_at": seen.isoformat(),
                            "inner_entry_at": at.isoformat(), "quoted_fee": quoted,
                            "refused_exit_snapshot": refused_snapshot}))
    # Гараар сэргээх (REOPEN)-тэй ижил мэдэгдэл — касс/самбар «төлбөр хүлээж буй»-г арилгана
    from ..ws import notify
    notify(parent.site_id, "ENTRY_EVENT", {
        "session_id": parent.id, "plate": parent.plate_number,
        "entry_time": parent.entry_time.isoformat() if parent.entry_time else None,
        "reopened": True, "by": "system:nested_reentry"})
    log.info("[nested] %s: гарцад төлбөр хүлээж байсан машин дотогш буцлаа — гадна сешнийг "
             "OPEN болгов (хүлээж байсан дүн %s)", parent.plate_number, quoted)
    return True


def _admitted(db: Session, child_site: ParkingSite, plate: str) -> bool:
    """Энэ машинд доторх хаалт НЭЭГДЭХ үү — live `handle_entry`-ийн `denied`
    («Зөвхөн бүртгэлтэй» зогсоолын бүртгэлгүй машин) ба `black_blocks`
    (хар жагсаалт + орохыг хориглох дүрэм)-тэй ижил шийдвэр.

    Татгалзсан машинд ч доторх сешн үүсдэг (хаалт нээгдэхгүй) — Рашбулаг шороо
    10-12..13-нд 19 доторх сешн, нэг ч хаалтны команд алга. Live зам тэдний
    гадна тоолуурыг зогсоодоггүй; сэргээлт/нөхөлтийн зам ч зогсоох ёсгүй
    (эс бол гадна сешн «хуурамч гарц»-аар сэргэхэд 4ц хүртэл үнэгүй болно).
    Одоогийн бүртгэл/хар жагсаалтаар шалгана (сешнд шийдвэр хадгалагддаггүй)."""
    from ..session_logic import find_registered, is_blacklisted
    if child_site.registered_only and find_registered(db, plate, child_site.id) is None:
        return False
    if is_blacklisted(db, plate) is not None:
        from .app_settings import get_blacklist_rules
        if get_blacklist_rules(db, child_site.id)["block_entry"]:
            return False
    return True


def cap_minutes(site: ParkingSite | None) -> int:
    """Тоолуур зогсох дээд минут. 0 = хязгааргүй."""
    hours = None if site is None else site.transit_max_hours
    if hours is None:
        hours = settings.transit_max_hours
    return max(0, int(hours)) * 60


def child_cap_minutes(db: Session, child: ParkingSite) -> int:
    """ТУСДАА site-тай (parent/child) загварын хүүхэд зогсоолын хязгаар.

    Хүүхэд дээр тохируулаагүй (NULL) бол ЭЦЭГ зогсоолын утгыг авна, тэр ч
    хоосон бол глобал. Өмнө нь зөвхөн хүүхдийнхийг уншдаг байсан тул админ
    «Дотор хугацааг хасах хязгаар»-ыг гадна (эцэг) зогсоол дээр 0 гэж тавихад
    backend үл тоож глобал 4 цагаар тасалдаг байв — 100 айл 10-07..10-12:
    доторх ГАРАХ уншилттай 7–13ц дотор байсан 29 гадна сешнд 4 цагийг л хасаж
    352,000₮ нэхсэн (audit1013 дахин тооцоолол). 0 = хязгааргүй нь зөвхөн
    БАТЛАГДСАН зогсолтод — модулийн docstring-ийн хязгаарын дүрмийг үз."""
    if child is not None and child.transit_max_hours is None and child.parent_site_id:
        parent = db.get(ParkingSite, child.parent_site_id)
        if parent is not None and parent.transit_max_hours is not None:
            return cap_minutes(parent)
    return cap_minutes(child)


def pause_cap_minutes(db: Session, site_id: str) -> int:
    """Гадна session дээрх зогсолтод хэрэглэх дээд хязгаар (минут). 0 = хязгааргүй.

    Гадна session дээр зогсолт үлдсэн (paused_since) байхад ямар хязгаар
    хэрэглэхээ мэдэх хэрэгтэй, гэвч аль доторх зогсоолд орсныг session өөрөө
    хадгалдаггүй.

      • ТУСДАА site-тай (parent/child) загвар: хүүхэд зогсоол(ууд)-ын тохиргоо
        (хүүхэд дээр хоосон бол эцгийнх — `child_cap_minutes`).
        Хэд хэдэн доторх зогсоолтой бол ХАМГИЙН БАГА хязгаарыг авна (аюулгүй тал).
      • НЭГ site доторх (`nested_inner` камер) загвар: хүүхэд зогсоол байхгүй тул
        тухайн зогсоолын ӨӨРИЙН `transit_max_hours`. Өмнө нь энэ тохиолдолд
        глобал default руу шууд унадаг байсан — үр дүнд нь доторх ГАРАХ уншилт
        алдагдсан машин зогсоолын тохиргоог тойрч, ЗӨВ уншсанаас ИЛҮҮ хугацаа
        үнэгүй авдаг байв (жагсаалт дээрх дүн ба гарц дээрх дүн ч зөрдөг).
    """
    return _configured_cap(db, site_id)[0]


def _configured_cap(db: Session, site_id: str) -> tuple[int, bool]:
    """(`pause_cap_minutes`, хүүхэд зогсоолтой эсэх) — нэг асуулгаар."""
    kids = db.query(ParkingSite).filter(ParkingSite.parent_site_id == site_id).all()
    if not kids:
        return cap_minutes(db.get(ParkingSite, site_id)), False
    caps = [child_cap_minutes(db, k) for k in kids]
    return (0 if 0 in caps else min(caps)), True


def unverified_cap_minutes(db: Session, site_id: str) -> int:
    """Доторх ГАРАХ уншилтаар БАТЛАГДААГҮЙ зогсолтын хязгаар (минут). 0 = хязгааргүй.

    Тусдаа site-тай (parent/child) загварт «0 = хязгааргүй» нь зөвхөн жинхэнэ
    доторх гарах уншилтаар хаагдсан зогсолтод үйлчилнэ. Гарах уншилтгүй
    (гадна гарц/албадан хаалт дээр барагдуулсан, эсвэл одоо явж буй) зогсолтод
    тохируулсан хязгаар 0 бол глобал `transit_max_hours`-ыг хэрэглэнэ — эс бол
    доторх гарах уншилт алдагдсан машин (доторх зогсоолоос гараад гадна талбайд
    зогссон ч) бүх гадна хугацаагаа үнэгүй авна. 1937УАЕ (10-09, 7ц дотор,
    гарах уншилт зөвхөн татгалзсан) энэ хязгаараар 4ц = прод 8,000₮.

    НЭГ site доторх загвар (хүүхэд зогсоолгүй) `pause_cap_minutes`-тэй ижил —
    Рашбулаг ЭТТ-ийн 0-ийг эзэн 2026-09-19 «хязгааргүй» гэж баталсан.

    Глобал утгыг эцэг зогсоол бүрээр дарж болно (2026-10-14): Тохиргоо →
    Төлбөрийн дүрэм → «Доторх гарах уншилт алдагдсан зогсолтын хязгаар»
    (`exit_rules.inner_unverified_cap_hours`, анхдагч = .env transit_max_hours).
    100 айл: 24ц (эзэн) — батлагдсан зогсолт хязгааргүй хэвээр."""
    cap, split = _configured_cap(db, site_id)
    if cap or not split:
        return cap
    from .app_settings import get_exit_rules
    return max(0, int(get_exit_rules(db, site_id)["inner_unverified_cap_hours"])) * 60


def _parent_session(db: Session, site: ParkingSite, plate: str) -> ParkingSession | None:
    """Гадна зогсоол дээрх тухайн машины идэвхтэй session."""
    if not site or not site.parent_site_id:
        return None
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site.parent_site_id,
                    ParkingSession.plate_number == plate,
                    ParkingSession.status.in_(_ACTIVE))
            .order_by(ParkingSession.entry_time.desc()).first())


def settle_pause(s: ParkingSession, at: datetime, cap: int) -> int:
    """Явж буй зогсолтыг хааж `paused_minutes` дээр нэмнэ. Нэмсэн минутыг буцаана.

    cap > 0 бол ЭНЭ УДААГИЙН зогсолт тэр хэмжээгээр таслагдана — доторх гарах
    уншилт алдагдсан тохиолдолд бүх хугацаа үнэгүй болохоос сэргийлнэ.
    """
    if not s.paused_since:
        return 0
    mins = max(0, int((at - s.paused_since).total_seconds() // 60))
    if cap:
        mins = min(mins, cap)
    s.paused_minutes = int(s.paused_minutes or 0) + mins
    s.paused_since = None
    return mins


def effective_paused_minutes(db: Session, s: ParkingSession, at: datetime) -> int:
    """Төлбөр тооцоход хасагдах минут — хуримтлагдсан + ЯВЖ БУЙ зогсолт.

    Session-ийг ӨӨРЧЛӨХГҮЙ (зөвхөн уншина) — жагсаалт/урьдчилсан тооцоонд
    аюулгүй дуудагдана. Талбарыг getattr-аар уншина: `session_fee_info` нь
    тестүүдэд хиймэл (ORM бус) session обьекттой ч дуудагддаг.
    """
    total = int(getattr(s, "paused_minutes", 0) or 0)
    since = getattr(s, "paused_since", None)
    if since:
        # Явж буй зогсолт = доторх гарах уншилтаар хараахан батлагдаагүй. Гадна
        # гарц дээр `close_open_pause` ИЖИЛ хязгаараар барагдуулна — урьдчилсан
        # дүн гарц дээр өөрчлөгдөхгүй (модулийн docstring-ийн хязгаарын дүрэм).
        cap = unverified_cap_minutes(db, s.site_id)
        mins = max(0, int((at - since).total_seconds() // 60))
        total += min(mins, cap) if cap else mins
    return total


def on_inner_entry(db: Session, site: ParkingSite, plate: str, at: datetime,
                   child: ParkingSession | None = None, exact_only: bool = False) -> bool:
    """Доторх зогсоолд ОРЛОО — гадна session-ий тоолуурыг зогсооно.

    Давхар уншилтад найдвартай: аль хэдийн зогссон бол дахин эхлүүлэхгүй.
    Гадна session олдохгүй бол (гадна орох уншилт алдагдсан) юу ч хийхгүй —
    хасах хугацаа байхгүй тул доторх зогсолт энгийнээр явна.
    Яг дугаар олдохгүй бол OCR-ойролцоо ГАНЦ гадна сешн (модулийн docstring);
    тохирвол `child` (энэ уншилтаар ШИНЭЭР үүссэн доторх сешн)-ийн дугаарыг засна.
    Гадна сешн гарцад төлбөр хүлээж байсан бол OPEN болгоно (`_revert_refused_exit`).
    exact_only: ойролцоо тохирол хийхгүй (гараар бүртгэл — `manual_inner_entry`).
    """
    if exact_only:
        parent, fuzzy = _parent_session(db, site, plate), False
    else:
        parent, fuzzy = match_parent(db, site, plate, at, paused=False)
    if parent is None:
        log.info("[nested] %s: «%s»-д орлоо ч гадна зогсоолд идэвхтэй бүртгэл алга — "
                 "тоолуур зогсоохгүй", plate, site.name)
        return False
    # Гарцад татгалзсан (AWAITING_PAYMENT) машин зогсолттой байсан ч (доторх гарах
    # уншилт нь алдагдаж гарц руу очсон) буцаалтыг шалгана — эс бол царцсан дүн
    # 2 цагийн дараа өр болж, жинхэнэ гарцад «орох уншилтгүй» хураамж нэмэгдэнэ.
    if parent.paused_since and parent.status != "AWAITING_PAYMENT":
        return False   # аль хэдийн зогссон (давхар уншилт)
    # Түгжээ ЗӨВХӨН буцаалтад (төлбөртэй уралдахгүйн тулд). OPEN сешнд e7c5a8a-тай
    # ижил (бичилт commit дээр хүлээнэ): skip_locked-оор алгасвал богино түгжээ
    # (зураг/эмнэлгийн UPDATE) зогсолтыг чимээгүй хаяж байв (хяналт F1: 0₮ → 3,000₮).
    if parent.status == "AWAITING_PAYMENT" and _lock_parent(db, parent) is None:
        log.warning("[nested] %s: гадна «%s» сешн түгжээтэй (төлбөр боловсруулагдаж буй) — "
                    "буцаалт хийсэнгүй", plate, parent.plate_number)
        return False
    if parent.status == "AWAITING_PAYMENT":
        seen = parent.last_exit_seen_at or parent.payment_wait_started_at
        if _revert_refused_exit(db, parent, at) and parent.paused_since and seen:
            # Гадна гарах уншилт = машин тэр үед доторх зогсоолоос ГАРСАН байсны
            # нотолгоо (доторх гарах уншилтгүй → батлагдаагүй хязгаар); шинэ зогсолт
            # энэ уншилтаас.
            settle_pause(parent, seen, unverified_cap_minutes(db, parent.site_id))
    if parent.paused_since:
        return False   # аль хэдийн зогссон (давхар уншилт / буцаалт хийгдээгүй)
    # Гадна сешн эхлэхээс ӨМНӨХ цагаас зогсоохгүй: хуучин (гарах уншилтгүй
    # үлдсэн) доторх сешний entry_time энд ирж болно — хасалт гадна зогсолтоос
    # хэтэрч бүх гадна хугацаа үнэгүй болдог байв (8810УКР 2026-10-13).
    parent.paused_since = max(at, parent.entry_time) if parent.entry_time else at
    if fuzzy:
        _note_fuzzy(db, site, parent, plate, child, "inner_entry")
    log.info("[nested] %s: «%s»-д орлоо — гадна «%s»-ын тоолуур зогслоо",
             plate, site.name, getattr(parent.site, "name", parent.site_id))
    return True


def manual_inner_entry(db: Session, site: ParkingSite, plate: str, username: str,
                       child: ParkingSession | None = None) -> bool:
    """Доторх зогсоолд ГАРААР бүртгэсэн машин (sessions_router.manual_entry).

    Зогсолт ОДООГООС эхэлнэ — операторын оруулсан (хойш татаж болох) орсон цагаас
    БИШ: эс бол кассын эрхтэй хэн ч гадна тоолуурыг гадна сешний эхлэл хүртэл ухрааж
    дүнг тасалж чадна (хяналт F3: 5,000 → 1,000₮, 24ц/батлагдсан гарцтай бол 0).
    Зөвхөн ЯГ дугаар (гар оруулгад OCR андуурал байхгүй). `NESTED_MANUAL_PAUSE` аудит."""
    now = datetime.utcnow()
    if not on_inner_entry(db, site, plate, now, child=child, exact_only=True):
        return False
    parent = _parent_session(db, site, plate)
    db.add(AuditLog(username=username, action="NESTED_MANUAL_PAUSE", entity="session",
                    entity_id=parent.id if parent is not None else None,
                    detail={"plate": plate, "child_site": site.name,
                            "child_session_id": child.id if child is not None else None,
                            "paused_since": (parent.paused_since.isoformat()
                                             if parent is not None and parent.paused_since
                                             else None)}))
    return True


def on_inner_exit(db: Session, site: ParkingSite, plate: str, at: datetime,
                  parent: ParkingSession | None = None,
                  child: ParkingSession | None = None) -> int:
    """Доторх зогсоолоос ГАРЛАА — гадна session-ий тоолуурыг үргэлжлүүлнэ.

    `parent` өгөөгүй бол яг дугаараар. Олдохгүй бол хаагдаж буй доторх сешн
    (`child`) нь орохдоо тохирох байсан OCR-ойролцоо ГАНЦ ЗОГССОН гадна сешн —
    доторх сешний орсон цаг гадна орсноос `_FUZZY_WINDOW` дотор (дугаар нь
    засагдаагүй үлдсэн тохиолдол: тэр дугаартай идэвхтэй доторх сешн байсан г.м).
    Ингэж хатуу байх шалтгаан: гадна орох уншилт нь алдагдсан ӨӨР машины доторх
    сешн хаагдахад ойролцоо дугаартай машины зогсолтыг тасалж илүү нэхэхгүй."""
    if parent is None:
        parent, fuzzy = match_parent(db, site, plate, at, paused=True)
        if fuzzy and (child is None or child.entry_time is None
                      or not _seen_outside(parent, child.entry_time)):
            parent = None
        elif fuzzy:
            log.info("[nested] %s: «%s» гарах — OCR-ойролцоо гадна «%s» сешнийг үргэлжлүүлнэ",
                     plate, site.name, parent.plate_number)
    if parent is None or not parent.paused_since:
        return 0
    if parent.paused_since > at:
        # Энэ гарц нь ӨМНӨХ айлчлалынх (camera_sync/албадан хаалт өнгөрсөн
        # цагаар дууддаг) — түүнээс хойш эхэлсэн шинэ зогсолтыг бүү хаа.
        return 0
    # Жинхэнэ доторх ГАРАХ уншилт — БАТЛАГДСАН зогсолт: тохируулсан хязгаар
    # (0 = хязгааргүй). Модулийн docstring-ийн хязгаарын дүрмийг үз.
    mins = settle_pause(parent, at, child_cap_minutes(db, site))
    log.info("[nested] %s: «%s»-аас гарлаа — гадна тоолуур %d минут зогссон "
             "(нийт %d мин хасагдана)", plate, site.name, mins, parent.paused_minutes)
    return mins


def restore_parent_pause(db: Session, child_site: ParkingSite, child: ParkingSession,
                         since: datetime | None) -> bool:
    """ДОТООД (хүүхэд) сешн «хуурамч гарц»-аас АВТО/гараар СЭРГЭЭГДЛЭЭ — машин
    дотроо байсаар байжээ. Гадна тоолуурыг тэр «гарц»-ын цагаас дахин зогсооно.

    Доторх ГАРАХ камер дөнгөж ОРЖ буй машиныг уншиж (100 айл 8507УБР
    2026-10-12: орсноос 15с-ийн дараа) хүүхэд сешн хаагдахад `on_inner_exit`
    гадна тоолуурыг үргэлжлүүлдэг. Жинхэнэ гарах уншилтаар хүүхэд сешн сэргэхэд
    гадна тоолуур ҮРГЭЛЖИЛСЭЭР байсан тул дотор өнгөрүүлсэн 7 цагийг нэхдэг байв
    (4688УБН, 7969УАА, 8507УБР — 70,000₮; зөв дүн 0₮).

    `since` (хуурамч гарцын цаг) хүртэлх минут `paused_minutes`-д аль хэдийн
    орсон тул давхар тоологдохгүй; дараагийн `on_inner_exit` хязгаараар барагдуулна.
    Зөвхөн тэр үед идэвхтэй байсан, одоо OPEN, зогсоогүй гадна сешнд, доторх
    хаалтаар нэвтрэх эрхтэй машинд (`_admitted`)."""
    if since is None or not _admitted(db, child_site, child.plate_number):
        return False
    parent = _parent_session(db, child_site, child.plate_number)
    if parent is None and child.entry_time is not None:
        # Доторх сешн OCR-ойролцоо дугаартай үлдсэн (засагдаагүй) — орох үеийнх нь
        # ижил дүрмээр (ганц, зогсоогүй, гадна камерт 10 мин дотор харагдсан)
        parent = _fuzzy_parent(db, child_site, child.plate_number, child.entry_time,
                               paused=False, window=True)
    if (parent is None or parent.status != "OPEN" or parent.paused_since
            or parent.entry_time > since):
        return False
    parent.paused_since = since
    log.info("[nested] %s: «%s» сешн сэргэв — гадна тоолуурыг %s-аас дахин зогсоов",
             child.plate_number, child_site.name, since)
    return True


def reapply_child_pauses(db: Session, s: ParkingSession, since: datetime | None,
                         now: datetime) -> int:
    """ГАДНА (эцэг) сешн сэргээгдлээ — хаалттай байсан хугацаанд (since..now)
    машин доторх зогсоолд байсан бол тэр хугацааг хасалтад нэмнэ.

    Гадна ГАРАХ камер дөнгөж орж буй машиныг уншиж гадна сешн FREE хаагдана →
    машин доторх зогсоолд ороход идэвхтэй гадна сешн алга тул тоолуур зогсохгүй
    (`on_inner_entry`) → жинхэнэ гарахад гадна сешн «хуурамч гарц» гэж сэргэж
    дотор өнгөрүүлсэн бүх цагийг нэхдэг байв (100 айл 4216УБО 2026-10-07: 11ц,
    25,000₮; зөв дүн 0₮).

      • ДОТООД гарах уншилттай (exit_confirmed) хаагдсан хүүхэд сешн — давхцсан
        хэсгийг (интервалуудын нэгдэл) БАТЛАГДСАН хязгаараар `paused_minutes`-д нэмнэ.
      • Нээлттэй хүүхэд сешн — `paused_since`-ийг эхлүүлнэ; гадна гарц дээр
        `close_open_pause` батлагдаагүй хязгаараар барагдуулна (live замтай ижил).
      • Гарах уншилтгүй албадан хаагдсан хүүхэд сешн — цаг нь таамаг тул тооцохгүй.
      • Доторх хаалтаар нэвтрэх эрхгүй машины (татгалзсан/хориглогдсон) сешн —
        машин дотогш ороогүй (`_admitted`), тооцохгүй.
      • ӨМНӨХ айлчлалаас үлдсэн (гадна сешнээс өмнө нээгдсэн) хүүхэд сешн —
        зөвхөн энэ айлчлалын доторх ОРОХ уншилтаас (`existing` замаар дахин
        ашиглагдсан) тоолно; уншилтгүй бол машин гадаа байсан гэж үзнэ. Эс бол
        гарах уншилтгүй үлдсэн phantom сешн гадаа зогссон хугацааг хасдаг (8810УКР
        хэв маяг; on_inner_entry-ийн «ОДООГООС» дүрэмтэй ижил).
    Цонх нь ямагт [сүүлийн хаалт, одоо] тул дахин сэргээхэд давхар тоологдохгүй.
    Хүүхэд зогсоолгүй (НЭГ site) зогсоолд юу ч хийхгүй."""
    if since is None or since >= now:
        return 0
    kids = {k.id: k for k in db.query(ParkingSite)
            .filter(ParkingSite.parent_site_id == s.site_id).all()}
    if not kids:
        return 0
    rows = (db.query(ParkingSession)
            .filter(ParkingSession.site_id.in_(list(kids)),
                    ParkingSession.plate_number == s.plate_number,
                    ParkingSession.entry_time < now,
                    (ParkingSession.exit_time.is_(None)) | (ParkingSession.exit_time > since))
            .all())
    visit_start = s.entry_time - _STALE_TOLERANCE if s.entry_time else None
    admitted: dict[str, bool] = {}
    spans, open_start = [], None
    for c in rows:
        if c.site_id not in admitted:
            admitted[c.site_id] = _admitted(db, kids[c.site_id], s.plate_number)
        if not admitted[c.site_id]:
            continue
        start = c.entry_time
        if visit_start is not None and c.entry_time < visit_start:
            start = (db.query(func.min(LprEvent.created_at))
                     .filter(LprEvent.site_id == c.site_id,
                             LprEvent.plate_number == s.plate_number,
                             LprEvent.lane_dir == "entry",
                             LprEvent.accepted.is_(True),
                             LprEvent.created_at >= visit_start,
                             LprEvent.created_at <= (c.exit_time or now))
                     .scalar())
            if start is None:
                continue
        start = max(start, since)
        if c.exit_time is None:
            if c.status in _ACTIVE:
                open_start = start if open_start is None else min(open_start, start)
            continue
        if c.exit_confirmed:
            spans.append((start, min(c.exit_time, now)))
    cap = pause_cap_minutes(db, s.site_id)
    added, cur = 0, None
    for a, b in sorted(spans) + [(None, None)]:          # интервалуудын нэгдэл
        if cur and (a is None or a > cur[1]):
            mins = max(0, int((cur[1] - cur[0]).total_seconds() // 60))
            added += min(mins, cap) if cap else mins
            cur = None
        if a is not None and b > a:
            cur = (a, b) if cur is None else (cur[0], max(cur[1], b))
    if added:
        s.paused_minutes = int(s.paused_minutes or 0) + added
    if open_start is not None and not s.paused_since:
        s.paused_since = open_start
    if added or open_start is not None:
        log.info("[nested] %s: гадна сешн сэргэв — доторх %d мин хасалтад нэмэв%s",
                 s.plate_number, added,
                 f", тоолуур {open_start}-аас зогссон" if open_start is not None else "")
    return added


def credit_backfilled_child(db: Session, child_site: ParkingSite, plate: str,
                            start: datetime, end: datetime | None,
                            child: ParkingSession | None = None) -> int:
    """camera_sync-аар НӨХӨЖ үүсгэсэн доторх сешн — live `on_inner_entry`
    дуудагдаагүй тул гадна тоолуур огт зогсоогүй (100 айл 10-07..10-12: 8333УАО,
    3016УНЕ, 5508УАТ — дотор 8–9ц, 68,000₮ нэхэмжилсэн, зөв дүн 0₮).

    Гадна сешн тэр үед идэвхтэй (start-аас өмнө орсон) бол:
      • end байхгүй (машин дотроо) → тоолуурыг start-аас зогсооно;
      • end байгаа (логийн ГАРАХ уншилт = батлагдсан) → [start, end] минутыг
        тохируулсан хязгаараар шууд нэмнэ.
    Гадна тоолуур аль хэдийн зогссон бол давхар тооцохгүй. Логт ОРОХ уншилт
    байгаа ч доторх хаалтаар нэвтрэх эрхгүй машинд (`_admitted`) хасалт өгөхгүй
    — камер татгалзсан машиныг ч уншдаг.
    Яг дугаар олдохгүй бол live-тай ижил OCR-ойролцоо дүрэм (`start`-аар цонх);
    тохирвол `child`-ийн дугаарыг гадныхаар засна."""
    parent, fuzzy = match_parent(db, child_site, plate, start, paused=False)
    if parent is None or parent.entry_time > start or parent.paused_since:
        return 0
    if not _admitted(db, child_site, plate):
        return 0
    if fuzzy:
        _note_fuzzy(db, child_site, parent, plate, child, "camera_sync")
    if end is None:
        parent.paused_since = start
        log.info("[nested] %s: «%s»-ийн нөхсөн бүртгэл — гадна тоолуурыг %s-аас зогсоов",
                 plate, child_site.name, start)
        return 0
    mins = max(0, int((end - start).total_seconds() // 60))
    cap = child_cap_minutes(db, child_site)
    mins = min(mins, cap) if cap else mins
    parent.paused_minutes = int(parent.paused_minutes or 0) + mins
    log.info("[nested] %s: «%s»-ийн нөхсөн бүртгэл — гадна сешнд %d мин хасалт нэмэв",
             plate, child_site.name, mins)
    return mins


def close_open_pause(db: Session, s: ParkingSession, at: datetime) -> int:
    """Гадна session хаагдахад үлдсэн зогсолтыг хаана.

    Машин гадна гарцад уншигдсан гэдэг нь доторх зогсоолоос гарсан гэсэн үг —
    доторх ГАРАХ уншилт алдагдсан ч гэсэн. Хязгаараар таслагдана.

    Талбарыг getattr-аар уншина: `close_session_forced` нь тестүүдэд хиймэл
    (ORM бус) session обьекттой ч дуудагддаг — `effective_paused_minutes`-тэй ижил.
    """
    if not getattr(s, "paused_since", None):
        return 0
    # Доторх гарах уншилтгүй → БАТЛАГДААГҮЙ зогсолт (`unverified_cap_minutes`)
    mins = settle_pause(s, at, unverified_cap_minutes(db, s.site_id))
    log.info("[nested] %s: гадна гарцад уншигдлаа — доторх гарах уншилтгүй үлдсэн "
             "зогсолтыг %d минутаар хаав", s.plate_number, mins)
    return mins


def same_site_session(db: Session, site_id: str, plate: str) -> ParkingSession | None:
    """НЭГ зогсоол доторх давхар зогсоолд хэрэглэнэ — тухайн зогсоолын идэвхтэй session."""
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id,
                    ParkingSession.plate_number == plate,
                    ParkingSession.status.in_(_ACTIVE))
            .order_by(ParkingSession.entry_time.desc()).first())


def pause_session(s: ParkingSession | None, at: datetime) -> bool:
    """Тоолуурыг зогсооно. Давхар уншилтад найдвартай (зогссоныг дахин эхлүүлэхгүй)."""
    if s is None or s.paused_since:
        return False
    s.paused_since = at
    return True


def resume_session(s: ParkingSession | None, at: datetime, cap: int) -> int:
    """Тоолуурыг үргэлжлүүлж, зогссон минутыг хуримтлуулна."""
    if s is None or not s.paused_since:
        return 0
    return settle_pause(s, at, cap)


def inside_nested_count(db: Session, site_id: str) -> int:
    """Тухайн ГАДНА зогсоолын машинуудаас хэд нь ОДОО доторх зогсоолд байгаа вэ."""
    return (db.query(ParkingSession)
            .filter(ParkingSession.site_id == site_id,
                    ParkingSession.status.in_(_ACTIVE),
                    ParkingSession.paused_since.isnot(None)).count())
