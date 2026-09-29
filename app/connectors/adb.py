"""ADB-over-TCP connector built on adb-shell (pure Python, no Android SDK).

One RSA keypair is reused for every device, exactly like the stock adb server — so the
user accepts the on-screen authorization dialog once per device. Whoever holds that key
can run shell commands on every TV that approved it, so it is treated as a secret:
generated 0600 in a 0700 directory, or supplied read-only via WARDEN_ADB_KEY_FILE, and
never loaded if other users can read it.
"""

import logging
import os
import stat
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


class KeystoreError(RuntimeError):
    pass


def _check_private(path: Path) -> None:
    """Refuse a key file or directory that group/other can access; tighten it if we own it."""
    mode = stat.S_IMODE(path.stat().st_mode)
    if not mode & 0o077:
        return
    try:
        os.chmod(path, mode & 0o700)
        log.warning("Tightened permissions on %s (was %o)", path, mode)
    except PermissionError:
        raise KeystoreError(
            f"{path} is accessible to other users (mode {mode:o}) and cannot be fixed; "
            "it must be 0600 (file) / 0700 (directory)"
        ) from None


def get_signer(settings) -> PythonRSASigner:
    global _signer
    with _signer_lock:
        if _signer is None:
            if settings.adb_key_file:
                priv_path = Path(settings.adb_key_file)
                if not priv_path.exists():
                    raise KeystoreError(f"WARDEN_ADB_KEY_FILE {priv_path} does not exist")
            else:
                keys_dir = Path(settings.keys_dir)
                keys_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                _check_private(keys_dir)
                priv_path = keys_dir / "adb_key"
                if not priv_path.exists():
                    if not settings.adb_keygen:
                        raise KeystoreError(
                            f"no ADB key at {priv_path} and WARDEN_ADB_KEYGEN=false; restore the "
                            "key TVs already trust instead of creating a new identity"
                        )
                    old_umask = os.umask(0o077)
                    try:
                        keygen(str(priv_path))  # writes adb_key + adb_key.pub
                    finally:
                        os.umask(old_umask)
                    log.warning("Generated new ADB RSA keypair at %s — each TV will ask to "
                                "authorize it", priv_path)
            _check_private(priv_path)
            pub_path = priv_path.with_name(priv_path.name + ".pub")
            _signer = PythonRSASigner(pub_path.read_text(), priv_path.read_text())
        return _signer


class AdbConnector(BaseConnector):
    supports = frozenset({"shell", "package_disable", "setting"})

    _dev: AdbDeviceTcp | None = None

    def connect(self, auth_timeout_s: float | None = None) -> dict:
        timeout = auth_timeout_s or self.settings.adb_auth_timeout_s
        self._dev = AdbDeviceTcp(self.host, self.port, default_transport_timeout_s=10.0)
        try:
            self._dev.connect(rsa_keys=[get_signer(self.settings)],
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
