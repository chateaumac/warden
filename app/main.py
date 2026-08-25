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
from .profiles import load_profiles
from .routers import devices as devices_router
from .routers import discovery as discovery_router
from .routers import health as health_router
from .routers import profiles as profiles_router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("warden")


def create_app() -> FastAPI:
    settings = Settings.load()
    db = Database(settings.db_path)
    profiles = load_profiles(settings.profile_dirs)
    engine = Engine(db, profiles, settings)
    discovery = DiscoveryService(
        profiles_getter=lambda: profiles,
        known_hosts_getter=lambda: {d["host"] for d in db.list_devices()},
        default_subnet=settings.discovery_subnet,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("Warden %s — %d profile(s), %d device(s), audit every %ds",
                 settings.version, len(profiles), len(db.list_devices()),
                 settings.audit_interval_s)
        audit_task = asyncio.create_task(engine.loop(), name="warden-audit-loop")
        yield
        audit_task.cancel()
        try:
            await audit_task
        except asyncio.CancelledError:
            pass
        db.close()

    app = FastAPI(title="Warden", version=settings.version, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.profiles = profiles
    app.state.engine = engine
    app.state.discovery = discovery

    for module in (health_router, devices_router, profiles_router, discovery_router):
        app.include_router(module.router)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    return app
