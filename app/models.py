"""Pydantic request bodies. Responses are plain dicts straight from the DB/engine."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class DeviceCreate(BaseModel):
    host: str = Field(min_length=1)
    name: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    connector: Literal["adb", "ssh"] = "adb"
    profile_id: str | None = None
    mode: Literal["monitor", "enforce"] = "monitor"
    vars: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class DeviceUpdate(BaseModel):
    name: str | None = None
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
