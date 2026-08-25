"""The enforcement engine: connect → audit → (re-)apply drifted actions.

A device in 'monitor' mode only reports drift; in 'enforce' mode drift is
re-applied automatically on the scheduled audit. Manual "Enforce now" always
applies regardless of mode.
"""

import asyncio
import logging
import threading
from collections import defaultdict
from dataclasses import asdict

from .actions import ActionResult, enforce_action, evaluate_action
from .connectors.base import ConnectorError, Unauthorized, Unreachable, make_connector
from .db import utcnow

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, db, profiles: dict, settings):
        self.db = db
        self.profiles = profiles
        self.settings = settings
        self._locks: dict[int, threading.Lock] = defaultdict(threading.Lock)

    # ---------------------------------------------------------------- public

    def connect_device(self, device_id: int, timeout_s: float = 30.0) -> dict:
        """Interactive pairing: long auth timeout so the user has time to accept
        the authorization dialog on the device's screen."""
        device = self._device(device_id)
        conn = make_connector(device, self.settings)
        try:
            identity = conn.connect(auth_timeout_s=timeout_s)
        except ConnectorError as exc:
            status = ("unauthorized" if isinstance(exc, Unauthorized)
                      else "unreachable" if isinstance(exc, Unreachable) else "error")
            self._set_status(device, status, str(exc))
            return {"ok": False, "status": status, "error": str(exc)}
        finally:
            conn.close()

        self.db.update_device(device_id, identity=identity or device["identity"],
                              last_seen=utcnow())
        self.db.add_event(device_id, "connect",
                          "Device authorized Warden's key — connection established",
                          level="success")
        audit = self.run(device_id, force_enforce=False) if device.get("profile_id") else None
        return {"ok": True, "status": (audit or {}).get("status", "unknown"),
                "identity": identity, "audit": audit}

    def run(self, device_id: int, force_enforce: bool | None = None) -> dict:
        """Audit one device; enforce drift per mode (or force_enforce override)."""
        with self._locks[device_id]:
            return self._run_locked(device_id, force_enforce)

    def audit_all(self) -> None:
        for device in self.db.list_devices():
            if not device["enabled"] or not device["profile_id"]:
                continue
            try:
                self.run(device["id"])
            except Exception:  # noqa: BLE001 - one bad device must not stop the sweep
                log.exception("scheduled audit failed for device %s", device["id"])

    async def loop(self) -> None:
        await asyncio.sleep(self.settings.audit_startup_delay_s)
        while True:
            await asyncio.to_thread(self.audit_all)
            await asyncio.sleep(self.settings.audit_interval_s)

    # -------------------------------------------------------------- internals

    def _device(self, device_id: int) -> dict:
        device = self.db.get_device(device_id)
        if device is None:
            raise KeyError(f"device {device_id} not found")
        return device

    def _set_status(self, device: dict, status: str, detail: str) -> None:
        self.db.update_device(device["id"], status=status, status_detail=detail,
                              last_audit=utcnow())
        if device["status"] != status:
            level = "error" if status in ("unreachable", "error") else "warning"
            self.db.add_event(device["id"], "status", f"{status}: {detail}", level=level)

    def _run_locked(self, device_id: int, force_enforce: bool | None) -> dict:
        device = self._device(device_id)
        profile = self.profiles.get(device["profile_id"] or "")
        if profile is None:
            self._set_status(device, "error", "No profile assigned (or profile file missing)")
            return {"status": "error", "error": "no profile assigned", "results": []}

        conn = make_connector(device, self.settings)
        try:
            identity = conn.connect()
        except ConnectorError as exc:
            status = ("unauthorized" if isinstance(exc, Unauthorized)
                      else "unreachable" if isinstance(exc, Unreachable) else "error")
            self._set_status(device, status, str(exc))
            conn.close()
            return {"status": status, "error": str(exc), "results": []}

        try:
            cache: dict = {}
            overrides = device.get("action_overrides") or {}
            results: list[ActionResult] = []
            for action in profile.actions:
                if not overrides.get(action["id"], action.get("default", True)):
                    results.append(ActionResult(action["id"], action.get("name", action["id"]),
                                                "disabled", detail="Disabled for this device"))
                    continue
                results.append(evaluate_action(conn, action, device["vars"], cache))

            do_enforce = force_enforce if force_enforce is not None else device["mode"] == "enforce"
            fixed = failed = 0
            if do_enforce and any(r.status == "drifted" for r in results):
                actions_by_id = {a["id"]: a for a in profile.actions}
                for i, result in enumerate(results):
                    if result.status != "drifted":
                        continue
                    redone = enforce_action(conn, actions_by_id[result.action_id],
                                            device["vars"], cache)
                    results[i] = redone
                    if redone.status == "fixed":
                        fixed += 1
                    else:
                        failed += 1
                message = f"Re-sanitized: re-applied {fixed} drifted action(s)"
                if failed:
                    message += f", {failed} failed"
                self.db.add_event(device_id, "enforce", message,
                                  level="success" if not failed else "warning")

            drifted = sum(r.status == "drifted" for r in results)
            errors = sum(r.status == "error" for r in results)
            ok = sum(r.status in ("compliant", "fixed", "na") for r in results)
            if drifted:
                status = "drifted"
            elif errors:
                status = "error"
            else:
                status = "compliant"
            parts = [f"{ok} ok"]
            if fixed:
                parts.append(f"{fixed} fixed")
            if drifted:
                parts.append(f"{drifted} drifted")
            if errors:
                parts.append(f"{errors} errored")
            status_detail = ", ".join(parts)

            now = utcnow()
            self.db.update_device(
                device_id,
                status=status, status_detail=status_detail,
                identity=identity or device["identity"],
                last_seen=now, last_audit=now,
                last_result=[asdict(r) for r in results],
            )
            if device["status"] != status:
                self.db.add_event(
                    device_id, "audit",
                    f"Status changed: {device['status']} → {status} ({status_detail})",
                    level="success" if status == "compliant" else "warning",
                )
            return {"status": status, "status_detail": status_detail,
                    "fixed": fixed, "results": [asdict(r) for r in results]}
        finally:
            conn.close()
