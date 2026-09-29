"""Pydantic request bodies. Responses are plain dicts straight from the DB/engine."""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .guard.actions import normalize_tune_url


class DeviceCreate(BaseModel):
    host: str = Field(min_length=1)
    name: str | None = None
    location: str = ""
    port: int | None = Field(default=None, ge=1, le=65535)
    connector: Literal["adb", "ssh"] = "adb"
    profile_id: str | None = None
    mode: Literal["monitor", "enforce"] = "monitor"
    vars: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class DeviceUpdate(BaseModel):
    name: str | None = None
    location: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    profile_id: str | None = None
    mode: Literal["monitor", "enforce"] | None = None
    enabled: bool | None = None
    vars: dict[str, str] | None = None
    config: dict[str, Any] | None = None
    action_overrides: dict[str, bool] | None = None


class ConnectRequest(BaseModel):
    timeout_s: float = Field(default=30.0, ge=1, le=120)


class ScanRequest(BaseModel):
    mdns: bool = True
    subnet: str | None = None
    duration_s: float = Field(default=6.0, ge=2, le=30)


TUNE_NEEDS_URL = "action 'tune' requires at least one tune_urls entry (a tv.youtube.com/watch link)"
GuardAction = Literal["auto_skip", "tune", "force_stop", "back", "home", "mute"]


def _tune_urls(value: list[str] | None) -> list[str] | None:
    """Canonicalize tv.youtube.com/watch links or bare ids, dropping blanks."""
    if value is None:
        return None
    return [normalize_tune_url(v) for v in value if v.strip()]


class ChannelRuleCreate(BaseModel):
    name: str = Field(min_length=1)
    enabled: bool = True
    target_packages: list[str] = Field(default_factory=lambda: ["com.google.android.youtube.tvunplugged"])
    channels: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    action: GuardAction = "auto_skip"
    key_sequence: list[str] = Field(default_factory=lambda: ["KEYCODE_CHANNEL_UP"])
    tune_urls: list[str] = Field(default_factory=list)
    description: str = ""

    _check_tune_urls = field_validator("tune_urls")(_tune_urls)

    @model_validator(mode="after")
    def _tune_needs_url(self) -> "ChannelRuleCreate":
        if self.action == "tune" and not self.tune_urls:
            raise ValueError(TUNE_NEEDS_URL)
        return self


class ChannelRuleUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    target_packages: list[str] | None = None
    channels: list[str] | None = None
    patterns: list[str] | None = None
    action: GuardAction | None = None
    key_sequence: list[str] | None = None
    tune_urls: list[str] | None = None
    description: str | None = None

    _check_tune_urls = field_validator("tune_urls")(_tune_urls)


class GuardSettingsUpdate(BaseModel):
    enabled: bool | None = None
    default_action: Literal["auto_skip", "force_stop", "back", "home", "mute"] | None = None
    poll_interval_s: float | None = Field(default=None, ge=0.5, le=60.0)
    cooldown_s: float | None = Field(default=None, ge=1.0, le=300.0)


class SnoozeRequest(BaseModel):
    duration_s: int = Field(default=1800, ge=60, le=86400)


class TestRuleRequest(BaseModel):
    pattern: str
    sample_text: str
