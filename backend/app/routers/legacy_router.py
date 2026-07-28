"""Хуучин easy-park платформын агентын урсгалыг хүлээн авах (телеметр цуглуулалт).

Юуны учир (2026-07-28): хуучин системийн агент(ууд) сервер хаягаа энэ сервер лүү
заасан хэвээр тул `/api/v1.0/parkings/set-gate-status` руу минут тутам хаалтныхаа
төлөвийг PUT хийдэг. Өмнө нь 404 болж лог бөглөдөг байв. Одоо хүлээж аваад:

  • Ямар ХОСТНЭЙМ-ээр ирж байгааг (Host толгой) бүртгэнэ — аль DNS/NAT хуучин
    систем рүү заах ёстой байсныг олоход (сүлжээний админд өгөх баримт).
  • Gate бүрийн төлөв ӨӨРЧЛӨГДӨХ бүрд AuditLog-д хадгална — хуучин систем
    «Time out», «Password is not correct», «The account has been locked» гэж
    ӨӨРӨӨ мэдээлж буй нь камерын түгжээ/удаашралын маргаанд шууд нотолгоо.
    (Минут тутмын давталтыг бүгдийг нь хадгалбал аудит бөглөрнө — зөвхөн
    өөрчлөлтийг хадгалж, бүрэн урсгал нь journald-д INFO-оор үлдэнэ.)

Энэ нь хуучин платформыг ДУУРАЙХГҮЙ — зөвхөн ирсэн мэдээллийг бүртгэж {"ok"}
буцаана; хаалт удирдах ямар ч хариу өгөхгүй.

Харах: Админ → Аудит (action=LEGACY_GATE_STATUS) эсвэл:
  sudo -u postgres psql -d parking -c "SELECT created_at, entity_id, detail FROM audit_logs WHERE action='LEGACY_GATE_STATUS' ORDER BY created_at DESC LIMIT 20;"
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AuditLog

log = logging.getLogger("parking.legacy")

router = APIRouter(prefix="/api/v1.0", tags=["legacy"])

# gate_id -> (status, message): өөрчлөлтийг л AuditLog-д бичихэд ашиглана
_last_state: dict[str, tuple[str, str]] = {}
# Урьд харж байгаагүй зам бүрийг нэг л удаа бүртгэнэ (агент өөр юу дууддагийг олох)
_seen_paths: set[str] = set()


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "")


@router.api_route("/parkings/set-gate-status", methods=["PUT", "POST", "GET"])
async def set_gate_status(request: Request, db: Session = Depends(get_db)):
    q = request.query_params
    gate_id = q.get("GateId") or q.get("gateId") or "?"
    status = q.get("status") or "?"
    message = q.get("errorMessage") or ""
    host = request.headers.get("host", "")
    ip = _client_ip(request)
    log.info("legacy gate: id=%s status=%s host=%s ip=%s msg=%s",
             gate_id, status, host, ip, message[:200])
    state = (status, message)
    if _last_state.get(gate_id) != state:
        _last_state[gate_id] = state
        db.add(AuditLog(username="legacy-agent", action="LEGACY_GATE_STATUS",
                        entity="gate", entity_id=str(gate_id)[:60],
                        detail={"status": status, "message": message[:500],
                                "host": host, "source_ip": ip},
                        ip_address=ip[:50]))
        db.commit()
    return {"ok": True}


@router.api_route("/parkings/{rest:path}", methods=["PUT", "POST", "GET"])
async def legacy_catchall(rest: str, request: Request, db: Session = Depends(get_db)):
    """Агент ӨӨР ямар зам дууддагийг илрүүлнэ — зам бүрийг нэг л удаа аудитад бичнэ."""
    host = request.headers.get("host", "")
    ip = _client_ip(request)
    log.info("legacy call: /api/v1.0/parkings/%s host=%s ip=%s query=%s",
             rest, host, ip, str(dict(request.query_params))[:200])
    if rest not in _seen_paths:
        _seen_paths.add(rest)
        db.add(AuditLog(username="legacy-agent", action="LEGACY_UNKNOWN_PATH",
                        entity="path", entity_id=rest[:60],
                        detail={"host": host, "source_ip": ip,
                                "query": dict(request.query_params)},
                        ip_address=ip[:50]))
        db.commit()
    # Хуучин системийг дуурайхгүй — ямар ч командын хариу өгөхгүй
    return {"ok": False, "note": "legacy endpoint not supported"}
