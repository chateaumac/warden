"""ADB connect failures must distinguish "TV waiting for approval" from "TV unreachable"."""

from types import SimpleNamespace

import pytest
from adb_shell import exceptions as adb_exc

from app.connectors import adb
from app.connectors.base import Unauthorized, Unreachable


@pytest.fixture
def connector(monkeypatch, tmp_path):
    monkeypatch.setattr(adb, "get_signer", lambda settings: object())
    settings = SimpleNamespace(adb_auth_timeout_s=3.0, keys_dir=tmp_path)
    return adb.AdbConnector("192.0.2.10", 5555, {}, settings)


def failing_connect(exc):
    def connect(self, rsa_keys, auth_timeout_s):
        raise exc
    return connect


@pytest.mark.parametrize("exc", [
    # What adb-shell raises while the TV shows "Allow USB debugging?" (TCP is up, no reply)
    adb_exc.TcpTimeoutException("Reading from 192.0.2.10:5555 timed out (10.0 seconds)"),
    adb_exc.AdbTimeoutError("auth timed out"),
    adb_exc.DeviceAuthError("rejected"),
])
def test_handshake_timeouts_mean_waiting_for_approval(connector, monkeypatch, exc):
    monkeypatch.setattr(adb.AdbDeviceTcp, "connect", failing_connect(exc))
    with pytest.raises(Unauthorized, match="Allow USB debugging"):
        connector.connect()


@pytest.mark.parametrize("exc", [
    TimeoutError("timed out"),          # socket.create_connection never got a SYN-ACK
    ConnectionRefusedError(),           # ADB over network disabled
    OSError("No route to host"),        # TV powered off
])
def test_connect_failures_mean_unreachable(connector, monkeypatch, exc):
    monkeypatch.setattr(adb.AdbDeviceTcp, "connect", failing_connect(exc))
    with pytest.raises(Unreachable):
        connector.connect()
