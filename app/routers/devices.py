import sqlite3

from fastapi import APIRouter, HTTPException, Request

from ..models import ConnectRequest, DeviceCreate, DeviceUpdate
from ..profiles import DEFAULT_PORTS

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _get_or_404(state, device_id: int) -> dict:
    device = state.db.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"device {device_id} not found")
    return device


def _check_profile(state, profile_id: str | None) -> None:
    if profile_id and profile_id not in state.profiles:
        raise HTTPException(status_code=400, detail=f"unknown profile {profile_id!r}")


@router.get("")
def list_devices(request: Request) -> list[dict]:
    return request.app.state.db.list_devices()


@router.post("", status_code=201)
def create_device(body: DeviceCreate, request: Request) -> dict:
    state = request.app.state
    _check_profile(state, body.profile_id)
    profile = state.profiles.get(body.profile_id) if body.profile_id else None
    port = body.port or (profile.default_port if profile else DEFAULT_PORTS[body.connector])
    try:
        device = state.db.create_device(
            name=body.name or body.host, host=body.host, port=port,
            connector=body.connector, profile_id=body.profile_id,
            mode=body.mode, vars=body.vars, config=body.config,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409,
                            detail=f"a device for {body.host}:{port} already exists") from None
    state.db.add_event(device["id"], "device", "Device added")
    return device


@router.get("/{device_id}")
def get_device(device_id: int, request: Request) -> dict:
    return _get_or_404(request.app.state, device_id)


@router.patch("/{device_id}")
def update_device(device_id: int, body: DeviceUpdate, request: Request) -> dict:
    state = request.app.state
    existing = _get_or_404(state, device_id)
    fields = body.model_dump(exclude_unset=True)
    if "profile_id" in fields:
        _check_profile(state, fields["profile_id"])
        if fields["profile_id"] != existing["profile_id"]:
            # results from the old profile are meaningless now
            fields.update(status="unknown", status_detail="", last_result=[])
    if not fields:
        return existing
    try:
        return state.db.update_device(device_id, **fields)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="another device already uses that host:port") from None


@router.delete("/{device_id}", status_code=204)
def delete_device(device_id: int, request: Request) -> None:
    _get_or_404(request.app.state, device_id)
    request.app.state.db.delete_device(device_id)


@router.post("/{device_id}/connect")
def connect_device(device_id: int, request: Request, body: ConnectRequest | None = None) -> dict:
    state = request.app.state
    _get_or_404(state, device_id)
    return state.engine.connect_device(device_id, (body or ConnectRequest()).timeout_s)


@router.post("/{device_id}/audit")
def audit_device(device_id: int, request: Request) -> dict:
    state = request.app.state
    _get_or_404(state, device_id)
    return state.engine.run(device_id, force_enforce=False)


@router.post("/{device_id}/enforce")
def enforce_device(device_id: int, request: Request) -> dict:
    state = request.app.state
    _get_or_404(state, device_id)
    return state.engine.run(device_id, force_enforce=True)


@router.get("/{device_id}/events")
def device_events(device_id: int, request: Request, limit: int = 100) -> list[dict]:
    state = request.app.state
    _get_or_404(state, device_id)
    return state.db.list_events(device_id, limit=max(1, min(limit, 400)))
