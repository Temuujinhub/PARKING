"""Dahua ITC ANPR (Web 5.0) хаалт удирдлага — RPC2 (JSON-RPC).

Бодит төхөөрөмж дээр баталгаажсан (2026-07-07): энэ загварын камерт CGI
(trafficSnap.cgi гэх мэт) "Not Implemented" өгдөг тул удирдлага нь RPC2-оор явна:

  POST /RPC2_Login  global.login (2 алхамт MD5 challenge) → session
  POST /RPC2        trafficSnap.factory.instance {channel} → object
  POST /RPC2        trafficSnap.openStrobe | closeStrobe | forceBreaking
                    {info:{openType, plateNumber}} + object

Session нь богино настай (keepAliveInterval 60с) тул команд бүрт шинээр
login хийж, дуусаад logout хийнэ — kept-alive session удирдахаас найдвартай.
Session-ийг body("session") + Cookie(WebClientHttpSessionID) + x-api-session
header гурвуулангаар нь дамжуулах шаардлагатай (Web 5.0 firmware).

barrier_mock=True үед бодит төхөөрөмж рүү хүсэлт явуулахгүй, амжилттай гэж
бүртгэнэ (төхөөрөмж холбогдоогүй хөгжүүлэлтийн орчинд).
"""
import asyncio
import hashlib
import logging
import time
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BarrierCommand, Device
from .device_auth import barrier_credentials

log = logging.getLogger("parking.barrier")

RPC_METHODS = {
    "open": "trafficSnap.openStrobe",
    "close": "trafficSnap.closeStrobe",
    "force_open": "trafficSnap.forceBreaking",
}


class DahuaRpcError(RuntimeError):
    pass


class DahuaRpc:
    """Нэг командын хугацаанд амьдрах RPC2 клиент (login → команд → logout)."""

    def __init__(self, client: httpx.AsyncClient, host: str, username: str, password: str):
        self.client = client
        self.base = f"http://{host}"
        self.username = username
        self.password = password
        self.session_id = None
        self._id = 0

    async def _call(self, method: str, params: dict | None = None,
                    url: str = "/RPC2", obj=None) -> dict:
        self._id += 1
        payload = {"method": method, "id": self._id}
        if params is not None:
            payload["params"] = params
        if self.session_id is not None:
            payload["session"] = self.session_id
        headers = {}
        if self.session_id:
            headers = {"x-api-session": str(self.session_id),
                       "Cookie": f"WebClientHttpSessionID={self.session_id}"}
        if obj is not None:
            payload["object"] = obj
        resp = await self.client.post(self.base + url, json=payload, headers=headers)
        return resp.json()

    @staticmethod
    def _md5u(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest().upper()

    async def login(self):
        # 1-р алхам: challenge — result:false + realm/random буцаана (энэ нь хэвийн)
        first = await self._call("global.login", {
            "userName": self.username, "password": "",
            "clientType": "Web3.0", "loginType": "Direct"}, url="/RPC2_Login")
        self.session_id = first.get("session")
        p = first.get("params") or {}
        realm, random = p.get("realm"), p.get("random")
        if realm is None or random is None:
            raise DahuaRpcError(f"login challenge алдаа: {first}")
        inner = self._md5u(f"{self.username}:{realm}:{self.password}")
        pwd = self._md5u(f"{self.username}:{random}:{inner}")
        second = await self._call("global.login", {
            "userName": self.username, "password": pwd, "clientType": "Web3.0",
            "authorityType": "Default", "passwordType": "Default"}, url="/RPC2_Login")
        if not second.get("result"):
            # Dahua нь олон удаагийн буруу нэвтрэлтийн дараа бүртгэлийг ТҮГЖДЭГ.
            # Түүхий JSON биш, юу болсныг ойлгомжтой хэлнэ — оператор нууц үгээ
            # дахин дахин оролдож түгжээг уртасгахаас сэргийлнэ.
            lock = (second.get("params") or {}).get("remainLockSecond")
            if lock:
                raise DahuaRpcError(
                    f"Камер ТҮГЖИГДСЭН байна (олон удаа буруу нууц үг оруулсны улмаас). "
                    f"Дахин оролдох хүртэл {lock} секунд үлдлээ. Нууц үгээ Тохиргоо → "
                    f"Төхөөрөмж хэсэгт зөв болгоод, түгжээ тайлагдтал хүлээнэ үү "
                    f"(буруу нууц үгээр дахин оролдвол түгжээ дахин уртасна).")
            raise DahuaRpcError(f"login амжилтгүй: {second}")
        self.session_id = str(second.get("session", self.session_id))

    async def logout(self):
        try:
            await self._call("global.logout")
        except Exception:
            pass  # logout бүтэлгүйтэх нь командын үр дүнд нөлөөгүй

    async def set_screen(self, text: str) -> dict:
        """LED дэлгэцэнд текст харуулах — trafficParking.setScreenDisplay {Custom}.
        Камерын Web 5.0 клиентийн InoutGeneralConfig/DeviceTest хуудас яг ийм
        дуудлага хийдэг (клиент JS-ээс батлагдсан). Дэлгэц "Managed Mode
        (Platform)" горимд байх шаардлагатай.
        «|» эсвэл «\\n» = мөр таслал (дугаар/төлбөрийг 2 мөрөнд харуулна)."""
        br = settings.screen_line_break.replace("\\n", "\n").replace("\\r", "\r")
        text = text.replace("\\n", "\n").replace("|", "\n").replace("\n", br)
        params = {"Custom": text}
        # Камер «хэдэн секунд харуулах» талбар дэмждэг бол НЭГ команд хангалттай —
        # давталт хэрэггүй болж RPC сесс богиносно (хаалттай мөргөлдөх нь буурна).
        # Ямар талбар дэмжигдэхийг tools/screen_probe.py-ээр камераас нь асууж
        # тогтооно; олдвол .env-д PARKING_SCREEN_HOLD_FIELD/SEC-ээр асаана.
        if settings.screen_hold_sec > 0 and settings.screen_hold_field:
            params[settings.screen_hold_field] = settings.screen_hold_sec
        res = await self._call("trafficParking.setScreenDisplay", params)
        if not res.get("result"):
            raise DahuaRpcError(f"setScreenDisplay амжилтгүй: {res}")
        return res

    async def set_voice(self, text: str) -> dict:
        """Дуут зарлал — trafficParking.setVoiceBroadcast {Custom}."""
        res = await self._call("trafficParking.setVoiceBroadcast", {"Custom": text})
        if not res.get("result"):
            raise DahuaRpcError(f"setVoiceBroadcast амжилтгүй: {res}")
        return res

    async def strobe(self, method: str, channel: int, plate: str = "") -> dict:
        inst = await self._call("trafficSnap.factory.instance", {"channel": channel})
        obj = inst.get("result")
        if not obj:
            raise DahuaRpcError(f"factory.instance амжилтгүй: {inst}")
        open_type = "Always" if method == "trafficSnap.forceBreaking" else settings.barrier_open_type
        res = await self._call(method, {"info": {"openType": open_type,
                                                 "plateNumber": plate or ""}}, obj=obj)
        if not res.get("result"):
            raise DahuaRpcError(f"{method} амжилтгүй: {res}")
        return res


def _resolve_device(db: Session, device: Device) -> tuple[str | None, Device | None]:
    """Команд илгээх IP. Бүх-нэг-дор ITC камер хаалтаа өөрийн реле (NO1/NO2)-ээр
    нээдэг тул хаалт төхөөрөмжид IP байхгүй бол тухайн эгнээний камерын IP-г ашиглана.

    ЧУХАЛ: ижил эгнээний камер олдоогүй үед БУСАД эгнээний камер руу шилжихийг
    хориглоно (өмнө нь тэгдэг байсан) — эс бол орох хаалтын команд гарах камер руу
    очиж БУРУУ хаалт нээнэ. Олон камертай зогсоолд ижил эгнээнийх заавал байх ёстой.

    Буцаах: (ip, тэр IP-тэй төхөөрөмж) — нэвтрэх нэр/нууц үгийг ЯГ ТЭР төхөөрөмжөөс
    авахын тулд (камер бүр өөр нууц үгтэй байж болно)."""
    if device.ip_address:
        return device.ip_address, device
    cams = db.query(Device).filter(
        Device.site_id == device.site_id, Device.device_type == "camera",
        Device.ip_address.isnot(None), Device.ip_address != "",
    ).all()
    # 1) Ижил эгнээний (lane_no) камер — хамгийн зөв
    same_lane = [c for c in cams if c.lane_no == device.lane_no]
    if same_lane:
        return same_lane[0].ip_address, same_lane[0]
    # 2) Зогсоолд ЯГ НЭГ камертай (нэг all-in-one төхөөрөмж орох/гарах хоёуланд) бол түүнийг
    if len(cams) == 1:
        return cams[0].ip_address, cams[0]
    # 3) Олон камертай ч энэ эгнээнийх алга — буруу хаалт нээхээс сэргийлж унана
    return None, None


def _resolve_ip(db: Session, device: Device) -> str | None:
    """Зөвхөн IP хэрэгтэй дуудагчдад — буцаах утга нь өмнөх хэвээр."""
    return _resolve_device(db, device)[0]


async def _execute(db: Session, device: Device, command: str, session_id: str | None,
                   source: str, issued_by: str | None = None, plate: str = "") -> BarrierCommand:
    cmd = BarrierCommand(
        session_id=session_id, device_id=device.id, command=command,
        command_source=source, issued_by=issued_by,
    )
    _inflight = command in ("open", "force_open")
    if _inflight:
        _open_inflight.add(device.id)   # давхар зэрэгцээ командаас сэргийлнэ
    db.add(cmd)
    db.flush()

    ip, target = _resolve_device(db, device)

    if settings.barrier_mock:
        cmd.status = "SUCCESS"
        cmd.response_text = f"MOCK: barrier {command}"
        cmd.executed_at = datetime.utcnow()
        _open_inflight.discard(device.id)
        db.commit()
        return cmd

    if not ip:
        # IP тодорхойгүй бол команд явуулах газаргүй — SUCCESS гэж хуурамчаар
        # тэмдэглэвэл barrier_opened=true болж оператор андуурна.
        lane_ru = "орох" if device.lane_dir == "entry" else "гарах"
        cmd.status = "FAILED"
        cmd.response_text = (
            f"IP олдсонгүй: энэ хаалт ({device.name}) өөрийн IP-гүй бөгөөд ижил "
            f"эгнээний ({lane_ru}) камерын IP ч бүртгэлгүй байна. Тохиргоо → Төхөөрөмж "
            f"дээр {lane_ru} камерын IP-г бүртгэнэ үү (эсвэл хаалтад шууд IP өгнө үү)."
        )
        cmd.executed_at = datetime.utcnow()
        _open_inflight.discard(device.id)
        db.commit()
        return cmd

    # Нэвтрэлт нь командыг ХҮЛЭЭН АВАХ төхөөрөмжийнх (хаалт камерын IP зээлсэн бол камерынх)
    username, password = barrier_credentials(target)
    cmd.status = "FAILED"
    # Машин хаалганы өмнө зогсож байгаа тул нэг удаагийн сүлжээний саатлаар
    # бууж өгөхгүй — timeout/холболтын алдаанд хэд дахин оролдоно.
    attempts = max(1, settings.barrier_retries + 1)
    last_err = ""
    _t0 = time.monotonic()
    # Нэг камерт нэг RPC — дэлгэцтэй мөргөлдөхөөс сэргийлнэ (хаалт тэргүүлэх эрхтэй)
    # НИЙТ хугацааны таслалт. ЧУХАЛ: httpx-ийн timeout нь ХҮСЭЛТ БҮРД үйлчилдэг,
    # харин нэг хаалт нээхэд 5 хүсэлт явдаг (login×2 + factory.instance + strobe +
    # logout). Тиймээс timeout=12 гэдэг нь нэг оролдлого 60 секунд хүртэл үргэлжилж
    # болно гэсэн үг байв — 3 оролдлоготой бол 180с. Production дээр 51275мс
    # (51 секунд) болж бүртгэгдсэн. Одоо оролдлого БҮРИЙГ болон НИЙТ хугацааг
    # asyncio.wait_for-оор хатуу таслана.
    _deadline = time.monotonic() + settings.barrier_total_budget_sec
    async with _BarrierPriority(ip):
      for attempt in range(attempts):
          if attempt:
              if time.monotonic() >= _deadline:
                  last_err = last_err or "нийт хугацаа дууслаа"
                  cmd.response_text = f"{last_err} (нийт {settings.barrier_total_budget_sec:.0f}с хэтэрлээ)"
                  break
              await asyncio.sleep(settings.barrier_retry_delay_sec)
          # Эхний оролдлого богино timeout-тай — хариугүй төхөөрөмж дээр 12с бүтэн
          # хүлээхийн оронд хурдан дахин илгээж хаалтыг эрт нээнэ
          _remaining = _deadline - time.monotonic()
          if _remaining <= 0.2:
              break
          # Оролдлого бүр БОГИНО, харин ОЛОН. Production нотолгоо: MONNIS-ийн камер
          # event стрим барьж байхад RPC-д завгүй болж хариу өгөхөө болино, гэвч
          # дахин оролдоход СЭРГЭДЭГ (10:08:12 — 2-р оролдлогоор амжилттай).
          # Өмнө нь 4с + 12с гэсэн 2 оролдлого л багтдаг байсан тул нийт 15-18
          # секунд хүлээгээд бүтэлгүйтдэг байв. Богино оролдлогоор ижил төсөвт
          # 4 удаа оролдоно — сэргэх магадлал 2 дахин их, хүлээлт 3 дахин бага.
          _timeout = min(_remaining, settings.barrier_attempt_timeout_sec)
          async def _one_attempt():
              """Нэг оролдлого — бүхэлдээ _timeout дотор багтах ёстой."""
              async with httpx.AsyncClient(timeout=_timeout) as client:
                  if command == "open" and settings.barrier_open_path:
                      # Өөр загварын (CGI дэмждэг) төхөөрөмжид зориулсан гар тохиргоо
                      auth = httpx.DigestAuth(username, password)
                      resp = await client.get(f"http://{ip}{settings.barrier_open_path}", auth=auth)
                      body = (resp.text or "").strip()
                      if resp.status_code == 200 and "error" not in body.lower():
                          cmd.status = "SUCCESS"
                      cmd.response_text = f"CGI {resp.status_code}: {body[:200]}"
                  else:
                      rpc = DahuaRpc(client, ip, username, password)
                      await rpc.login()
                      try:
                          res = await rpc.strobe(RPC_METHODS[command],
                                                 settings.barrier_channel, plate)
                          cmd.status = "SUCCESS"
                          cmd.response_text = (f"RPC2 {RPC_METHODS[command]} → {res.get('result')}"
                                               + (f" ({attempt + 1}-р оролдлого)" if attempt else ""))
                      finally:
                          await rpc.logout()

          try:
              # wait_for нь ОРОЛДЛОГЫГ БҮХЭЛД нь таслана (нэг хүсэлт биш) —
              # login+strobe+logout цуврал хүсэлт нийлээд хугацаа хэтрүүлэхээс сэргийлнэ
              await asyncio.wait_for(_one_attempt(), timeout=_timeout)
              if cmd.status == "SUCCESS":
                  break
          except (asyncio.TimeoutError, TimeoutError):
              last_err = f"хугацаа хэтэрлээ ({_timeout:.1f}с)"
              cmd.response_text = (f"{last_err} — {attempt + 1}/{attempts} оролдлого"
                                   if attempt + 1 < attempts
                                   else f"{last_err} ({attempts} удаа оролдсон)")
          except Exception as e:
              last_err = f"{type(e).__name__}: {str(e)[:300]}"
              cmd.response_text = (f"{last_err} — {attempt + 1}/{attempts} оролдлого"
                                   if attempt + 1 < attempts
                                   else f"{last_err} ({attempts} удаа оролдсон)")
    if _inflight:
        _open_inflight.discard(device.id)   # дууслаа — дараагийн оролдлого чөлөөтэй
    cmd.executed_at = datetime.utcnow()
    # Хугацааны хэмжилт — «хаалт удаан нээгдэж байна» гомдлыг тоогоор нотлох
    _ms = int((time.monotonic() - _t0) * 1000)
    cmd.duration_ms = _ms
    if _ms >= settings.barrier_slow_warn_ms or cmd.status != "SUCCESS":
        log.warning("хаалт %s: %s — %dмс (%s, %d оролдлого) %s",
                    command, cmd.status, _ms, source, attempts, (cmd.response_text or "")[:120])
    else:
        log.info("хаалт %s: SUCCESS — %dмс (%s)", command, _ms, source)
    db.commit()
    return cmd


async def open_barrier(db: Session, device: Device, session_id: str | None, source: str,
                       issued_by: str | None = None, plate: str = "",
                       force: bool = False) -> BarrierCommand:
    """Хаалт нээх. force=True үед forceBreaking (албадан онгойлгоод барих)."""
    return await _execute(db, device, "force_open" if force else "open",
                          session_id, source, issued_by, plate)


async def close_barrier(db: Session, device: Device, session_id: str | None = None,
                        source: str = "manual", issued_by: str | None = None) -> BarrierCommand:
    """Хаалт хаах (closeStrobe). Ихэвчлэн гараар — авто хаалт нь газрын
    мэдрэгч/радараар төхөөрөмж талдаа хийгддэг."""
    return await _execute(db, device, "close", session_id, source, issued_by)


# ─── Нэг камерт нэг RPC — дараалуулагч ───────────────────────────────────────
# Dahua ITC камер нэгэн зэрэг цөөхөн RPC2 сесс л зөвшөөрдөг. Хязгаараас хэтэрвэл
# «User or password not valid» гэж ХУДАЛ хариу өгдөг (нууц үг зөв атлаа) —
# 2026-07-28-нд production дээр яг ийм зүйл болж, LED дэлгэц 18 секунд (6 давталт
# × 3с) сесс барьж байхад ирсэн ХААЛТНЫ команд татгалзаж, 10-30 секундын саатал
# үүсгэж байв.
#
# Шийдэл: камерын IP тус бүрд түгжээ — манай систем нэг камерт хоёр RPC-г ХЭЗЭЭ Ч
# зэрэг явуулахгүй. ХААЛТ нь ТЭРГҮҮЛЭХ эрхтэй: дэлгэц түгжээг барьж байвал
# хаалт ирмэгц дэлгэц давталтаа тасалж бууж өгнө. Хаалт нь түгжээг хэт удаан
# хүлээхгүй (max 2с) — авч чадаагүй ч команд явуулна, учир нь хаалт нээх нь
# хамгийн чухал.
_rpc_locks: dict[str, asyncio.Lock] = {}
_barrier_waiting: dict[str, int] = {}     # ip -> хүлээж буй хаалтны командын тоо


def _rpc_lock(ip: str) -> asyncio.Lock:
    lock = _rpc_locks.get(ip)
    if lock is None:
        lock = _rpc_locks[ip] = asyncio.Lock()
    return lock


def barrier_is_waiting(ip: str) -> bool:
    """Тухайн камерт хаалтны команд дараалалд байгаа эсэх (дэлгэц бууж өгөхөд)."""
    return _barrier_waiting.get(ip, 0) > 0


# ─── Давхар «нээх» командыг таслах ───────────────────────────────────────────
# Нэг машин гарахад камер 2-3 удаа уншдаг: 1-р уншилт auto_exit-ээр хаалт нээнэ,
# 2-р уншилт dedup дээр exit_retry-ээр ДАХИН нээхийг оролдоно. DB дэх cooldown
# шалгалт нь SUCCESS болсон командыг хайдаг тул ЯГ ОДОО ЯВЖ БУЙ (хараахан
# дуусаагүй) командыг олж хардаггүй — үр дүнд нь нэг камерт хоёр RPC зэрэг очиж
# хоёулаа удаашрана (production: ганц команд 87-410мс, давхацсан үед 749-985мс,
# нэг тохиолдолд хоёул 15 СЕКУНДЭД timeout болсон).
#
# Тиймээс процессын доторх (агшин зуурын) хамгаалалт: сүүлд ОРОЛДСОН цагийг
# санаж, cooldown дотор давтахгүй. Гараар нээх (manual) энэ хамгаалалтад
# ОРОХГҮЙ — оператор дарвал ямагт явна.
# Хугацаанд суурилсан хамгаалалт БУРУУ байсан: эхний команд УНАСАН тохиолдолд
# дахин оролдохыг ч хаачихдаг (гарах талын дахин нээлтийн гол зорилго нь тэр).
# Тиймээс «ЯГ ОДОО ЯВЖ БАЙГАА эсэх»-ийг л хардаг:
#   • явж байгаа   → алгасна (зэрэгцээ RPC-ээс сэргийлнэ)
#   • амжилттай дууссан → DB-ийн cooldown барина
#   • амжилтгүй дууссан → дахин оролдоно (яг хүссэн зан төлөв)
_open_inflight: set[str] = set()


def open_in_flight(barrier_id: str) -> bool:
    """Тухайн хаалтад «нээх» команд ЯГ ОДОО явж байна уу."""
    return barrier_id in _open_inflight


class _BarrierPriority:
    """Хаалтны командын хугацаанд «хүлээж байна» гэж тэмдэглэнэ + түгжээг
    богино хугацаанд авахыг оролдоно (аваагүй ч цааш явна)."""

    def __init__(self, ip: str):
        self.ip = ip
        self.held = False

    async def __aenter__(self):
        _barrier_waiting[self.ip] = _barrier_waiting.get(self.ip, 0) + 1
        try:
            await asyncio.wait_for(_rpc_lock(self.ip).acquire(),
                                   timeout=settings.barrier_lock_wait_sec)
            self.held = True
        except (asyncio.TimeoutError, TimeoutError):
            # Түгжээг авч чадсангүй — ХҮЛЭЭХГҮЙ. Хаалт нээх нь бүхнээс чухал.
            log.warning("%s: RPC түгжээг %.1fс дотор авч чадсангүй — команд ЯГ ОДОО явуулж байна",
                        self.ip, settings.barrier_lock_wait_sec)
        return self

    async def __aexit__(self, *exc):
        _barrier_waiting[self.ip] = max(0, _barrier_waiting.get(self.ip, 1) - 1)
        if self.held:
            _rpc_lock(self.ip).release()
        return False


# ─── Нэвтрэлтийн таслуур (circuit breaker) ───────────────────────────────────
# Яагаад: Dahua төхөөрөмж дараалсан буруу нэвтрэлтийн дараа бүртгэлийг ТҮГЖДЭГ
# (remainLoginTimes → 0). Дэлгэцэнд бичих оролдлого машин бүрд давтагддаг тул
# нууц үг буруу үед хэдхэн машины дараа камер түгжигдэж, ТЭР ҮЕД ХААЛТНЫ команд
# ч уначихдаг байв (2026-07-28-нд production дээр 23-30 секундын саатал болж
# бүртгэгдсэн). Мөн нэг камерыг ХОЁР систем зэрэг ашиглаж байвал нэг нь нөгөөгийн
# бүртгэлийг түгжих эрсдэлтэй.
#
# Шийдэл: нэвтрэлт нь ЭРХИЙН алдаагаар унавал тухайн IP-г түр хугацаанд
# «блоклож» дахин оролдохоо болино. Хаалт (аюулгүй байдлын шаардлагатай) энэ
# таслуурт ОРОХГҮЙ — зөвхөн дэлгэц (гоо сайхны) хязгаарлагдана.
_auth_fail: dict[str, tuple[int, float]] = {}   # ip -> (дараалсан алдаа, хүртэл_блок)


def _is_auth_error(exc: Exception) -> bool:
    """Алдаа нь нууц үг/түгжээний алдаа мөн үү (сүлжээний түр саатал биш)."""
    msg = str(exc)
    return any(k in msg for k in ("User or password not valid", "login амжилтгүй",
                                  "ТҮГЖИГДСЭН", "remainLoginTimes"))


def auth_block_remaining(ip: str) -> float:
    """Блоклогдсон бол үлдсэн секунд, эс бол 0."""
    item = _auth_fail.get(ip)
    if not item:
        return 0.0
    return max(0.0, item[1] - time.monotonic())


def _auth_failed(ip: str):
    fails = _auth_fail.get(ip, (0, 0.0))[0] + 1
    until = (time.monotonic() + settings.camera_auth_retry_sec
             if fails >= settings.camera_auth_fail_limit else 0.0)
    _auth_fail[ip] = (fails, until)
    if until:
        log.error("%s: нэвтрэлт %d удаа дараалан уналаа — %d секунд ЗОГСООВ "
                  "(камерын бүртгэл түгжигдэхээс сэргийлж). Нууц үгийг Тохиргоо → "
                  "Төхөөрөмж дээр зөв болгоно уу.", ip, fails, settings.camera_auth_retry_sec)


def _auth_ok(ip: str):
    if ip in _auth_fail:
        log.info("%s: нэвтрэлт сэргэлээ — таслуур цэвэрлэв", ip)
        _auth_fail.pop(ip, None)


# ─── LED дэлгэц / дуут зарлал ────────────────────────────────────────────────

async def display_on_screen(ip: str, text: str, voice_text: str | None = None,
                            repeat: int | None = None,
                            creds: tuple[str, str] | None = None) -> str:
    """Камерын LED дэлгэцэнд текст харуулна (шаардлагатай бол дуут зарлал).

    Камер Vehicle Passing горимдоо манай текстийг хурдан дарж бичдэг тул текстийг
    screen_repeat удаа (screen_repeat_interval зайтай) ДАВТАЖ илгээснээр нийт
    ~5-6 секунд тогтвортой харагдуулна. Дуут зарлал зөвхөн эхний удаад.
    Амжилттай бол хоосон мөр, алдаатай бол алдааны тайлбар буцаана."""
    if settings.barrier_mock:
        log.info(f"[screen] MOCK {ip}: {text}")
        return ""
    blocked = auth_block_remaining(ip)
    if blocked:
        # Нууц үг буруу байхад машин бүрд дахин оролдвол камер ТҮГЖИГДЭЖ,
        # хаалт ч нээгдэхгүй болно. Дэлгэц бол заавал биш тул алгасна.
        log.debug("[screen] %s: нэвтрэлтийн таслуур идэвхтэй (%.0fс үлдлээ) — алгасав",
                  ip, blocked)
        return "нэвтрэлтийн таслуур идэвхтэй"
    username, password = creds or barrier_credentials(None)
    # Хугацааны талбар идэвхтэй бол камер өөрөө текстээ барих тул ДАВТАХГҮЙ —
    # нэг команд = хамгийн богино RPC сесс = хаалттай мөргөлдөхгүй
    if settings.screen_hold_sec > 0 and settings.screen_hold_field:
        times = 1
    else:
        times = max(1, repeat if repeat is not None else settings.screen_repeat)
    shown = 0
    try:
        # Нэг камерт нэг RPC — хаалтны командтай мөргөлдвөл камер «нууц үг буруу»
        # гэж ХУДАЛ татгалздаг. Дэлгэц бол заавал биш тул хаалт ирвэл бууж өгнө.
        async with _rpc_lock(ip):
            async with httpx.AsyncClient(timeout=settings.barrier_timeout_sec) as client:
                rpc = DahuaRpc(client, ip, username, password)
                await rpc.login()
                try:
                    for i in range(times):
                        if i:
                            await asyncio.sleep(settings.screen_repeat_interval)
                            # Хаалтны команд хүлээж байвал сессээ ЯГ ОДОО чөлөөлнө —
                            # машин хаалганы өмнө зогсохоос дэлгэцийн давталт чухал биш
                            if barrier_is_waiting(ip):
                                log.info("[screen] %s: хаалт хүлээж байна — давталтыг "
                                         "%d/%d дээр тасаллаа", ip, i, times)
                                break
                        await rpc.set_screen(text)
                        shown = i + 1
                        if i == 0 and voice_text:
                            await rpc.set_voice(voice_text)
                finally:
                    await rpc.logout()
        # Амжилтыг ч логлоно — LED-ийг нүдээр харахгүйгээр алсаас
        # (journalctl | grep screen) ажилласныг батлахад хэрэгтэй
        log.info(f"[screen] {ip}: OK ×{shown} «{text}»")
        _auth_ok(ip)
        return ""
    except Exception as e:  # дэлгэцний алдаа хаалт нээх урсгалыг хэзээ ч зогсоохгүй
        err = f"{type(e).__name__}: {str(e)[:200]}"
        if _is_auth_error(e):
            _auth_failed(ip)   # дараалсан эрхийн алдаа → түр зогсооно
        log.warning(f"[screen] {ip}: бичиж чадсангүй ({err})")
        return err


def schedule_display(ip: str | None, text: str, voice_text: str | None = None,
                     creds: tuple[str, str] | None = None):
    """Event боловсруулалтын дараа дуудна — дэлгэцний командыг АРД НЬ явуулна
    (хаалт нээх/WS broadcast-ыг хэзээ ч хүлээлгэхгүй, snapshot-той ижил хэв маяг)."""
    if not settings.screen_enabled or not ip or not text:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # event loop-гүй орчин (тест г.м) — алгасна
    asyncio.create_task(display_on_screen(ip, text, voice_text, creds=creds))


def render_screen_text(template: str, amount: float | int | None = None,
                       plate: str = "") -> str:
    """Template-ийн {amount}/{plate}-ийг орлуулна. Дүн бүхэл тоогоор.
    Мөр таслал: .env-д «|» эсвэл literal «\\n» бичвэл LED-ийн жинхэнэ мөр таслал (\\n)
    болгоно — дугаар/төлбөрийг 2 тусдаа мөрөнд харуулах боломжтой."""
    amt = "" if amount is None else f"{int(round(float(amount)))}"
    text = template.replace("{amount}", amt).replace("{plate}", plate or "")
    text = text.replace("\\n", "\n").replace("|", "\n")  # .env мөр таслалыг хөрвүүлнэ
    return "\n".join(line.strip() for line in text.split("\n")).strip("\n")
