from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, engine
from .routers import (
    admin_router, auth_router, barriers_router, cashier_router, compensations_router,
    lpr_router, payments_router, public_router, reports_router, sessions_router,
)
from .ws import manager

Base.metadata.create_all(bind=engine)

from .migrations import run_migrations  # noqa: E402
run_migrations()

app = FastAPI(title=settings.app_name, docs_url="/api/docs", openapi_url="/api/openapi.json")

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
          public_router, barriers_router, cashier_router, reports_router, compensations_router):
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
