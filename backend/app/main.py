import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import DEFAULT_SECRET_KEY, settings
from .database import Base, engine

# journald руу бүх "parking.*" логгер харагдана (print орлож logging ашигладаг болсон)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("parking")

# ── Production аюулгүй байдлын шалгуур (debug=False үед) ──
# Кодын default secret-ээр токен гарын үсэг зурахаас сэргийлж startup-д зогсооно.
if not settings.debug:
    if settings.secret_key == DEFAULT_SECRET_KEY or len(settings.secret_key) < 32:
        raise RuntimeError(
            "PARKING_SECRET_KEY тохируулаагүй/сул байна. .env-д CSPRNG түлхүүр тавина уу: "
            'python3 -c "import secrets;print(secrets.token_urlsafe(48))"')
    if settings.cors_origins == "*":
        log.warning("CORS origins '*' байна — production-д PARKING_CORS_ORIGINS-д домэйноо зааж өгнө үү.")
    if settings.allow_simulate:
        log.warning("PARKING_ALLOW_SIMULATE=true — /api/lpr/simulate нээлттэй. Production-д унтраана уу.")
from .routers import (
    admin_router, auth_router, barriers_router, cashier_router, compensations_router,
    health_router, integration_router, legacy_router, lpr_router, payments_router,
    public_router, reports_router, sessions_router,
)
from .ws import manager

Base.metadata.create_all(bind=engine)

from .migrations import run_migrations  # noqa: E402
run_migrations()

# Камер бүрд хос хаалт байгааг баталгаажуулна (устгагдсаныг сэргээх / дутууг үүсгэх) —
# admin гараар хаалт бүртгэх шаардлагагүй, startup бүрт өөрөө засна.
from .database import SessionLocal  # noqa: E402
from .services.device_auto import ensure_lane_barriers  # noqa: E402
try:
    _db = SessionLocal()
    ensure_lane_barriers(_db)
    _db.close()
except Exception as _e:  # noqa: BLE001 — баталгаажуулалт унасан ч серверийг унагахгүй
    print(f"[device_auto] startup шалгалт алдаа: {_e}")

# API баримт (Swagger) зөвхөн debug үед нээлттэй — production-д API гадаргууг ил гаргахгүй
app = FastAPI(
    title=settings.app_name,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.debug else None,
)

# CORS: default "*" (nginx-ийн ард same-origin). Production-д PARKING_CORS_ORIGINS-оор домэйн зааж өгнө.
# "*" үед credentials-ийг унтраана (стандарт шаардлага — wildcard + credentials зөрчилддөг).
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth_router, lpr_router, admin_router, sessions_router, payments_router,
          public_router, barriers_router, cashier_router, reports_router, compensations_router,
          health_router, integration_router, legacy_router):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}


# Асаалттай background task-ууд — shutdown дээр ЦЭВЭР зогсоохын тулд хөтөлнө
_bg_tasks: list = []


def _bg_task(coro, name: str):
    """Background task үүсгэхдээ уналтыг нь ЗААВАЛ логлоно — чимээгүй үхсэн task
    (жишээ нь камерын поллер зогсчихсоныг хэн ч мэдэхгүй байх) илэрдэг болно."""
    import asyncio
    task = asyncio.get_event_loop().create_task(coro, name=name)
    _bg_tasks.append(task)

    def _done(t: asyncio.Task):
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            log.error("background task '%s' унав: %r", name, exc)

    task.add_done_callback(_done)
    return task


@app.on_event("startup")
async def start_vat_auto_send():
    """ТЕГ шаардлага №4: борлуулалтын мэдээг өдөрт нэгээс доошгүй удаа АВТОМАТААР илгээх."""
    import asyncio

    from .services import ebarimt

    async def daily_send():
        """30 мин тутам шалгаж, сүүлийн илгээлтээс 24ц өнгөрсөн бол илгээнэ.

        Өмнө нь sleep(24ц) байсан — өдөр бүр deploy/restart хийгддэг серверт
        ХЭЗЭЭ Ч ажилладаггүй байв. Одоо .last_ebarimt_send файл (restart-д
        тэсвэртэй) дээр тулгуурлана; алдааг залгихгүй логлоно."""
        while True:
            try:
                last = ebarimt.last_send_at()
                import time as _t
                if last is None or _t.time() - last >= 24 * 3600:
                    await ebarimt.send_data()
                    log.info("e-Barimt мэдээ ТЕГ-т илгээгдлээ (авто, 24ц тутам)")
            except Exception as e:  # noqa: BLE001 — гараар илгээх товч нөөц болно
                log.error("e-Barimt авто илгээлт алдаа: %r — 30 мин дараа дахин оролдоно", e)
            await asyncio.sleep(30 * 60)

    _bg_task(daily_send(), "ebarimt-daily-send")

    async def loop_lag_monitor():
        """Event loop царцаж байгааг ИЛРҮҮЛНЭ.

        Backend нь async loop дотроос СИНХРОН psycopg2 ашигладаг тул удаан
        query/түгжээ хүлээлт бүх системийг зэрэг царцаадаг — тэр үед хаалтны
        asyncio.wait_for таймер ч ажиллахгүй (production дээр 15с төсөвтэй
        хаалт 101 секунд үргэлжилсэн). Энэ ажиглагч 1с тутам сэрж, ХИЧНЭЭН
        хоцорсныг хэмжинэ: хоцролт нь loop блоклогдсон хугацаа юм."""
        import time as _t
        while True:
            t0 = _t.monotonic()
            await asyncio.sleep(1.0)
            lag_ms = int((_t.monotonic() - t0 - 1.0) * 1000)
            if lag_ms >= settings.loop_lag_warn_ms:
                log.warning("event loop %dмс ЦАРЦЛАА — тэр хугацаанд хаалт/LPR "
                            "боловсруулалт бүхэлдээ зогссон (удаан DB query эсвэл "
                            "түгжээ хүлээлт байх магадлалтай)", lag_ms)

    _bg_task(loop_lag_monitor(), "loop-lag-monitor")

    async def camera_silence_monitor():
        """Камер event ИЛГЭЭХЭЭ БОЛЬСОН эсэхийг ажиглана.

        Камер сервер рүү хүрч байгаа (гараар хаалт нээгддэг) атлаа машин ирэхэд
        огт танихгүй байх тохиолдол гардаг: камер→сервер чиглэл тасарсан байна.
        Үүнийг хэн ч ажиглахгүй бол зогсоол хагас өдөр «танихгүй» ажиллана
        (2026-07-28: MONNIS гарах камер 06:38-аас 8 цаг чимээгүй байсныг зөвхөн
        гомдол ирсний дараа мэдсэн). Одоо лог дээр өөрөө анхааруулна."""
        from datetime import datetime, timedelta
        from .models import Device, ParkingSite
        warned: set[str] = set()
        await asyncio.sleep(120)   # startup-ийн дараа камерууд холбогдох завсар
        while True:
            try:
                db = SessionLocal()
                try:
                    cutoff = datetime.utcnow() - timedelta(
                        minutes=settings.camera_silence_warn_min)
                    cams = db.query(Device).filter(
                        Device.device_type == "camera", Device.status == "active",
                        Device.ip_address != "").all()
                    # Камер бүрийн сүүлийн ЖИНХЭНЭ уншилт (heartbeat илгээдэггүй
                    # firmware-т last_seen хоцордог тул хоёуланг нь харна)
                    from sqlalchemy import func as _f
                    from .models import LprEvent as _Ev
                    last_ev = dict(
                        db.query(_Ev.device_id, _f.max(_Ev.created_at))
                        .filter(_Ev.device_id.in_([c.id for c in cams]))
                        .group_by(_Ev.device_id).all()) if cams else {}
                    for c in cams:
                        marks = [t for t in (c.last_seen, last_ev.get(c.id)) if t]
                        newest = max(marks) if marks else None
                        silent = newest is None or newest < cutoff
                        site = db.get(ParkingSite, c.site_id)
                        tag = f"{site.site_code if site else '?'} {c.name} ({c.ip_address})"
                        if silent and c.id not in warned:
                            warned.add(c.id)
                            mins = ("хэзээ ч" if not newest else
                                    f"{int((datetime.utcnow() - newest).total_seconds()/60)} мин")
                            log.warning("КАМЕР ЧИМЭЭГҮЙ: %s — %s мэдээлээгүй. Машин ирэхэд "
                                        "танихгүй байж магадгүй. Шалгах: tools/camera_push_check.py %s",
                                        tag, mins, c.ip_address)
                        elif not silent and c.id in warned:
                            warned.discard(c.id)
                            log.info("камер сэргэлээ: %s", tag)
                finally:
                    db.close()
            except Exception as e:  # noqa: BLE001
                log.error("камерын чимээгүй байдлын шалгалт алдаа: %r", e)
            await asyncio.sleep(settings.camera_silence_check_sec)

    _bg_task(camera_silence_monitor(), "camera-silence-monitor")

    # CGI event pull — камераас ANPR татах (PARKING_CGI_POLL=true үед)
    from .services.cgi_poller import supervisor as cgi_supervisor
    _bg_task(cgi_supervisor(), "cgi-poller")

    # Event зургийн стрим — snapManager.attachFileProc (PARKING_SNAP_PULL, default true)
    from .services.snap_puller import supervisor as snap_supervisor
    _bg_task(snap_supervisor(), "snap-puller")

    # Гацсан session-ийн авто цэвэрлэгээ (site.auto_close_hours / default 72ц)
    from .services.auto_close import supervisor as auto_close_supervisor
    _bg_task(auto_close_supervisor(), "auto-close")


@app.on_event("shutdown")
async def stop_background_tasks():
    """Restart/зогсоохын өмнө камер руу нээсэн стримийг ЦЭВЭР хаана.

    ЯАГААД ЧУХАЛ ВЭ: cgi_poller нь камер бүрт eventManager.cgi?action=attach
    гэсэн БАЙНГЫН HTTP холболт барьдаг. Процесс шууд үхэхэд камер тал холболтоо
    удаан хугацаанд «нээлттэй» гэж үзсээр байдаг. Dahua камер зэрэгцээ
    subscription-ийн ХЯЗГААРТАЙ тул орхигдсон холболт хуримтлагдвал шинэ attach
    хүсэлтийг HTTP 400-аар татгалздаг — 2026-07-28-нд MONNIS гарах камер өглөө
    ажиллаж байгаад олон удаагийн deploy-ийн дараа бүх attach хүсэлтийг
    татгалзаж, 9 цаг машин таниагүй.

    Одоо task-уудыг cancel хийж стримийг хаалгана (httpx холболтоо таслана)."""
    import asyncio
    if not _bg_tasks:
        return
    log.info("зогсож байна — %d background task-ыг цэвэр хаана", len(_bg_tasks))
    for t in _bg_tasks:
        if not t.done():
            t.cancel()
    # Стрим хаагдах хүртэл богино хугацаанд хүлээнэ (deploy-г удаашруулахгүй)
    try:
        await asyncio.wait_for(
            asyncio.gather(*_bg_tasks, return_exceptions=True), timeout=5.0)
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("зарим task 5с дотор зогсоогүй — албадан хаалаа")
    _bg_tasks.clear()
    # Камер руу нээсэн keep-alive холболтуудыг ч цэвэр хаана
    try:
        from .services.barrier import close_camera_clients
        await close_camera_clients()
    except Exception as e:  # noqa: BLE001
        log.warning("камерын холболт хаахад алдаа: %r", e)


@app.websocket("/ws/sites/{site_id}")
async def ws_site(websocket: WebSocket, site_id: str):
    """Real-time events: dashboard, касс, PAX терминал холбогдоно.
    site_id="all" бол бүх зогсоолын event сонсоно."""
    key = "*" if site_id == "all" else site_id
    await manager.connect(websocket, key)
    try:
        while True:
            await websocket.receive_text()  # ping/pong
    except WebSocketDisconnect:
        await manager.disconnect(websocket, key)
