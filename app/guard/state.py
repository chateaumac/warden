"""Device power & lifecycle state management for smart TVs (TCL, Chromecast, Bravia, Shield)."""

import time
from dataclasses import dataclass, field
from enum import Enum

from .parser import MediaMetadata


class DeviceState(str, Enum):
    OFFLINE = "offline"         # Host down / connection refused (TV off at wall or deep sleep)
    STANDBY = "standby"         # TV reachable, but display screen is off
    IDLE = "idle"               # TV screen is on, but target app is in background
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

    def get_poll_interval(self, base_interval: float = 1.0) -> float:
        """Dynamic polling interval based on device power & activity state."""
        if self.is_snoozed:
            return 15.0
        if self.state == DeviceState.OFFLINE:
            # Back off aggressively if device is offline/asleep
            return 20.0
        if self.state == DeviceState.STANDBY:
            return 10.0
        if self.state == DeviceState.IDLE:
            return 2.5
        if self.state == DeviceState.COOLDOWN:
            return 3.0
        # Active playback in foreground
        return max(0.5, base_interval)
