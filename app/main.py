"""Application factory. Run with:

    uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8484
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR, Settings
from .db import Database
from .discovery import DiscoveryService
from .enforcer import Engine
from .guard import GuardEngine
from .integrations import HomeAssistantClient, Notifier
from .integrations import metrics as metrics_module
from .profiles import load_profiles
from .routers import devices as devices_router
from .routers import discovery as discovery_router
from .routers import guard as guard_router
from .routers import health as health_router
from .routers import profiles as profiles_router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("warden")


def create_app() -> FastAPI:
    settings = Settings.load()
    db = Database(settings.db_path)
    profiles = load_profiles(settings.profile_dirs)
    notifier = Notifier(settings.notify_url)

    ha_client = HomeAssistantClient(
        host=settings.mqtt_host,
        port=settings.mqtt_port,
        user=settings.mqtt_user,
        password=settings.mqtt_password,
        db=db,
    )

    guard_engine = GuardEngine(db=db, settings=settings, notifier=notifier, ha_client=ha_client)
    ha_client.guard_engine = guard_engine

    engine = Engine(db, profiles, settings)
    discovery = DiscoveryService(
        profiles_getter=lambda: profiles,
        known_hosts_getter=lambda: {d["host"] for d in db.list_devices()},
        default_subnet=settings.discovery_subnet,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("Warden %s — %d profile(s), %d device(s), audit every %ds, guard polling active",
                 settings.version, len(profiles), len(db.list_devices()),
                 settings.audit_interval_s)

        # Start Home Assistant MQTT client if configured
        ha_client.start()

        audit_task = asyncio.create_task(engine.loop(), name="warden-audit-loop")
        guard_task = asyncio.create_task(guard_engine.loop(), name="warden-guard-loop")

        yield

        audit_task.cancel()
        guard_task.cancel()
        guard_engine.stop()
        ha_client.stop()

        try:
            await asyncio.gather(audit_task, guard_task, return_exceptions=True)
        except Exception:
            pass

        db.close()

    app = FastAPI(title="Warden", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.profiles = profiles
    app.state.engine = engine
    app.state.guard_engine = guard_engine
    app.state.discovery = discovery
    app.state.ha_client = ha_client
    app.state.notifier = notifier

    for module in (health_router, devices_router, profiles_router, discovery_router, guard_router, metrics_module):
        app.include_router(module.router)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app
