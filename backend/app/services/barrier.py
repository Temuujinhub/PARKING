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
from datetime import datetime, timedelta

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


# ─── Камер тус бүрийн ХУВААЛЦСАН HTTP холболт ────────────────────────────────
# Dahua ITC камерын веб сервер нэгэн зэрэг ЦӨӨН холболт л зөвшөөрдөг. Манай систем
# урьд нь үйлдэл бүрд (хаалт нээх, дэлгэц бичих, зураг татах) ШИНЭ AsyncClient
# үүсгэдэг байсан тул шинэ TCP холболт нээгдэж, камерын сан дүүрч ХАРИУ ӨГӨХӨӨ
# БОЛИДОГ байв. Production дээр curl-ээр яг энэ хэв маяг бүртгэгдсэн (2026-07-28,
# MONNIS): эхний 4-9 хүсэлт timeout, дараа нь бүгд 11 МИЛЛИСЕКУНД. Ping 0%
# алдагдалтай байсан тул сүлжээ биш — холболтын сангийн асуудал.
#
# Шийдэл: камерын IP тус бүрд НЭГ клиент, keep-alive-аар холболтоо дахин ашиглана.
_http_clients: dict[str, httpx.AsyncClient] = {}


def camera_client(ip: str) -> httpx.AsyncClient:
    """Тухайн камерын хуваалцсан HTTP клиент (холболт дахин ашиглана)."""
    c = _http_clients.get(ip)
    if c is None or c.is_closed:
        c = _http_clients[ip] = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.barrier_attempt_timeout_sec),
            limits=httpx.Limits(
                max_connections=settings.camera_max_connections,
                max_keepalive_connections=settings.camera_max_connections,
                keepalive_expiry=60.0),
        )
    return c


async def close_camera_clients():
    """Зогсох үед бүх холболтыг цэвэр хаана (камерт орхигдсон сокет үлдээхгүй)."""
    for ip, c in list(_http_clients.items()):
        try:
            await c.aclose()
        except Exception:  # noqa: BLE001
            pass
        _http_clients.pop(ip, None)


# Камер «өвчтэй» цонх: хаалтны команд бүх оролдлогодоо timeout болбол камер
# RPC-д хариу өгөхгүй байна гэсэн үг. Энэ үед ЗААВАЛ БИШ RPC-үүд (LED дэлгэц,
# сессийн хяналт) түр зогсох ёстой — эс бол тэд хаалтны дараагийн оролдлоготой
# өрсөлдөж камерын ховор нөөцийг улам шавхна (Monnis 2026-07-28: LED 0/59
# амжилттай байх зуур хаалт 38 удаа timeout).
_cam_sick_until: dict[str, float] = {}


def camera_sick_remaining(ip: str) -> float:
    """Камер саяхан timeout-оор унасан бол үлдсэн «амрах» секунд, эс бол 0."""
    return max(0.0, _cam_sick_until.get(ip, 0.0) - time.monotonic())


def _mark_camera_sick(ip: str, sec: float = 60.0):
    _cam_sick_until[ip] = time.monotonic() + sec


async def reset_camera_client(ip: str):
    """Нэг камерын холболтын санг шинэчилнэ (хаагаад дараагийн хүсэлтэд шинээр
    нээнэ). Хаалтны команд БҮХ оролдлогодоо timeout болсны дараа дуудна: хуучирсан
    keep-alive холболт камер талдаа үхсэн байх нь ийм timeout-ын түгээмэл шалтгаан
    бөгөөд шинэ TCP холболтоор дараагийн команд сэргэдэг. Камерыг reboot хийхээс
    хамаагүй аюулгүй — камерын ажиллагаа, бусад системд огт нөлөөгүй."""
    c = _http_clients.pop(ip, None)
    if c is not None:
        try:
            await c.aclose()
        except Exception:  # noqa: BLE001
            pass
        log.info("%s: холболтын санг шинэчлэв (дараагийн команд шинэ TCP-ээр)", ip)


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

        ОЛОН МӨР (Monnis 2026-07-28 туршилтаар батлагдсан): энэ firmware Custom
        доторх ЯМАР Ч мөр таслалыг (\\n, \\r\\n, |, ;) үл ойшоож бүгдийг 1-р мөрөнд
        урсгадаг. Харин Custom-ыг ЖАГСААЛТААР өгвөл элемент тус бүр дэлгэцийн
        LogicScreens-ийн 1/2/3-р мөрөнд ТУСДАА гарна. Тиймээс олон мөртэй текстийг
        жагсаалтаар илгээж, татгалзвал (хуучин firmware) мөрүүдийг
        screen_line_break-ээр нийлүүлж нэг мөрөөр дахин илгээнэ."""
        text = text.replace("\\n", "\n").replace("|", "\n")
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        # Камер «хэдэн секунд харуулах» талбар дэмждэг бол НЭГ команд хангалттай —
        # давталт хэрэггүй болж RPC сесс богиносно (хаалттай мөргөлдөх нь буурна).
        # Ямар талбар дэмжигдэхийг tools/screen_probe.py-ээр камераас нь асууж
        # тогтооно; олдвол .env-д PARKING_SCREEN_HOLD_FIELD/SEC-ээр асаана.
        extra = ({settings.screen_hold_field: settings.screen_hold_sec}
                 if settings.screen_hold_sec > 0 and settings.screen_hold_field else {})
        if len(lines) > 1:
            res = await self._call("trafficParking.setScreenDisplay",
                                   {"Custom": lines, **extra})
            if res.get("result"):
                return res
            # Жагсаалт дэмжихгүй firmware — нэг мөрийн хуучин арга руу буцна
        br = settings.screen_line_break.replace("\\n", "\n").replace("\\r", "\r")
        res = await self._call("trafficParking.setScreenDisplay",
                               {"Custom": br.join(lines), **extra})
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
    # УСТГАСАН камерыг ХЭЗЭЭ Ч сонгохгүй: устгал нь зөөлөн (status='deleted') тул
    # шүүхгүй бол админ UI-аас устгасан хуучин бүртгэл хаалтыг чимээгүй барьсаар
    # байдаг (2026-08-07 Туушин: устгасан 10.0.111.10 хэвээр сонгогдож байв).
    # ДОТООД (давхар зогсоолын) хаалт нь ГАДНАХААС бие даасан: команд нь заавал
    # ижил төрлийн камерын реле рүү очно. Эс бол эгнээний дугаар давхцахад
    # дотоод гарах командыг ГАДНА гарах камер гүйцэтгэж, машин төлбөр төлөлгүй
    # зогсоолоос шууд гарах эрсдэлтэй.
    cams = db.query(Device).filter(
        Device.site_id == device.site_id, Device.device_type == "camera",
        Device.status != "deleted",
        Device.nested_inner.is_(bool(device.nested_inner)),
        Device.ip_address.isnot(None), Device.ip_address != "",
    ).order_by(Device.created_at, Device.id).all()   # тодорхой эрэмбэ — доор үзнэ үү
    # 1) Ижил эгнээний (lane_no) камер — хамгийн зөв. Нэг эгнээнд олон камер
    # бүртгэлтэй байвал ЧИГЛЭЛ (lane_dir) таарсныг нь эхэлж авна: өмнө нь эрэмбэгүй
    # `same_lane[0]` байсан тул дуудалт бүрд ӨӨР камер сонгогдож, нэг удаа
    # ажиллаад дараагийнд нь унадаг «санамсаргүй» хэв маяг үүсгэж байв.
    same_lane = [c for c in cams if c.lane_no == device.lane_no]
    if same_lane:
        best = next((c for c in same_lane if c.lane_dir == device.lane_dir), same_lane[0])
        if len(same_lane) > 1:
            log.warning("%s (%s, эгнээ %s/%s): ижил эгнээнд %d камер бүртгэлтэй — «%s» (%s)-г "
                        "сонголоо. Тохиргоо → Төхөөрөмж дээр эгнээ/чиглэлийг ялгана уу: %s",
                        device.name, device.id, device.lane_no, device.lane_dir, len(same_lane),
                        best.name, best.ip_address,
                        ", ".join(f"{c.name}({c.ip_address},{c.lane_dir})" for c in same_lane))
        return best.ip_address, best
    # 2) Зогсоолд ЯГ НЭГ камертай (нэг all-in-one төхөөрөмж орох/гарах хоёуланд) бол түүнийг
    if len(cams) == 1:
        return cams[0].ip_address, cams[0]
    # 3) Олон камертай ч энэ эгнээнийх алга — буруу хаалт нээхээс сэргийлж унана
    return None, None


def _resolve_ip(db: Session, device: Device) -> str | None:
    """Зөвхөн IP хэрэгтэй дуудагчдад — буцаах утга нь өмнөх хэвээр."""
    return _resolve_device(db, device)[0]


async def _execute(db: Session, device: Device, command: str, session_id: str | None,
                   source: str, issued_by: str | None = None, plate: str = "",
                   screen_text: str = "") -> BarrierCommand:
    cmd = BarrierCommand(
        session_id=session_id, device_id=device.id, command=command,
        command_source=source, issued_by=issued_by,
    )
    _inflight = command in ("open", "force_open")
    if _inflight:
        _open_inflight[device.id] = time.monotonic()   # давхар зэрэгцээ командаас сэргийлнэ
    # ЧУХАЛ: доорх бүх зам try/finally дотор — өмнө нь цэвэрлэгээ шугаман кодоор
    # явдаг байсан тул дуудагчийн HTTP хүсэлт (POS/QPay/frontend/камерын push)
    # таслагдахад CancelledError цэвэрлэгээг алгасаж, _open_inflight-д тэмдэглэгээ
    # МӨНХӨД үлддэг байв. Түүнээс хойш ensure_entry_barrier/ensure_exit_barrier_
    # if_cleared «аль хэдийн нээж байна» гэж худал үзээд команд огт илгээхгүй —
    # бүртгэлтэй машин ч нээгдэхгүй, restart хийтэл гацна (MONNIS 2026-07-28).
    _detached = False   # True: цэвэрлэгээг background done-callback хариуцна
    try:
        db.add(cmd)
        db.flush()

        ip, target = _resolve_device(db, device)

        if settings.barrier_mock:
            cmd.status = "SUCCESS"
            cmd.response_text = f"MOCK: barrier {command}"
            cmd.executed_at = datetime.utcnow()
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
        # ХААЛТ өөрөө нээгдтэл хэдэн мс болсныг ТУСАД нь хэмжинэ. Нийт duration_ms
        # нь дэлгэц бичихийг Ч агуулдаг бөгөөд дэлгэц нь хаалт нээгдсэний ДАРАА
        # ижил сессээр явдаг. Тиймээс «хаалт 1982мс» гэсэн тоо жолооч 2 секунд
        # хүлээсэн мэт харагдуулж байсан ч бодит байдалд хаалт аль хэдийн
        # нээгдчихсэн, үлдсэн хугацаа нь LED текст байв (2026-08-08 STO/NOMADS/
        # TUUSHIN/KH дээр бүгд 1.8-2.0с). Хоёрыг нь салгаж бичихгүй бол «хаалт
        # удаан» гэсэн худал дохио өгсөөр байна.
        _gate_ms: int | None = None
        # НИЙТ хугацааны таслалт. ЧУХАЛ: httpx-ийн timeout нь ХҮСЭЛТ БҮРД үйлчилдэг,
        # харин нэг хаалт нээхэд 5 хүсэлт явдаг (login×2 + factory.instance + strobe +
        # logout). Тиймээс timeout=12 гэдэг нь нэг оролдлого 60 секунд хүртэл үргэлжилж
        # болно гэсэн үг байв — 3 оролдлоготой бол 180с. Production дээр 51275мс
        # (51 секунд) болж бүртгэгдсэн. Одоо оролдлого БҮРИЙГ болон НИЙТ хугацааг
        # asyncio.wait_for-оор хатуу таслана.
        _deadline = time.monotonic() + settings.barrier_total_budget_sec

        async def _attempts():
            nonlocal last_err
            # Нэг камерт нэг RPC — дэлгэцтэй мөргөлдөхөөс сэргийлнэ (хаалт тэргүүлэх эрхтэй)
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
                  _timeout = min(_remaining, settings.barrier_attempt_timeout_sec
                                 + (settings.barrier_screen_timeout_sec if screen_text else 0))
                  async def _one_attempt():
                      """Нэг оролдлого — бүхэлдээ _timeout дотор багтах ёстой.

                      Хуваалцсан клиент: шинэ TCP холболт нээхгүй, камерын холболтын
                      санг шавхахгүй (дээрх camera_client-ийн тайлбарыг үзнэ үү)."""
                      nonlocal _gate_ms
                      if True:
                          client = camera_client(ip)
                          if command == "open" and settings.barrier_open_path:
                              # Өөр загварын (CGI дэмждэг) төхөөрөмжид зориулсан гар тохиргоо
                              auth = httpx.DigestAuth(username, password)
                              resp = await client.get(f"http://{ip}{settings.barrier_open_path}", auth=auth)
                              body = (resp.text or "").strip()
                              if resp.status_code == 200 and "error" not in body.lower():
                                  cmd.status = "SUCCESS"
                                  _gate_ms = int((time.monotonic() - _t0) * 1000)
                              cmd.response_text = f"CGI {resp.status_code}: {body[:200]}"
                          else:
                              rpc = DahuaRpc(client, ip, username, password)
                              await rpc.login()
                              try:
                                  res = await rpc.strobe(RPC_METHODS[command],
                                                         settings.barrier_channel, plate)
                                  cmd.status = "SUCCESS"
                                  # ЯГ ЭНД хаалт нээгдлээ — жолоочийн хүлээлт дуусах цэг
                                  _gate_ms = int((time.monotonic() - _t0) * 1000)
                                  cmd.response_text = (f"RPC2 {RPC_METHODS[command]} → {res.get('result')}"
                                                       + (f" ({attempt + 1}-р оролдлого)" if attempt else ""))
                                  # ── Дэлгэцийг ЯГ ЭНЭ СЕССЭЭР бичнэ ──
                                  # Тусад нь бичвэл хоёр дахь login камерын сессийн
                                  # слот чөлөөлөгдөхөөс өмнө очиж «нууц үг буруу»
                                  # гэсэн ХУДАЛ хариу авдаг байв (remainLoginTimes
                                  # буурч түгжээ рүү ойртоно). Нэг сесс = нэг
                                  # нэвтрэлт = давхцалгүй. Хаалт аль хэдийн
                                  # нээгдсэн тул энэ хэсэг унасан ч командын үр
                                  # дүнд НӨЛӨӨГҮЙ (богино timeout-той).
                                  if screen_text and settings.screen_enabled:
                                      try:
                                          await asyncio.wait_for(
                                              rpc.set_screen(screen_text),
                                              timeout=settings.barrier_screen_timeout_sec)
                                          _screen_record(ip, True)
                                          _note_screen_shown(ip, screen_text)
                                          log.info("[screen] %s: хаалттай ХАМТ бичив «%s»",
                                                   ip, screen_text.replace("\n", "|"))
                                      except Exception as _se:  # noqa: BLE001
                                          _screen_record(ip, False)
                                          log.info("[screen] %s: хаалттай хамт бичиж чадсангүй (%s)",
                                                   ip, type(_se).__name__)
                              finally:
                                  await rpc.logout()

                  try:
                      # wait_for нь ОРОЛДЛОГЫГ БҮХЭЛД нь таслана (нэг хүсэлт биш) —
                      # login+strobe+logout цуврал хүсэлт нийлээд хугацаа хэтрүүлэхээс сэргийлнэ
                      await asyncio.wait_for(_one_attempt(), timeout=_timeout)
                      if cmd.status == "SUCCESS":
                          break
                  except (asyncio.TimeoutError, TimeoutError):
                      if cmd.status == "SUCCESS":
                          break   # хаалт нээгдчихсэн (дэлгэц удаассан) — ДАХИН нээхгүй
                      last_err = f"хугацаа хэтэрлээ ({_timeout:.1f}с)"
                      cmd.response_text = (f"{last_err} — {attempt + 1}/{attempts} оролдлого"
                                           if attempt + 1 < attempts
                                           else f"{last_err} ({attempts} удаа оролдсон)")
                  except Exception as e:
                      if cmd.status == "SUCCESS":
                          break   # хаалт нээгдчихсэн — дараагийн алдаа түүнийг үгүйсгэхгүй
                      last_err = f"{type(e).__name__}: {str(e)[:300]}"
                      # ЭРХИЙН алдаанд ДАХИН ОРОЛДОХГҮЙ. Буруу нэвтрэлт дахин
                      # илгээх нь хэзээ ч амжилтад хүргэхгүй, зөвхөн камерын
                      # remainLoginTimes-ыг бууруулж ТҮГЖЭЭГ УРТАСГАНА — тэр
                      # хугацаанд хаалт огт нээгдэхгүй. 2026-08-07 production:
                      # Туушины камер түгжигдсэн байхад гараар дарах болгонд
                      # 4 оролдлого явж, түгжээ 17:38:09 → 17:38:18 болж сунгав.
                      # (Сүлжээний timeout-д дахин оролдох нь хэвээр — тэнд
                      # дахин оролдлого үнэхээр тусалдаг.)
                      if _is_auth_error(e):
                          cmd.response_text = f"{last_err} (эрхийн алдаа — дахин оролдсонгүй)"
                          break
                      cmd.response_text = (f"{last_err} — {attempt + 1}/{attempts} оролдлого"
                                           if attempt + 1 < attempts
                                           else f"{last_err} ({attempts} удаа оролдсон)")

        _task = asyncio.ensure_future(_attempts())
        try:
            # shield: дуудагчийн хүсэлт таслагдсан ч (камер push-аа хаях, POS/QPay
            # bridge timeout, оператор таб хаах) хаалт нээх RPC-г ХАМТ ТАСЛАХГҮЙ —
            # машин хаалганы өмнө зогсож байгаа тул нээлт заавал дуустал явна.
            await asyncio.shield(_task)
        except asyncio.CancelledError:
            _detached = True

            def _bg_done(t: asyncio.Task):
                _open_inflight.pop(device.id, None)
                _exc = t.exception()
                log.warning("хаалт %s: дуудагчийн хүсэлт таслагдсан ч команд ард нь "
                            "дууслаа [%s] status=%s%s", command, ip, cmd.status,
                            f" ({_exc!r})" if _exc else "")
            _task.add_done_callback(_bg_done)
            raise

        cmd.executed_at = datetime.utcnow()
        # Хугацааны хэмжилт — «хаалт удаан нээгдэж байна» гомдлыг тоогоор нотлох
        _ms = int((time.monotonic() - _t0) * 1000)
        cmd.duration_ms = _ms
        # АЛЬ камер/зогсоол удаашралтай байгааг заавал хэлнэ — эс бол олон зогсоолтой
        # орчинд «дундаж 1102мс» гэсэн тоо аль талбайн асуудал болохыг заахгүй.
        _site = ""
        try:
            _st = getattr(target or device, "site", None)
            _site = f" {_st.site_code}" if _st is not None else ""
        except Exception:  # noqa: BLE001
            _site = ""
        _where = f"{ip}{_site}"
        # Задаргаа: «хаалт Xмс + LED Yмс». Удаашралын анхааруулгыг ХААЛТНЫ хугацаанд
        # тулгуурлана — LED нь хаалт нээгдсэний дараа явдаг тул жолоочийг хүлээлгэдэггүй.
        _brk = ""
        if _gate_ms is not None and _ms - _gate_ms >= 100:
            _brk = f" (хаалт {_gate_ms}мс + LED {_ms - _gate_ms}мс)"
        _driver_ms = _gate_ms if _gate_ms is not None else _ms
        if _driver_ms >= settings.barrier_slow_warn_ms or cmd.status != "SUCCESS":
            log.warning("хаалт %s: %s — %dмс%s [%s] (%s, дээд %d оролдлого) %s",
                        command, cmd.status, _ms, _brk, _where, source, attempts,
                        (cmd.response_text or "")[:120])
        else:
            log.info("хаалт %s: SUCCESS — %dмс%s [%s] (%s)", command, _ms, _brk, _where, source)
        if cmd.status != "SUCCESS" and "хугацаа хэтэрлээ" in (cmd.response_text or ""):
            # Бүх оролдлого timeout — холболтын сан хуучирсан байж болзошгүй тул
            # шинэчилнэ; дараагийн команд (давхар уншилтын retry) шинэ TCP-ээр явна.
            # Мөн заавал биш RPC-үүдийг (LED г.м.) 60с амраана — хаалтны дараагийн
            # оролдлогод камерын бүх нөөцийг үлдээнэ.
            await reset_camera_client(ip)
            _mark_camera_sick(ip)
        if cmd.status != "SUCCESS":
            # Хуучин easy-park агентын телеметртэй автоматаар холбоно: манай команд
            # унах мөчид хуучин систем мөн алдаа/төлөв мэдээлж байсан бол хоёр систем
            # нэг камерын нөөц булаацалдсаны баримт шууд лог дээр бүрддэг
            # (дэлгэрэнгүй: аудит LEGACY_GATE_STATUS).
            try:
                from ..models import AuditLog
                _legacy = (db.query(AuditLog)
                           .filter(AuditLog.username == "legacy-agent",
                                   AuditLog.created_at >= datetime.utcnow() - timedelta(minutes=3))
                           .count())
                if _legacy:
                    log.warning("хаалт FAILED [%s] мөчид хуучин систем сүүлийн 3 минутад "
                                "%d алдаа/төлөв мэдээлжээ — камерын нөөц булаацалдааны шинж",
                                _where, _legacy)
            except Exception:  # noqa: BLE001 — холбоо тогтоох нь заавал биш
                pass
        db.commit()
        return cmd
    finally:
        # Ямар ч замаар гарсан (амжилт, алдаа, таслалт) in-flight тэмдэглэгээ
        # заавал цэвэрлэгдэнэ. Таслагдсан үед background таск өөрөө цэвэрлэнэ.
        if _inflight and not _detached:
            _open_inflight.pop(device.id, None)


async def open_barrier(db: Session, device: Device, session_id: str | None, source: str,
                       issued_by: str | None = None, plate: str = "",
                       force: bool = False, screen_text: str = "") -> BarrierCommand:
    """Хаалт нээх. force=True үед forceBreaking (албадан онгойлгоод барих).

    screen_text өгвөл дэлгэцийг ЯГ ТЭР сессээр бичнэ — тусад нь нэвтрэхгүй
    (камерын сессийн давхцлаас сэргийлнэ, нэвтрэлт хоёр дахин цөөрнө)."""
    return await _execute(db, device, "force_open" if force else "open",
                          session_id, source, issued_by, plate, screen_text)


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

# Камер сесс чөлөөлөхөд ХОЦРОГДОЛТОЙ: logout буцсаны дараа ч сессийн слот шууд
# сулардаггүй. Тиймээс хаалт нээгээд ШУУД дэлгэц бичихэд камер «хязгаар давлаа»
# гэж үзэж «User or password not valid» гэсэн ХУДАЛ хариу өгдөг — бүр аюултай нь
# энэ нь remainLoginTimes-ыг БУУРУУЛЖ түгжээ рүү ойртуулдаг (2026-07-29 Monnis:
# LED-ийн 51 алдаа бүгд ийм, remainLoginTimes 4→3). Тиймээс ЗААВАЛ БИШ RPC-үүд
# (дэлгэц, сессийн хяналт) сүүлийн RPC-ээс хойш завсар үлдээж хүлээнэ.
_last_rpc_done: dict[str, float] = {}


def note_rpc_done(ip: str):
    _last_rpc_done[ip] = time.monotonic()


async def wait_rpc_gap(ip: str):
    """Заавал биш RPC-ийн өмнө: сүүлийн RPC-ээс хойш завсар үлдээнэ."""
    gap = settings.camera_rpc_gap_sec
    if gap <= 0:
        return
    left = gap - (time.monotonic() - _last_rpc_done.get(ip, 0.0))
    if left > 0:
        await asyncio.sleep(min(left, gap))


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
# Утга нь ЭХЭЛСЭН цаг (monotonic): тэмдэглэгээ ямар нэг шалтгаанаар цэвэрлэгдэлгүй
# үлдвэл (production 2026-07-28, MONNIS: картын төлбөрийн хүсэлт таслагдаад
# CancelledError цэвэрлэгээг алгасаж) НЭГ удаагийн леак хаалтыг restart хүртэл
# «нээж байна» гэж худал мэдээлж, бүртгэлтэй машиныг ч нээхгүй болгодог байв.
# Тиймээс хугацаа нь илт хэтэрсэн тэмдэглэгээг өөрөө хүчингүй болгоно.
_open_inflight: dict[str, float] = {}


def open_in_flight(barrier_id: str) -> bool:
    """Тухайн хаалтад «нээх» команд ЯГ ОДОО явж байна уу."""
    t0 = _open_inflight.get(barrier_id)
    if t0 is None:
        return False
    if time.monotonic() - t0 > settings.barrier_total_budget_sec + 10:
        _open_inflight.pop(barrier_id, None)   # гацсан тэмдэглэгээ — өөрөө сэргэнэ
        log.warning("хаалт %s: гацсан in-flight тэмдэглэгээг цэвэрлэв", barrier_id)
        return False
    return True


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
        note_rpc_done(self.ip)   # дэлгэц энэ агшнаас хойш завсар хүлээнэ
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

# LED-ийн амжилт/алдааны түүх (эрүүл мэндийн хуудсанд) — камерт хандахгүйгээр
# «дэлгэц ажиллаж байна уу»-г тоогоор харуулна. Restart-аас хойшхи санах ой.
from collections import deque as _deque  # noqa: E402

_screen_stats: dict[str, "_deque"] = {}


def _screen_record(ip: str, ok: bool):
    dq = _screen_stats.get(ip)
    if dq is None:
        dq = _screen_stats[ip] = _deque(maxlen=500)
    dq.append((datetime.utcnow(), ok))


def screen_stats(ip: str, since: datetime) -> tuple[int, int]:
    """(амжилттай, алдаатай) — since-ээс хойшхи бодит оролдлогууд (алгассан тооцохгүй)."""
    ok = fail = 0
    for ts, s in _screen_stats.get(ip) or ():
        if ts >= since:
            ok, fail = ok + (1 if s else 0), fail + (0 if s else 1)
    return ok, fail


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
    sick = camera_sick_remaining(ip)
    if sick:
        # Камер саяхан хаалтны командад ч хариу өгөөгүй — дэлгэцийн (заавал биш)
        # оролдлого зөвхөн нөөц шавхана. Түр алгасаж хаалтанд зам тавьж өгнө.
        log.info("[screen] %s: камер сэргэж амжаагүй (%.0fс үлдлээ) — алгасав", ip, sick)
        return "камер сэргэж амжаагүй"
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
        # Сүүлийн RPC-ээс хойш завсар хүлээнэ — камер сессээ чөлөөлж амжаагүй
        # байхад нэвтэрвэл ХУДАЛ «нууц үг буруу» авч remainLoginTimes буурна
        await wait_rpc_gap(ip)
        # Нэг камерт нэг RPC — хаалтны командтай мөргөлдвөл камер «нууц үг буруу»
        # гэж ХУДАЛ татгалздаг. Дэлгэц бол заавал биш тул хаалт ирвэл бууж өгнө.
        async with _rpc_lock(ip):
            if barrier_is_waiting(ip):
                return "хаалт хүлээж байна"   # хаалт тэргүүлэх эрхтэй — дараа оролдоно
            if True:
                client = camera_client(ip)   # хуваалцсан холболт
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
        note_rpc_done(ip)
        _note_screen_shown(ip, text)
        _screen_record(ip, True)
        return ""
    except Exception as e:  # дэлгэцний алдаа хаалт нээх урсгалыг хэзээ ч зогсоохгүй
        err = f"{type(e).__name__}: {str(e)[:200]}"
        note_rpc_done(ip)
        if _is_auth_error(e):
            # ЧУХАЛ: энэ нь камерын remainLoginTimes-ыг БУУРУУЛДАГ — түгжээнд
            # хүрвэл ХААЛТ ч нээгдэхгүй болно. Тиймээс дэлгэцийг нэг л алдаанд
            # түр зогсооно (нөхцөл сайжирвал дараагийн машин дээр сэргэнэ).
            _auth_failed(ip)
            _mark_camera_sick(ip, settings.camera_auth_retry_sec)
        log.warning(f"[screen] {ip}: бичиж чадсангүй ({err})")
        _screen_record(ip, False)
        return err


# Саяхан ХАРУУЛСАН текст — хаалттай хамт бичигдсэн бол дараагийн schedule_display
# давхардуулж камерт дахин хүрэхгүй (нэг машинд нэг л дэлгэцийн бичилт).
_shown_recent: dict[str, tuple[str, float]] = {}


def _note_screen_shown(ip: str, text: str):
    _shown_recent[ip] = (text, time.monotonic())


def _screen_recently_shown(ip: str, text: str) -> bool:
    item = _shown_recent.get(ip)
    return bool(item and item[0] == text
                and time.monotonic() - item[1] < settings.screen_dedup_sec)


# Тухайн камерт хамгийн сүүлд хүсэгдсэн дэлгэцийн «үе» — хожуу оролдлого хийж
# байх зуур ШИНЭ текст ирвэл хуучныг орхиж, камерыг дэмий цохихгүй байхад.
_display_gen: dict[str, int] = {}


def _retry_delays() -> list[float]:
    out = []
    for part in (settings.screen_retry_delays or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(float(part))
            except ValueError:
                pass
    return out


def _is_transient_screen_error(err: str) -> bool:
    """Дэлгэцийн алдаа ТҮР ЗУУРЫН уу (дахин оролдох утгатай юу)?

    Түр зуурын: нэвтрэлт/сесс дүүрсэн, timeout, холболт тасарсан — суваг
    чөлөөлөгдвөл бүтнэ. ТОГТМОЛ: камер командыг өөрийг нь татгалзаж байна
    (жишээ нь дэлгэц «Managed Mode» горимд биш) — дахин оролдох нь зөвхөн
    камерыг дэмий цохино, тиймээс НЭГ л удаа оролдоод болино."""
    e = (err or "").lower()
    if "setscreendisplay амжилтгүй" in e or "method not found" in e:
        return False
    return True


async def _display_with_retry(ip: str, text: str, voice_text: str | None,
                              creds: tuple[str, str] | None, gen: int):
    """Дэлгэцийг харуулах — амжилтгүй бол ХОЖУУ дахин оролдоно.

    Яагаад: гарах үед жолооч төлбөрөө төлж хэдэн арван секунд зогсдог тул
    дүнг 10-30 секундын дараа харуулах нь ХЭРЭГТЭЙ хэвээр. Гэхдээ камерыг
    дэмий цохихгүй: хаалтны команд хүлээж байвал эсвэл камер саяхан timeout
    болсон бол тэр оролдлогыг алгасаад дараагийн завсарт үлдээнэ."""
    for i, delay in enumerate([0.0] + _retry_delays()):
        if delay:
            await asyncio.sleep(delay)
        if _display_gen.get(ip) != gen:
            return   # шинэ текст ирсэн — хуучин нь хоцрогдсон
        if barrier_is_waiting(ip) or camera_sick_remaining(ip):
            continue   # суваг завгүй — дараагийн завсарт оролдоно
        err = await display_on_screen(ip, text, voice_text if i == 0 else None, creds=creds)
        if not err:
            if i:
                log.info("[screen] %s: %d-р оролдлогоор амжиллаа", ip, i + 1)
            return
        if not _is_transient_screen_error(err):
            # Камер командыг өөрийг нь татгалзлаа — дахин оролдох нь ашиггүй
            log.info("[screen] %s: тогтмол алдаа — дахин оролдохгүй (%s)", ip, err[:80])
            return


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
    if _screen_recently_shown(ip, text):
        # Хаалттай ХАМТ бичигдсэн — камерт дахин хүрэх шаардлагагүй
        log.debug("[screen] %s: саяхан харуулсан текст — алгасав", ip)
        return
    gen = _display_gen[ip] = _display_gen.get(ip, 0) + 1
    asyncio.create_task(_display_with_retry(ip, text, voice_text, creds, gen))


def format_duration(minutes: float | int | None) -> str:
    """Зогссон хугацааг LED-д багтахаар богино бичнэ: «45м», «2ц 05м»."""
    if minutes is None:
        return ""
    m = max(0, int(round(float(minutes))))
    h, mm = divmod(m, 60)
    return f"{h}ц {mm:02d}м" if h else f"{mm}м"


def render_screen_text(template: str, amount: float | int | None = None,
                       plate: str = "", duration_minutes: float | int | None = None,
                       time_str: str = "", payment: str = "", reason: str = "") -> str:
    """Template-ийн {amount}/{plate}/{duration}/{time}/{payment}/{reason}-ийг
    орлуулна. Дүн бүхэл тоогоор, {duration} нь зогссон хугацаа («2ts 05min»),
    {time} нь локал цаг («14:05», дуудагч бэлдэж өгнө), {payment} нь төлбөрийн
    төрөл («QPay»/«Карт»/«Бэлэн»), {reason} нь үнэгүй гарсан шалтгаан.
    Мөр таслал: .env-д «|» эсвэл literal «\\n» бичвэл LED-ийн жинхэнэ мөр таслал (\\n)
    болгоно — дугаар/хугацаа/төлбөрийг тусдаа мөрүүдэд харуулах боломжтой."""
    amt = "" if amount is None else f"{int(round(float(amount)))}"
    text = (template.replace("{amount}", amt).replace("{plate}", plate or "")
            .replace("{duration}", format_duration(duration_minutes))
            .replace("{time}", time_str or "")
            .replace("{payment}", payment or "").replace("{reason}", reason or ""))
    text = text.replace("\\n", "\n").replace("|", "\n")  # .env мөр таслалыг хөрвүүлнэ
    # Хоосон мөрийг хаяна: {duration} өгөгдөөгүй үед дундаа цоорхой мөр үлдэхгүй
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())
