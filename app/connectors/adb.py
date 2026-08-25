"""ADB-over-TCP connector built on adb-shell (pure Python, no Android SDK).

One RSA keypair is generated on first start and reused for every device,
exactly like the stock adb server — so the user accepts the on-screen
authorization dialog once per device.
"""

import logging
import threading
from pathlib import Path

from adb_shell import exceptions as adb_exc
from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

from .base import BaseConnector, ConnectorError, Unauthorized, Unreachable

log = logging.getLogger(__name__)

AUTH_HINT = (
    "device has not authorized Warden's ADB key — look for the "
    "'Allow USB debugging?' dialog on the device screen and tick "
    "'Always allow from this computer'"
)

IDENT_CMD = (
    "getprop ro.product.manufacturer; "
    "getprop ro.product.model; "
    "getprop ro.build.version.release"
)

_signer: PythonRSASigner | None = None
_signer_lock = threading.Lock()


def get_signer(keys_dir: Path) -> PythonRSASigner:
    global _signer
    with _signer_lock:
        if _signer is None:
            priv_path = Path(keys_dir) / "adb_key"
            if not priv_path.exists():
                keygen(str(priv_path))  # writes adb_key + adb_key.pub
                log.info("Generated new ADB RSA keypair at %s", priv_path)
            pub = (Path(keys_dir) / "adb_key.pub").read_text()
            _signer = PythonRSASigner(pub, priv_path.read_text())
        return _signer


class AdbConnector(BaseConnector):
    supports = frozenset({"shell", "package_disable", "setting"})

    _dev: AdbDeviceTcp | None = None

    def connect(self, auth_timeout_s: float | None = None) -> dict:
        timeout = auth_timeout_s or self.settings.adb_auth_timeout_s
        self._dev = AdbDeviceTcp(self.host, self.port, default_transport_timeout_s=10.0)
        try:
            self._dev.connect(rsa_keys=[get_signer(self.settings.keys_dir)],
                              auth_timeout_s=timeout)
        except adb_exc.DeviceAuthError as exc:
            raise Unauthorized(AUTH_HINT) from exc
        except adb_exc.AdbTimeoutError as exc:
            # TCP connected but the ADB handshake never finished — almost always
            # an authorization dialog waiting on screen
            raise Unauthorized(f"timed out waiting for authorization — {AUTH_HINT}") from exc
        except (TimeoutError, adb_exc.TcpTimeoutException, ConnectionRefusedError, OSError) as exc:
            raise Unreachable(
                f"cannot reach {self.host}:{self.port} ({exc or exc.__class__.__name__})"
            ) from exc

        try:
            out = self.shell(IDENT_CMD)
        except ConnectorError:
            return {}
        manufacturer, model, os_version = (out.splitlines() + ["", "", ""])[:3]
        return {
            "manufacturer": manufacturer.strip(),
            "model": model.strip(),
            "os": f"Android {os_version.strip()}".strip(),
        }

    def shell(self, cmd: str) -> str:
        if self._dev is None:
            raise ConnectorError("not connected")
        try:
            return self._dev.shell(cmd, read_timeout_s=30.0, timeout_s=45.0) or ""
        except (adb_exc.AdbTimeoutError, adb_exc.TcpTimeoutException,
                adb_exc.AdbConnectionError, OSError) as exc:
            raise ConnectorError(f"adb shell failed: {exc or exc.__class__.__name__}") from exc

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            self._dev = None
