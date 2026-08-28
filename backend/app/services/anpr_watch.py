"""ANPR-ийн «чимээгүй үхэл» — камер ОНЛАЙН мөртлөө дугаар илгээхээ болих.

Юуны учир (2026-08-28 аудитаар тогтоов): системд гурван төрлийн камерын гэмтэл
байдаг ч watchdog нь хоёрхон:

  1. Стрим бүрэн үхсэн (`last_seen` хуучирсан) → `camera_recovery` (deadman)
  2. Event амьд, `snapshot.cgi` гацсан        → `camera_health`
  3. Стрим амьд, snapshot зөв, ХАРИН ANPR event зогссон → ХЭН Ч ҮГҮЙ

Гурав дахийг юу ч хардаггүйн шалтгаан: `cgi_poller._touch()` нь стримийн
keep-alive-аар `last_seen`-ийг шинэчилдэг («событиегүй ч онлайн гэж зөв
харагдана»), тиймээс deadman хэзээ ч ажиллахгүй — камер мөнхөд эрүүл харагдана.
Жолоочид энэ нь «дугаараа уншуулсан хэрнээ хаалт нээгдэхгүй» гэж мэдрэгдэнэ:
сервер машин ирснийг ОГТ мэдэхгүй тул команд ч үүсэхгүй.

Хэмжилт (prod, ажлын цаг, «чимээгүй байхад нөгөө камер ≥3 машин уншсан» гэж
батлагдсан тохиолдол): Рашбулаг 59 · Эрэл-13 13 · Туушин 10 · Номадс 9 ·
Соёлын төв 8 · … · Хангарьд 0 · NIC 0. Хамгийн урт 602 минут.

ЯЛГАХ ДОХИО: «энэ камер N минут дугаар уншаагүй БАЙХАД ижил зогсоолын НӨГӨӨ
камер саяхан уншсан». Нөгөө камер уншиж байгаа нь тухайн зогсоолд урсгал БАЙГАА
гэдгийн баримт — иймд чимээгүй байдал нь «машин ирээгүй» биш «камер үхсэн».
Энэ шалгуургүйгээр шөнийн хоосон зогсоол бүрд худал дохио өгнө.

Стрим өөрөө 15 минут тутам дахин холбогддог (`stream_idle`), гэвч прод дээр
91-139 минутын чимээгүй байдал ажиглагдсан — дахин холболт ДАНГААРАА хангалтгүй.
Тиймээс энд илрүүлээд UI-д УЛААН анхааруулга өгч, операторт «Дахин холбох» ба
«Reboot» товчийг гаргаж өгнө. Автомат reboot ЗОРИУДААР хийхгүй: богино
тасалдалд reboot хортой (`camera_auto_reboot` default False).
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import Device, LprEvent

log = logging.getLogger("parking.anpr_watch")

# Энэ хугацаанд дугаар уншаагүй бол сэжигтэй (минут)
SILENCE_MIN = 20
# Нөгөө камерын уншилтыг «саяхан» гэж үзэх хугацаа (минут). SILENCE_MIN-ээс
# БОГИНО байх ёстой — эс бол хоёулаа зэрэг үхсэн үед худал дохио өгнө.
PEER_FRESH_MIN = 10


def anpr_silent(now: datetime, last_plate_at: datetime | None,
                peer_last_plate_at: datetime | None, *,
                online: bool,
                silence_min: int = SILENCE_MIN,
                peer_fresh_min: int = PEER_FRESH_MIN) -> bool:
    """Энэ камер ANPR-аараа үхсэн үү — ЦЭВЭР дүрэм (DB-гүй, тестээр хамрагдана).

    • `online=False` бол ҮГҮЙ: тэр нь стрим бүрэн үхсэн тохиолдол (deadman-ийн
      ажил), энд давхар дохио өгөх нь оношийг бүрхэгдүүлнэ.
    • Нөгөө камер САЯХАН уншсан байх ЗААВАЛ шаардлагатай — «машин ирээгүй»-гээс
      ялгах цорын ганц баримт. Нэг камертай зогсоолд дохио өгөхгүй (peer=None).
    """
    if not online or peer_last_plate_at is None:
        return False
    if (now - peer_last_plate_at) > timedelta(minutes=peer_fresh_min):
        return False                      # зогсоолд урсгал алга — шүүх үндэслэлгүй
    if last_plate_at is None:
        return True                       # огт уншиж байгаагүй ч хөрш нь уншиж байна
    return (now - last_plate_at) > timedelta(minutes=silence_min)


def anpr_note(db: Session, device: Device, now: datetime | None = None) -> str | None:
    """UI-д харуулах тайлбар (асуудалгүй бол None) — `relay_note`-той ижил хэв маяг."""
    if device.device_type != "camera" or device.status == "deleted":
        return None
    now = now or datetime.utcnow()
    online_cutoff = now - timedelta(minutes=5)
    if not (device.last_seen and device.last_seen >= online_cutoff):
        return None                       # офлайн — deadman-ийн ажил, энд биш

    def _last(dev_ids: list[str]) -> datetime | None:
        if not dev_ids:
            return None
        return (db.query(LprEvent.created_at)
                .filter(LprEvent.device_id.in_(dev_ids))
                .order_by(LprEvent.created_at.desc()).limit(1).scalar())

    peers = [c.id for c in db.query(Device).filter(
        Device.site_id == device.site_id, Device.device_type == "camera",
        Device.status == "active", Device.id != device.id).all()]
    mine, theirs = _last([device.id]), _last(peers)
    if not anpr_silent(now, mine, theirs, online=True):
        return None
    quiet = "хэзээ ч" if mine is None else f"{int((now - mine).total_seconds() // 60)} минут"
    return (f"Камер ОНЛАЙН боловч {quiet} дугаар уншаагүй — тэр хугацаанд энэ зогсоолын "
            f"өөр камер машин уншсан тул урсгал БАЙГАА. Өөрөөр хэлбэл камер→сервер "
            f"суваг чимээгүй тасарсан: машин ирэхэд сервер МЭДЭХГҮЙ тул хаалт "
            f"нээгдэхгүй, командын бүртгэл ч үүсэхгүй. Эхлээд «Дахин холбох», "
            f"засрахгүй бол «Reboot» дарна уу.")
