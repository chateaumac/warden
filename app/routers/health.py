from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz(request: Request) -> dict:
    # Unauthenticated (container healthchecks, uptime monitors): reveal nothing about devices
    return {"status": "ok", "version": request.app.state.settings.version}
