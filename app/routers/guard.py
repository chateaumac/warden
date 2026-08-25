"""API endpoints for Channel Guard, Content Governance, and Live Payload Inspector."""

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..connectors.base import make_connector
from ..guard.actions import execute_action
from ..models import (
    ChannelRuleCreate,
    ChannelRuleUpdate,
    GuardSettingsUpdate,
    SnoozeRequest,
    TestRuleRequest,
)

router = APIRouter(prefix="/api/guard", tags=["guard"])


# ------------------------------------------------------------- channel rules

@router.get("/rules")
def list_rules(request: Request) -> list[dict[str, Any]]:
    return request.app.state.db.list_channel_rules()


@router.post("/rules", status_code=201)
def create_rule(body: ChannelRuleCreate, request: Request) -> dict[str, Any]:
    return request.app.state.db.create_channel_rule(**body.model_dump())


@router.get("/rules/{rule_id}")
def get_rule(rule_id: int, request: Request) -> dict[str, Any]:
    rule = request.app.state.db.get_channel_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, body: ChannelRuleUpdate, request: Request) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    rule = request.app.state.db.update_channel_rule(rule_id, **fields)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, request: Request) -> None:
    request.app.state.db.delete_channel_rule(rule_id)


@router.post("/test-pattern")
def test_pattern(body: TestRuleRequest) -> dict[str, Any]:
    try:
        rx = re.compile(body.pattern, re.IGNORECASE)
        m = rx.search(body.sample_text)
        return {
            "matched": bool(m),
            "pattern": body.pattern,
            "matched_text": m.group(0) if m else None,
            "span": m.span() if m else None,
        }
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}")


# ------------------------------------------------------------- device guard

@router.get("/devices/{device_id}/settings")
def get_settings(device_id: int, request: Request) -> dict[str, Any]:
    return request.app.state.db.get_guard_settings(device_id)


@router.patch("/devices/{device_id}/settings")
def update_settings(
    device_id: int,
    body: GuardSettingsUpdate,
    request: Request,
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    return request.app.state.db.update_guard_settings(device_id, **fields)


@router.get("/devices/{device_id}/state")
def get_device_state(device_id: int, request: Request) -> dict[str, Any]:
    guard_engine = getattr(request.app.state, "guard_engine", None)
    if not guard_engine:
        raise HTTPException(status_code=503, detail="Guard engine not initialized")

    state = guard_engine.get_state(device_id)
    return {
        "device_id": device_id,
        "state": state.state.value,
        "current_package": state.current_package,
        "title": state.current_media.title,
        "subtitle": state.current_media.subtitle,
        "is_playing": state.current_media.is_playing,
        "playback_state": state.current_media.playback_state,
        "status_detail": state.status_detail,
        "is_snoozed": state.is_snoozed,
        "snooze_remaining_s": state.snooze_remaining_s,
        "last_action_name": state.last_action_name,
        "last_matched_rule": state.last_matched_rule,
        "last_violation_detail": state.last_violation_detail,
    }


@router.post("/devices/{device_id}/snooze")
def snooze_device(device_id: int, body: SnoozeRequest, request: Request) -> dict[str, Any]:
    guard_engine = getattr(request.app.state, "guard_engine", None)
    if not guard_engine:
        raise HTTPException(status_code=503, detail="Guard engine not initialized")

    state = guard_engine.get_state(device_id)
    state.snooze(body.duration_s)
    return {
        "ok": True,
        "device_id": device_id,
        "snoozed": True,
        "snooze_remaining_s": state.snooze_remaining_s,
    }


@router.post("/devices/{device_id}/unsnooze")
def unsnooze_device(device_id: int, request: Request) -> dict[str, Any]:
    guard_engine = getattr(request.app.state, "guard_engine", None)
    if not guard_engine:
        raise HTTPException(status_code=503, detail="Guard engine not initialized")

    state = guard_engine.get_state(device_id)
    state.unsnooze()
    return {"ok": True, "device_id": device_id, "snoozed": False}


# ------------------------------------------------------------- live inspector

@router.get("/devices/{device_id}/inspect")
def inspect_device(device_id: int, request: Request) -> dict[str, Any]:
    guard_engine = getattr(request.app.state, "guard_engine", None)
    if not guard_engine:
        raise HTTPException(status_code=503, detail="Guard engine not initialized")

    return guard_engine.inspect_device(device_id)


@router.post("/devices/{device_id}/test-action")
def test_action(
    device_id: int,
    action: str,
    request: Request,
    package: str = "",
) -> dict[str, Any]:
    device = request.app.state.db.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    conn = make_connector(device, request.app.state.settings)
    try:
        conn.connect(auth_timeout_s=5.0)
        res = execute_action(conn, action=action, target_pkg=package)
        return {"ok": True, "result": res}
    finally:
        conn.close()
