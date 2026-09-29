"""Async Real-Time Content Governance & Channel Guard Supervisor."""

import asyncio
import logging
import time
from typing import Any

from ..connectors.base import ConnectorError, Unauthorized, Unreachable, make_connector
from .actions import YTTV_PACKAGE, execute_action
from .parser import MediaMetadata, parse_media_session, parse_window_focus
from .rules import ChannelRule, RuleMatch, evaluate_rules
from .state import DeviceState, GuardState

log = logging.getLogger("warden.guard")

# A YouTube TV channel switch takes ~5s (no session -> "null, null, null" -> new channel).
# A dead or bogus link changes nothing at all, and a good one drops the session within ~2s,
# so a link that shows no change by TUNE_REJECT_S is abandoned for the next one.
TUNE_VERIFY_TIMEOUT_S = 12.0
TUNE_REJECT_S = 4.0
TUNE_VERIFY_STEP_S = 1.0


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
            meta = parse_media_session(raw_media, target_pkg=fg_pkg)

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
                    "channel": meta.channel,
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

    @staticmethod
    def _target_packages(rules: list[ChannelRule]) -> set[str]:
        pkgs = {p for r in rules for p in r.target_packages}
        return pkgs or {YTTV_PACKAGE}

    @staticmethod
    def _read_media(conn, target_pkgs: set[str]) -> MediaMetadata:
        """One cheap read (~2 KB): the media session of the first target app playing a channel."""
        raw = conn.shell("dumpsys media_session")
        if "*" in target_pkgs:
            return parse_media_session(raw)
        metas = [parse_media_session(raw, target_pkg=p) for p in sorted(target_pkgs)]
        return next((m for m in metas if m.is_enforceable), metas[0])

    def _poll_step(self, device: dict, guard_cfg: dict, state: GuardState) -> None:
        conn = make_connector(device, self.settings)
        now = time.time()
        state.last_poll_ts = now

        try:
            conn.connect(auth_timeout_s=3.0)
            state.consecutive_errors = 0

            rules = [r for r in self.list_rules() if r.enabled]
            target_pkgs = self._target_packages(rules)
            meta = self._read_media(conn, target_pkgs)
            state.current_media = meta
            state.current_package = meta.package

            if not meta.is_enforceable:
                state.state = DeviceState.IDLE
                state.status_detail = f"No live channel playing ({meta.playback_state})"
                return

            # Cooldown check
            cooldown_s = guard_cfg.get("cooldown_s", 15.0)
            if (now - state.last_action_ts) < cooldown_s:
                state.state = DeviceState.COOLDOWN
                state.status_detail = f"Cooldown ({int(cooldown_s - (now - state.last_action_ts))}s remaining) after {state.last_action_name}"
                return

            match = evaluate_rules(rules, meta, active_pkg=meta.package)
            if match and match.matched:
                self._enforce(conn, device, guard_cfg, state, meta, match, rules, target_pkgs, now)
            else:
                state.state = DeviceState.MONITORING
                state.status_detail = f"Monitoring {meta.channel}: {meta.title or 'Live'}"

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

    def _enforce(self, conn, device: dict, guard_cfg: dict, state: GuardState,
                 meta: MediaMetadata, match: RuleMatch, rules: list[ChannelRule],
                 target_pkgs: set[str], now: float) -> None:
        action = match.action or guard_cfg.get("default_action", "home")
        blocked = f"{meta.channel}: {meta.title}" if meta.title else meta.channel
        log.warning("Warden Rule Hit on %s [%s]: matched '%s' on %s. Executing %s",
                    device.get("name"), device.get("host"), match.matched_pattern, blocked, action)

        level = "warning"
        if action == "tune":
            action, detail, ok = self._tune_away(conn, meta, match, rules, target_pkgs)
            if not ok:
                level = "error"
        else:
            res = execute_action(
                conn,
                action=action,
                target_pkg=meta.package,
                key_sequence=match.key_sequence,
            )
            detail = res.get("detail", "")

        state.last_action_ts = time.time()
        state.last_action_name = action
        state.last_matched_rule = match.rule.name if match.rule else "Rule Match"
        state.last_violation_detail = f"Blocked: {blocked}"
        state.state = DeviceState.COOLDOWN
        state.status_detail = f"Enforced {action}: {state.last_violation_detail}"

        self.db.add_event(
            device_id=device["id"],
            kind="guard",
            level=level,
            message=f"Channel Guard [{action}]: {state.last_violation_detail}",
            detail=f"Rule: {state.last_matched_rule} | App: {meta.package} | Action Detail: {detail}",
        )

        if self.notifier:
            self.notifier.notify(
                title=f"Warden: Blocked Channel on {device.get('name')}",
                message=f"Matched '{state.last_matched_rule}' ({match.matched_text}). Enforced: {action}. {detail}",
                tags=["tv", "warning"],
            )

    def _tune_away(self, conn, meta: MediaMetadata, match: RuleMatch,
                   rules: list[ChannelRule], target_pkgs: set[str]) -> tuple[str, str, bool]:
        """Try each tune link in order; go Home if none lands on an allowed channel.

        Returns (action_name, detail, succeeded).
        """
        outcomes = []
        current = meta
        for url in match.tune_urls:
            res = execute_action(conn, action="tune", target_pkg=meta.package, tune_url=url)
            if res["status"] != "executed":
                outcomes.append(f"{url}: {res['detail']}")
                continue
            landed = self._await_channel(conn, target_pkgs, previous=current)
            if landed is None:
                outcomes.append(f"{url}: no channel change (dead link?)")
            elif evaluate_rules(rules, landed, active_pkg=landed.package):
                outcomes.append(f"{url}: landed on blocked {landed.channel}")
                current = landed
            else:
                tried = f" after {len(outcomes)} failed link(s): {'; '.join(outcomes)}" if outcomes else ""
                return "tune", f"Opened {url} | landed on {landed.channel}{tried}", True

        if not match.tune_urls:
            outcomes.append("rule has no tune links")
        fallback = execute_action(conn, action="home")
        return (
            "tune→home",
            f"{'; '.join(outcomes)} | fallback: {fallback.get('detail')} — update the rule's tune links",
            False,
        )

    def _await_channel(self, conn, target_pkgs: set[str],
                       previous: MediaMetadata) -> MediaMetadata | None:
        """Wait out a channel switch; return the newly playing channel, or None if the link
        was ignored or nothing played before the timeout.

        The old channel keeps reporting for ~1s after a good link, so it only counts as
        "ignored" if nothing has changed by TUNE_REJECT_S.
        """
        start = time.monotonic()
        changed = False
        while (elapsed := time.monotonic() - start) < TUNE_VERIFY_TIMEOUT_S:
            if not changed and elapsed >= TUNE_REJECT_S:
                return None
            time.sleep(TUNE_VERIFY_STEP_S)
            meta = self._read_media(conn, target_pkgs)
            if (meta.channel, meta.title) == (previous.channel, previous.title) and meta.is_enforceable:
                continue
            changed = True
            if meta.is_enforceable:
                return meta
        return None

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
