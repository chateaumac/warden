from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(request: Request) -> dict:
    state = request.app.state
    return {
        "status": "ok",
        "version": state.settings.version,
        "devices": len(state.db.list_devices()),
        "profiles": len(state.profiles),
    }
