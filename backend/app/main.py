import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import DEFAULT_SECRET_KEY, settings
from .database import Base, engine

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
    health_router, lpr_router, payments_router, public_router, reports_router, sessions_router,
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
          health_router):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name}


@app.on_event("startup")
async def start_vat_auto_send():
    """ТЕГ шаардлага №4: борлуулалтын мэдээг өдөрт нэгээс доошгүй удаа АВТОМАТААР илгээх."""
    import asyncio

    from .services import ebarimt

    async def daily_send():
        while True:
            await asyncio.sleep(24 * 3600)
            try:
                await ebarimt.send_data()
            except Exception:
                pass  # дараагийн өдөр дахин оролдоно; гараар илгээх товч нөөц болно

    asyncio.get_event_loop().create_task(daily_send())

    # CGI event pull — камераас ANPR татах (PARKING_CGI_POLL=true үед)
    from .services.cgi_poller import supervisor as cgi_supervisor
    asyncio.get_event_loop().create_task(cgi_supervisor())

    # Event зургийн стрим — snapManager.attachFileProc (PARKING_SNAP_PULL, default true)
    from .services.snap_puller import supervisor as snap_supervisor
    asyncio.get_event_loop().create_task(snap_supervisor())

    # Гацсан session-ийн авто цэвэрлэгээ (site.auto_close_hours / default 72ц)
    from .services.auto_close import supervisor as auto_close_supervisor
    asyncio.get_event_loop().create_task(auto_close_supervisor())


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
