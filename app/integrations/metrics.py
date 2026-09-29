"""Prometheus metrics and observability endpoint."""

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics(request: Request) -> str:
    db = request.app.state.db
    guard_engine = getattr(request.app.state, "guard_engine", None)

    devices = db.list_devices()
    rules = db.list_channel_rules()

    total_devices = len(devices)
    enabled_devices = sum(1 for d in devices if d.get("enabled"))
    total_rules = len(rules)
    enabled_rules = sum(1 for r in rules if r.get("enabled"))

    lines = [
        "# HELP warden_devices_total Total registered devices",
        "# TYPE warden_devices_total gauge",
        f"warden_devices_total {total_devices}",
        "# HELP warden_devices_enabled Enabled devices",
        "# TYPE warden_devices_enabled gauge",
        f"warden_devices_enabled {enabled_devices}",
        "# HELP warden_channel_rules_total Total channel rules",
        "# TYPE warden_channel_rules_total gauge",
        f"warden_channel_rules_total {total_rules}",
        "# HELP warden_channel_rules_enabled Enabled channel rules",
        "# TYPE warden_channel_rules_enabled gauge",
        f"warden_channel_rules_enabled {enabled_rules}",
    ]

    if guard_engine:
        for dev in devices:
            dev_id = dev["id"]
            state = guard_engine.get_state(dev_id)
            state_val = 1 if state.state.value == "monitoring" else 0
            lines.append(f'warden_device_monitoring{{device_id="{dev_id}",name="{dev.get("name")}"}} {state_val}')
            snoozed_val = 1 if state.is_snoozed else 0
            lines.append(f'warden_device_snoozed{{device_id="{dev_id}",name="{dev.get("name")}"}} {snoozed_val}')

    return "\n".join(lines) + "\n"
