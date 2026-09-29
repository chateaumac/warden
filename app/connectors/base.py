"""Connector abstraction: a transport that can open a session to a device and
run shell commands on it. New device families plug in here."""

from abc import ABC, abstractmethod


class ConnectorError(Exception):
    """Generic connector failure."""


class Unreachable(ConnectorError):
    """Could not reach the device on the network."""


class Unauthorized(ConnectorError):
    """The device refused, or has not yet granted, access."""


class BaseConnector(ABC):
    # action types this transport can audit/enforce (see app.profiles.ACTION_TYPES)
    supports: frozenset = frozenset({"shell"})

    def __init__(self, host: str, port: int, config: dict, settings):
        self.host = host
        self.port = port
        self.config = config or {}
        self.settings = settings

    @abstractmethod
    def connect(self, auth_timeout_s: float | None = None) -> dict:
        """Open the session. Returns identity metadata (manufacturer/model/os).

        Raises Unreachable / Unauthorized / ConnectorError.
        """

    @abstractmethod
    def shell(self, cmd: str) -> str:
        """Run a shell command on the device, returning its combined output."""

    def close(self) -> None:
        pass


def make_connector(device: dict, settings) -> BaseConnector:
    from .adb import AdbConnector
    from .ssh import SshConnector

    registry = {"adb": AdbConnector, "ssh": SshConnector}
    cls = registry.get(device["connector"])
    if cls is None:
        raise ConnectorError(f"Unknown connector {device['connector']!r}")
    return cls(device["host"], device["port"], device.get("config") or {}, settings)
