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
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import BarrierCommand, Device

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
        (Platform)" горимд байх шаардлагатай."""
        res = await self._call("trafficParking.setScreenDisplay", {"Custom": text})
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


def _resolve_ip(db: Session, device: Device) -> str | None:
    """Команд илгээх IP. Бүх-нэг-дор ITC камер хаалтаа өөрийн реле (NO1/NO2)-ээр
    нээдэг тул хаалт төхөөрөмжид IP байхгүй бол тухайн эгнээний камерын IP-г ашиглана.

    ЧУХАЛ: ижил эгнээний камер олдоогүй үед БУСАД эгнээний камер руу шилжихийг
    хориглоно (өмнө нь тэгдэг байсан) — эс бол орох хаалтын команд гарах камер руу
    очиж БУРУУ хаалт нээнэ. Олон камертай зогсоолд ижил эгнээнийх заавал байх ёстой."""
    if device.ip_address:
        return device.ip_address
    cams = db.query(Device).filter(
        Device.site_id == device.site_id, Device.device_type == "camera",
        Device.ip_address.isnot(None), Device.ip_address != "",
    ).all()
    # 1) Ижил эгнээний (lane_no) камер — хамгийн зөв
    same_lane = [c for c in cams if c.lane_no == device.lane_no]
    if same_lane:
        return same_lane[0].ip_address
    # 2) Зогсоолд ЯГ НЭГ камертай (нэг all-in-one төхөөрөмж орох/гарах хоёуланд) бол түүнийг
    if len(cams) == 1:
        return cams[0].ip_address
    # 3) Олон камертай ч энэ эгнээнийх алга — буруу хаалт нээхээс сэргийлж унана
    return None


async def _execute(db: Session, device: Device, command: str, session_id: str | None,
                   source: str, issued_by: str | None = None, plate: str = "") -> BarrierCommand:
    cmd = BarrierCommand(
        session_id=session_id, device_id=device.id, command=command,
        command_source=source, issued_by=issued_by,
    )
    db.add(cmd)
    db.flush()

    ip = _resolve_ip(db, device)

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

    username = settings.barrier_username or settings.camera_username
    password = settings.barrier_password or settings.camera_password
    cmd.status = "FAILED"
    try:
        async with httpx.AsyncClient(timeout=settings.barrier_timeout_sec) as client:
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
                    res = await rpc.strobe(RPC_METHODS[command], settings.barrier_channel, plate)
                    cmd.status = "SUCCESS"
                    cmd.response_text = f"RPC2 {RPC_METHODS[command]} → {res.get('result')}"
                finally:
                    await rpc.logout()
    except Exception as e:
        cmd.response_text = f"{type(e).__name__}: {str(e)[:400]}"
    cmd.executed_at = datetime.utcnow()
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


# ─── LED дэлгэц / дуут зарлал ────────────────────────────────────────────────

async def display_on_screen(ip: str, text: str, voice_text: str | None = None) -> str:
    """Камерын LED дэлгэцэнд текст харуулна (шаардлагатай бол дуут зарлал).
    Амжилттай бол хоосон мөр, алдаатай бол алдааны тайлбар буцаана."""
    if settings.barrier_mock:
        print(f"[screen] MOCK {ip}: {text}")
        return ""
    username = settings.barrier_username or settings.camera_username
    password = settings.barrier_password or settings.camera_password
    try:
        async with httpx.AsyncClient(timeout=settings.barrier_timeout_sec) as client:
            rpc = DahuaRpc(client, ip, username, password)
            await rpc.login()
            try:
                await rpc.set_screen(text)
                if voice_text:
                    await rpc.set_voice(voice_text)
            finally:
                await rpc.logout()
        # Амжилтыг ч логлоно — LED-ийг нүдээр харахгүйгээр алсаас
        # (journalctl | grep screen) ажилласныг батлахад хэрэгтэй
        print(f"[screen] {ip}: OK «{text}»")
        return ""
    except Exception as e:  # дэлгэцний алдаа хаалт нээх урсгалыг хэзээ ч зогсоохгүй
        err = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"[screen] {ip}: бичиж чадсангүй ({err})")
        return err


def schedule_display(ip: str | None, text: str, voice_text: str | None = None):
    """Event боловсруулалтын дараа дуудна — дэлгэцний командыг АРД НЬ явуулна
    (хаалт нээх/WS broadcast-ыг хэзээ ч хүлээлгэхгүй, snapshot-той ижил хэв маяг)."""
    if not settings.screen_enabled or not ip or not text:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # event loop-гүй орчин (тест г.м) — алгасна
    asyncio.create_task(display_on_screen(ip, text, voice_text))


def render_screen_text(template: str, amount: float | int | None = None,
                       plate: str = "") -> str:
    """Template-ийн {amount}/{plate}-ийг орлуулна. Дүн бүхэл тоогоор."""
    amt = "" if amount is None else f"{int(round(float(amount)))}"
    return template.replace("{amount}", amt).replace("{plate}", plate or "").strip()
