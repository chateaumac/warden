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

from .actions import (
    ActionResult,
    enforce_action,
    evaluate_action,
    installed_packages,
    revert_action,
)
from .connectors.base import ConnectorError, Unauthorized, Unreachable, make_connector
from .db import utcnow

log = logging.getLogger(__name__)

# Listed in full in the event; the notification names this many and counts the rest
NOTIFY_PACKAGES_SHOWN = 15


class Engine:
    def __init__(self, db, profiles: dict, settings, notifier=None):
        self.db = db
        self.profiles = profiles
        self.settings = settings
        self.notifier = notifier
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

    def revert(self, device_id: int, action_ids: list[str] | None = None) -> dict:
        """Undo actions on the device (all of the profile's when action_ids is None) and turn
        them off for it, so a later enforce does not redo them."""
        with self._locks[device_id]:
            return self._revert_locked(device_id, action_ids)

    def audit_all(self) -> None:
        for device in self.db.list_devices():
            if not device["enabled"] or not device["profile_id"]:
                continue
            try:
                self.run(device["id"])
            except Exception:
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
            if "package_disable" in getattr(conn, "supports", frozenset()):
                self._check_firmware(device, identity or {}, conn, cache)
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
            blocked: list[ActionResult] = []
            originals = dict(device.get("originals") or {})
            if do_enforce and any(r.status == "drifted" for r in results):
                actions_by_id = {a["id"]: a for a in profile.actions}
                for i, result in enumerate(results):
                    if result.status != "drifted":
                        continue
                    action = actions_by_id[result.action_id]
                    if action["type"] == "setting" and action["id"] not in originals:
                        originals[action["id"]] = result.observed
                    redone = enforce_action(conn, action, device["vars"], cache)
                    results[i] = redone
                    if redone.status == "fixed":
                        fixed += 1
                    elif redone.status == "blocked":
                        blocked.append(redone)
                    else:
                        failed += 1
                message = f"Re-sanitized: re-applied {fixed} drifted action(s)"
                if failed:
                    message += f", {failed} failed"
                if blocked:
                    message += f", {len(blocked)} refused as unsafe"
                self.db.add_event(device_id, "enforce", message,
                                  level="success" if not (failed or blocked) else "warning",
                                  detail="\n".join(f"{r.name}: {r.detail}" for r in blocked) or None)

            drifted = sum(r.status in ("drifted", "blocked") for r in results)
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
                originals=originals,
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

    def _check_firmware(self, device: dict, identity: dict, conn, cache: dict) -> None:
        """Record a firmware change, and packages that appeared since the last audit.

        An update re-enables what we disabled (the audit catches that) but also brings new
        preloads no profile mentions; partner auto-installers do the same without an update.
        """
        try:
            installed = installed_packages(conn, cache)
        except Exception:  # an audit must not fail on a bookkeeping read
            log.exception("package snapshot failed for device %s", device["id"])
            return
        if not installed:
            return
        before_fp = (device.get("identity") or {}).get("fingerprint", "")
        after_fp = identity.get("fingerprint", "")
        firmware_changed = bool(before_fp and after_fp and before_fp != after_fp)
        previous = set(device.get("packages") or [])
        new = sorted(installed - previous) if previous else []
        self.db.update_device(device["id"], packages=sorted(installed))

        if firmware_changed:
            message = f"Firmware updated: {after_fp}"
            if new:
                message += f" — {len(new)} new package(s)"
            self.db.add_event(device["id"], "firmware", message, level="warning",
                              detail=f"was {before_fp}" + (f"\nnew: {', '.join(new)}" if new else ""))
        elif new:
            self.db.add_event(device["id"], "packages", f"{len(new)} new package(s) installed",
                              detail=", ".join(new))
        if (firmware_changed or new) and self.notifier:
            shown = ", ".join(new[:NOTIFY_PACKAGES_SHOWN])
            if len(new) > NOTIFY_PACKAGES_SHOWN:
                shown += f" and {len(new) - NOTIFY_PACKAGES_SHOWN} more"
            title = (f"Warden: firmware update on {device['name']}" if firmware_changed
                     else f"Warden: new packages on {device['name']}")
            body = (f"New packages: {shown}. Review them for tracking or ads."
                    if new else "No new packages; the next audit re-checks the profile.")
            self.notifier.notify(title=title, message=body, tags=["tv", "package"])

    def _revert_locked(self, device_id: int, action_ids: list[str] | None) -> dict:
        device = self._device(device_id)
        profile = self.profiles.get(device["profile_id"] or "")
        if profile is None:
            return {"ok": False, "error": "no profile assigned", "results": []}
        if action_ids is None:
            overrides = device.get("action_overrides") or {}
            actions = [a for a in profile.actions
                       if overrides.get(a["id"], a.get("default", True))]
        else:
            actions = [profile.action(a) for a in action_ids]
            if None in actions:
                return {"ok": False, "error": "unknown action", "results": []}

        conn = make_connector(device, self.settings)
        try:
            conn.connect()
        except ConnectorError as exc:
            conn.close()
            return {"ok": False, "error": str(exc), "results": []}
        try:
            originals = dict(device.get("originals") or {})
            results = [revert_action(conn, a, device["vars"], originals.get(a["id"]))
                       for a in actions]
        finally:
            conn.close()

        overrides = dict(device.get("action_overrides") or {})
        by_id = {r.action_id: r for r in results}
        for result in results:
            if result.status == "reverted":
                overrides[result.action_id] = False
                originals.pop(result.action_id, None)
        last_result = [asdict(by_id[r["action_id"]]) if r["action_id"] in by_id else r
                       for r in device.get("last_result") or []]
        self.db.update_device(device_id, action_overrides=overrides, originals=originals,
                              last_result=last_result)
        reverted = [r for r in results if r.status == "reverted"]
        failed = [r for r in results if r.status != "reverted"]
        message = f"Reverted {len(reverted)} action(s)"
        if failed:
            message += f", {len(failed)} could not be reverted"
        self.db.add_event(device_id, "revert", message,
                          level="warning" if failed else "success",
                          detail="\n".join(f"{r.name}: {r.detail}" for r in failed) or None)
        return {"ok": not failed, "results": [asdict(r) for r in results]}
