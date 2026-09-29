"""Device power & lifecycle state management for smart TVs (TCL, Chromecast, Bravia, Shield)."""

import time
from dataclasses import dataclass, field
from enum import Enum

from .parser import MediaMetadata


class DeviceState(str, Enum):
    OFFLINE = "offline"         # Host down / connection refused (TV off at wall or deep sleep)
    STANDBY = "standby"         # TV reachable, but display screen is off (inspector only)
    IDLE = "idle"               # No target app playing a live channel
    MONITORING = "monitoring"   # Target app is foreground with media session
    COOLDOWN = "cooldown"       # Action was recently triggered, in grace period


@dataclass
class GuardState:
    device_id: int
    state: DeviceState = DeviceState.OFFLINE
    current_package: str = ""
    current_media: MediaMetadata = field(default_factory=MediaMetadata)
    last_poll_ts: float = 0.0
    last_action_ts: float = 0.0
    last_action_name: str = ""
    last_matched_rule: str = ""
    last_violation_detail: str = ""
    consecutive_errors: int = 0
    snooze_until_ts: float = 0.0
    status_detail: str = ""

    @property
    def is_snoozed(self) -> bool:
        return time.time() < self.snooze_until_ts

    @property
    def snooze_remaining_s(self) -> int:
        if not self.is_snoozed:
            return 0
        return max(0, int(self.snooze_until_ts - time.time()))

    def snooze(self, duration_s: int = 1800) -> None:
        """Snooze monitoring for a duration in seconds (default 30 mins)."""
        self.snooze_until_ts = time.time() + duration_s

    def unsnooze(self) -> None:
        """Clear active snooze."""
        self.snooze_until_ts = 0.0

    def get_poll_interval(self, base_interval: float = 30.0) -> float:
        """Polling interval: the configured base, backed off while the TV is unreachable.

        Each poll is a single ~2 KB `dumpsys media_session`, so there is no faster idle
        cadence to fall back to — the base interval bounds how long a blocked channel can play.
        """
        base = max(0.5, base_interval)
        if self.is_snoozed:
            return max(base, 15.0)
        if self.state == DeviceState.OFFLINE:
            return max(base, 20.0)
        return base
