"""SSH connector (paramiko) for non-Android devices that take shell tweaks
(LG webOS root, Linux boxes, etc.). Only generic 'shell' actions are supported.

Credentials live in the device's connector config:
    {"username": "root", "password": "..."} or {"username": "root", "key_path": "/data/ssh/id_ed25519"}
"""

import socket

import paramiko

from .base import BaseConnector, ConnectorError, Unauthorized, Unreachable


class SshConnector(BaseConnector):
    supports = frozenset({"shell"})

    _client: paramiko.SSHClient | None = None

    def connect(self, auth_timeout_s: float | None = None) -> dict:
        client = paramiko.SSHClient()
        # Homelab trade-off: trust-on-first-use instead of curated known_hosts.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.config.get("username", "root"),
            "timeout": auth_timeout_s or 10,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.config.get("password"):
            kwargs["password"] = self.config["password"]
        if self.config.get("key_path"):
            kwargs["key_filename"] = self.config["key_path"]
        try:
            client.connect(**kwargs)
        except paramiko.AuthenticationException as exc:
            raise Unauthorized(f"SSH authentication failed: {exc}") from exc
        except (socket.timeout, OSError, paramiko.SSHException) as exc:
            raise Unreachable(f"cannot reach {self.host}:{self.port} ({exc})") from exc
        self._client = client
        try:
            return {"model": self.shell("uname -sr").strip()}
        except ConnectorError:
            return {}

    def shell(self, cmd: str) -> str:
        if self._client is None:
            raise ConnectorError("not connected")
        try:
            _stdin, stdout, stderr = self._client.exec_command(cmd, timeout=30)
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
        except (paramiko.SSHException, socket.timeout, OSError) as exc:
            raise ConnectorError(f"ssh exec failed: {exc}") from exc
        if err:
            out = out + ("\n" if out else "") + err
        return out

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
