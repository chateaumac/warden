"""Async Real-Time Content Governance & Channel Guard Supervisor."""

import asyncio
import logging
import time
from typing import Any

from ..connectors.base import ConnectorError, Unauthorized, Unreachable, make_connector
from .actions import execute_action
from .parser import MediaMetadata, parse_media_session, parse_window_focus
from .rules import ChannelRule, evaluate_rules
from .state import DeviceState, GuardState

log = logging.getLogger("warden.guard")


class GuardEngine:
    def __init__(self, db, settings, notifier=None, ha_client=None):
        self.db = db
        self.settings = settings
        self.notifier = notifier
        self.ha_client = ha_client
        self.states: dict[int, GuardState] = {}
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def get_state(self, device_id: int) -> GuardState:
        if device_id not in self.states:
            self.states[device_id] = GuardState(device_id=device_id)
        return self.states[device_id]

    def list_rules(self) -> list[ChannelRule]:
        return [ChannelRule.from_dict(r) for r in self.db.list_channel_rules()]

    # ----------------------------------------------------------- device inspection

    def inspect_device(self, device_id: int) -> dict[str, Any]:
        """Synchronous on-demand live inspection of TV state and raw dumpsys output."""
        device = self.db.get_device(device_id)
        if not device:
            return {"ok": False, "error": f"Device {device_id} not found"}

        conn = make_connector(device, self.settings)
        try:
            conn.connect(auth_timeout_s=5.0)

            # Inspect window & media_session
            raw_win = conn.shell("dumpsys window windows")
            raw_media = conn.shell("dumpsys media_session")
            raw_power = conn.shell("dumpsys power")

            fg_pkg = parse_window_focus(raw_win)
            meta = parse_media_session(raw_media, foreground_pkg=fg_pkg)

            # Screen interactive check
            is_screen_on = "mHoldingDisplaySuspendBlocker=true" in raw_power or "Display Power: state=ON" in raw_power

            # Rule evaluation test
            rules = self.list_rules()
            match = evaluate_rules(rules, meta, active_pkg=fg_pkg)

            return {
                "ok": True,
                "device_id": device_id,
                "device_name": device.get("name"),
                "screen_on": is_screen_on,
                "foreground_package": fg_pkg,
                "parsed_metadata": {
                    "package": meta.package,
                    "title": meta.title,
                    "subtitle": meta.subtitle,
                    "is_playing": meta.is_playing,
                    "playback_state": meta.playback_state,
                    "full_text": meta.full_text,
                },
                "matched_rule": {
                    "matched": bool(match),
                    "rule_name": match.rule.name if match and match.rule else None,
                    "pattern": match.matched_pattern if match else None,
                    "matched_text": match.matched_text if match else None,
                    "action": match.action if match else None,
                } if match else None,
                "raw": {
                    "media_session": raw_media,
                    "window": raw_win[:2000],  # first 2000 chars of window
                }
            }
        except (ConnectorError, Unauthorized, Unreachable, Exception) as exc:
            return {"ok": False, "error": str(exc)}
        finally:
            conn.close()

    # ------------------------------------------------------------- polling loop

    async def run_device_poll(self, device_id: int) -> None:
        """Poll one device continuously in background with state-aware backoff."""
        while self._running:
            device = self.db.get_device(device_id)
            if not device or not device.get("enabled"):
                await asyncio.sleep(10.0)
                continue

            guard_cfg = self.db.get_guard_settings(device_id)
            if not guard_cfg.get("enabled", True):
                await asyncio.sleep(10.0)
                continue

            state = self.get_state(device_id)
            poll_interval = state.get_poll_interval(guard_cfg.get("poll_interval_s", 1.2))

            # If currently snoozed, skip evaluation
            if state.is_snoozed:
                if self.ha_client:
                    self.ha_client.publish_state(device, state)
                await asyncio.sleep(poll_interval)
                continue

            # Execute poll step in worker thread to prevent event loop blocking
            await asyncio.to_thread(self._poll_step, device, guard_cfg, state)

            if self.ha_client:
                self.ha_client.publish_state(device, state)

            await asyncio.sleep(poll_interval)

    def _poll_step(self, device: dict, guard_cfg: dict, state: GuardState) -> None:
        device_id = device["id"]
        conn = make_connector(device, self.settings)
        now = time.time()
        state.last_poll_ts = now

        try:
            conn.connect(auth_timeout_s=3.0)
            state.consecutive_errors = 0

            # 1. Quick check if screen is asleep
            raw_power = conn.shell("dumpsys power")
            is_screen_on = "mHoldingDisplaySuspendBlocker=true" in raw_power or "Display Power: state=ON" in raw_power or "mInteractive=true" in raw_power

            if not is_screen_on:
                state.state = DeviceState.STANDBY
                state.status_detail = "TV is in standby / screen off"
                return

            # 2. Check foreground window / app
            raw_win = conn.shell("dumpsys window windows")
            fg_pkg = parse_window_focus(raw_win)
            state.current_package = fg_pkg

            # Check if any target package or streaming app is active
            rules = self.list_rules()
            target_pkgs = set()
            for r in rules:
                target_pkgs.update(r.target_packages)

            # Default to YTTV if no rules configured
            if not target_pkgs:
                target_pkgs.add("com.google.android.youtube.tvunplugged")

            is_target_active = any(p in fg_pkg for p in target_pkgs) if "*" not in target_pkgs else bool(fg_pkg)

            if not is_target_active:
                state.state = DeviceState.IDLE
                state.status_detail = f"App in foreground: {fg_pkg or 'Home Launcher'}"
                state.current_media = MediaMetadata(package=fg_pkg)
                return

            # 3. Read media session metadata (Zero-wear fast path)
            raw_media = conn.shell("dumpsys media_session")
            meta = parse_media_session(raw_media, foreground_pkg=fg_pkg)
            state.current_media = meta

            # Cooldown check
            cooldown_s = guard_cfg.get("cooldown_s", 15.0)
            in_cooldown = (now - state.last_action_ts) < cooldown_s

            if in_cooldown:
                state.state = DeviceState.COOLDOWN
                state.status_detail = f"Cooldown ({int(cooldown_s - (now - state.last_action_ts))}s remaining) after {state.last_action_name}"
                return

            # 4. Evaluate rules
            match = evaluate_rules(rules, meta, active_pkg=fg_pkg)

            if match and match.matched:
                action_to_take = match.action or guard_cfg.get("default_action", "auto_skip")
                log.warning("Warden Rule Hit on %s [%s]: matched '%s' in '%s'. Executing %s",
                            device.get("name"), device.get("host"), match.matched_pattern,
                            meta.title or meta.full_text[:60], action_to_take)

                # Execute action
                res = execute_action(
                    conn,
                    action=action_to_take,
                    target_pkg=fg_pkg,
                    key_sequence=match.key_sequence,
                )

                state.last_action_ts = now
                state.last_action_name = action_to_take
                state.last_matched_rule = match.rule.name if match.rule else "Rule Match"
                state.last_violation_detail = f"Blocked: {meta.title or match.matched_text} ({match.matched_pattern})"
                state.state = DeviceState.COOLDOWN
                state.status_detail = f"Enforced {action_to_take}: {state.last_violation_detail}"

                # Record event in DB
                self.db.add_event(
                    device_id=device_id,
                    kind="guard",
                    level="warning",
                    message=f"Channel Guard [{action_to_take}]: {state.last_violation_detail}",
                    detail=f"Rule: {state.last_matched_rule} | App: {fg_pkg} | Action Detail: {res.get('detail')}",
                )

                # Send notifications
                if self.notifier:
                    self.notifier.notify(
                        title=f"Warden: Blocked Channel on {device.get('name')}",
                        message=f"Matched '{match.rule.name}' ({match.matched_text}). Enforced: {action_to_take}.",
                        tags=["tv", "warning"]
                    )
            else:
                state.state = DeviceState.MONITORING
                channel_info = meta.title or meta.subtitle or "Live Playback"
                state.status_detail = f"Monitoring {fg_pkg} ({channel_info})"

        except (Unreachable, ConnectionRefusedError, OSError) as exc:
            state.state = DeviceState.OFFLINE
            state.consecutive_errors += 1
            state.status_detail = f"Device unreachable ({exc.__class__.__name__})"
        except Unauthorized:
            state.state = DeviceState.OFFLINE
            state.status_detail = "Unauthorized (ADB key prompt pending)"
        except Exception as exc:
            state.consecutive_errors += 1
            state.status_detail = f"Poll error: {exc}"
            log.debug("Poll error on %s: %s", device.get("name"), exc)
        finally:
            conn.close()

    # ---------------------------------------------------------- supervisor loop

    async def loop(self) -> None:
        """Main guard supervisor that dynamically manages per-device worker tasks."""
        self._running = True
        log.info("Warden Channel Guard supervisor started")

        while self._running:
            devices = self.db.list_devices()
            current_device_ids = {d["id"] for d in devices if d.get("enabled")}

            # Start worker for any new devices
            for dev_id in current_device_ids:
                if not any(t.get_name() == f"guard-poll-{dev_id}" and not t.done() for t in self._tasks):
                    task = asyncio.create_task(self.run_device_poll(dev_id), name=f"guard-poll-{dev_id}")
                    self._tasks.append(task)

            # Cleanup finished tasks
            self._tasks = [t for t in self._tasks if not t.done()]

            await asyncio.sleep(5.0)

    def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
