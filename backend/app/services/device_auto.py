"""Төхөөрөмжийн авто тохиргоо.

Бүх-нэг-дор Dahua ANPR кит: камер хаалтаа ӨӨРИЙН релеэр (NO1/NO2) удирддаг тул
эгнээ бүр камер + хаалт ХОС байх ёстой. Энэ модуль:

1. ensure_lane_barriers() — идэвхтэй камер бүрд ижил эгнээний идэвхтэй barrier
   байгааг баталгаажуулна: устгагдсан бол СЭРГЭЭНЭ, огт байхгүй бол ҮҮСГЭНЭ.
   Startup бүрт + камер шинээр бүртгэх бүрт ажиллана — админаас нэмэлт ажил шаардахгүй.
2. fetch_camera_model() — камерын марк/загварыг өөрөөс нь (magicBox CGI) татна.
"""
import logging
import secrets

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Device
from .device_auth import camera_credentials

log = logging.getLogger("parking.device_auto")


def fetch_camera_model(ip: str, device=None) -> str | None:
    """Dahua камерын загварыг өөрөөс нь асууна: /cgi-bin/magicBox.cgi?action=getDeviceType
    → "type=IPMECS-2234-IZ". Хүрэхгүй/өөр брэнд бол None (алдаа шидэхгүй)."""
    if not ip:
        return None
    try:
        r = httpx.get(f"http://{ip}/cgi-bin/magicBox.cgi?action=getDeviceType",
                      auth=httpx.DigestAuth(*camera_credentials(device)),
                      timeout=4)
        if r.status_code == 200 and "type=" in r.text:
            return r.text.split("type=", 1)[1].strip().splitlines()[0][:80] or None
    except Exception:  # noqa: BLE001 — камер унтарсан ч бүртгэл саадгүй үргэлжилнэ
        pass
    return None


def barrier_matches_camera(cam: Device, bar: Device) -> bool:
    """Энэ хаалт тэр камерын ХОС мөн үү — цэвэр дүрэм (DB-гүй, тестээр хамрагдана).

    Гурван шинж ЦӨМ таарна: чиглэл, ЭГНЭЭ, дотоод/гадна. Нэг хаалтаар орох/гарах
    ХОЁУЛАНГ барьдаг зогсоолын `lane_dir="both"` хаалтыг зөвшөөрнө — эс бол тэнд
    хэрэггүй хоёр дахь хаалт үүснэ. Эгнээг тусгайлан
    онцлох шалтгаан: 2 эгнээнээс олон эгнээтэй зогсоолд зөвхөн чиглэлээр
    тааруулбал эгнээ 3-ын камер эгнээ 1-ийн хаалтыг «өөрийнх» гэж үзэж,
    шинэ хаалт үүсэхгүй өнгөрдөг (2026-08-28 Маршил).
    """
    return (bar.device_type == "barrier"
            and bar.lane_dir in (cam.lane_dir, "both")
            and bar.lane_no == cam.lane_no
            and bool(bar.nested_inner) == bool(cam.nested_inner))


def ensure_lane_barriers(db: Session) -> dict:
    """Идэвхтэй камер бүрд ижил зогсоол+эгнээний идэвхтэй barrier байлгана.
    Буцаана: {"restored": n, "created": n, "moved": n} — лог/мэдээлэлд."""
    restored = created = moved = 0
    cams = db.query(Device).filter(Device.device_type == "camera",
                                   Device.status == "active").all()
    for c in cams:
        # ЭГНЭЭ БҮРД өөрийн хаалт: камер бүр өөрийн эгнээний хаалттай хосолно.
        #
        # ТҮҮХ: 2026-08-02-т lane_no тулгалтыг ХАССАН байсан — TESTZOGSOOL дээр
        # нэг чиглэлд 2 хаалт үүсчихсэн тул. Гэвч жинхэнэ шалтгаан нь НЭГ эгнээнд
        # олон камер байсан явдал байв (Рашбулаг ЭТТ: 1/entry дээр 3 камер) бөгөөд
        # түүнийг 2026-08-07-нд нэмсэн доорх `db.flush()` аль хэдийн зассан: эхний
        # камер хаалтаа үүсгээд flush хийхэд ижил эгнээний дараагийн камер түүнийг
        # ОЛНО. Тиймээс lane_no тулгалт давхардал үүсгэхээ больсон.
        #
        # Тулгалтгүй үлдээсэн нь 2 эгнээнээс ОЛОН эгнээтэй зогсоолыг эвддэг:
        # эгнээ 3-т камер нэмэхэд «эгнээ 1-д орох хаалт байна» гээд хаалт ҮҮСГЭХГҮЙ
        # өнгөрдөг (2026-08-28 Маршил: 4 камер — 2 хаалт, Хаалтны удирдлагад
        # эгнээ 3,4 огт харагдахгүй). Улмаар `_find_barrier` эгнээ 3-ын уншилтаар
        # эгнээ 1-ийн хаалтыг нээж, машингүй газар хаалт хөдөлдөг.
        site_bars = db.query(Device).filter(
            Device.site_id == c.site_id, Device.device_type == "barrier",
        ).order_by(Device.created_at, Device.id).all()
        if any(b.status == "active" and barrier_matches_camera(c, b) for b in site_bars):
            continue
        # 0) Камерын ЭГНЭЭГ СОЛИХОД хаалт нь ард нь дагаж явна.
        # Эгнээгээр таардаггүй болмогц ШИНЭ хаалт үүсгэвэл хуучин эгнээнд өнчин
        # «Орох хаалт (авто)» үлдэж, админ гараар устгах шаардлагатай болдог
        # (2026-08-07 Рашбулаг ЭТТ: эгнээ 2 болон 3 дээр хоёр ширхэг үүссэн).
        # Тухайн чиглэлд ИДЭВХТЭЙ камергүй үлдсэн хаалтыг үүсгэхийн оронд зөөнө.
        # 2026-08-28: lane_no тулгалт бүх камерт үйлчилдэг болсон тул энэ зөөлт
        # ч зөвхөн nested биш, БҮХ камерт хэрэгтэй — эс бол эгнээ сольсон ердийн
        # зогсоол бүрд өнчин хаалт хуримтлагдана.
        orphan = next(
            (b for b in db.query(Device).filter(
                Device.site_id == c.site_id, Device.device_type == "barrier",
                Device.lane_dir == c.lane_dir,
                Device.nested_inner.is_(bool(c.nested_inner)),
                Device.status == "active").all()
             if not db.query(Device).filter(
                 Device.site_id == c.site_id, Device.device_type == "camera",
                 Device.nested_inner.is_(bool(c.nested_inner)),
                 Device.status == "active",
                 Device.lane_dir == b.lane_dir, Device.lane_no == b.lane_no).first()),
            None)
        if orphan is not None:
            log.info("хаалт «%s» эгнээ %s → %s руу зөөв (камер шилжсэн)",
                     orphan.name, orphan.lane_no, c.lane_no)
            orphan.lane_no = c.lane_no
            db.flush()
            moved += 1
            continue
        # 1) Устгагдсан хос байвал сэргээнэ (device_key, тохиргоо хэвээр)
        deleted_bar = next((b for b in reversed(site_bars)
                            if b.status == "deleted" and barrier_matches_camera(c, b)), None)
        if deleted_bar:
            deleted_bar.status = "active"
            restored += 1
            continue
        # 2) Огт байхгүй бол камерынхаа эгнээнд шинээр үүсгэнэ
        name = "Орох хаалт" if c.lane_dir == "entry" else "Гарах хаалт"
        if c.nested_inner:
            name = "Дотор " + name.lower()
        db.add(Device(site_id=c.site_id, name=f"{name} (авто)", device_type="barrier",
                      vendor="Dahua", model="DZBL-A / DZE-BL", ip_address="",
                      lane_no=c.lane_no, lane_dir=c.lane_dir, auto_open=False,
                      nested_inner=bool(c.nested_inner),
                      device_key=f"barrier-{secrets.token_hex(8)}"))
        # ЗААВАЛ flush: SessionLocal нь autoflush=False тул flush хийхгүй бол
        # дөнгөж нэмсэн хаалт ДАРААГИЙН давталтын query-д ХАРАГДАХГҮЙ бөгөөд
        # нэг чиглэлд хэдэн камер байна, төдөн хаалт үүснэ. Рашбулаг ЭТТ-д
        # 1/entry эгнээнд 3 камер байсан тул 3 «Орох хаалт (авто)» үүсч байв
        # (2026-08-07 туршилтаар баригдсан).
        db.flush()
        created += 1
    if restored or created or moved:
        db.commit()
        log.info(f"хаалт баталгаажуулалт: {restored} сэргээв, {created} шинээр үүсгэв, "
                 f"{moved} зөөв")
    # Хосолол дууссаны ДАРАА реле олдохгүй үлдсэн хаалтыг ЧАНГА зарлана.
    # Ийм хаалт машин ирэхэд команд ч үүсгэдэггүй тул `barrier_commands`-аас
    # хэзээ ч харагдахгүй — цорын ганц дохио нь энэ лог ба UI-ийн улаан тэмдэг
    # (2026-08-26 Рашбулаг ЭТТ: доторх 2 хаалт 33 цаг чимээгүй үхсэн).
    broken = relay_broken(db)
    for b in broken:
        log.error("ХААЛТ РЕЛЕГҮЙ: «%s» (%s, эгнээ %s/%s, дотоод=%s) — машин ирэхэд "
                  "НЭЭГДЭХГҮЙ. Тохиргоо → Төхөөрөмж дээр ижил эгнээний%s камерыг "
                  "бүртгэ/тааруул.", b.name, b.site.name if b.site else b.site_id,
                  b.lane_no, b.lane_dir, bool(b.nested_inner),
                  " ДОТООД" if b.nested_inner else "")
    return {"restored": restored, "created": created, "moved": moved,
            "relay_broken": [b.id for b in broken]}


def relay_broken(db: Session) -> list[Device]:
    """Реле олдохгүй идэвхтэй хаалтууд — тохиргооны эрүүл мэндийн шалгалт."""
    from .barrier import relay_note
    return [b for b in db.query(Device).filter(
        Device.device_type == "barrier", Device.status == "active").all()
        if relay_note(db, b)]
