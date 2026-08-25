#!/usr/bin/env python3
"""Run one adb shell command on a device using Warden's fleet key.

Ops/debugging helper — e.g. simulating the drift Warden exists to fix:

    docker exec warden python scripts/adbsh.py 10.10.40.235 \
        "pm enable com.google.android.tvlauncher.ads"

Uses the same RSA key as the server (WARDEN_DATA_DIR/keys), so it works on any
device that has already authorized Warden.
"""

import os
import sys
from pathlib import Path

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    host, cmd = sys.argv[1], " ".join(sys.argv[2:])
    port = 5555
    if ":" in host:
        host, port_s = host.split(":", 1)
        port = int(port_s)

    keys_dir = Path(os.environ.get("WARDEN_DATA_DIR", "/data")) / "keys"
    priv = keys_dir / "adb_key"
    if not priv.exists():
        print(f"no key at {priv} — has the server started at least once?", file=sys.stderr)
        return 1
    signer = PythonRSASigner((keys_dir / "adb_key.pub").read_text(), priv.read_text())

    device = AdbDeviceTcp(host, port, default_transport_timeout_s=10.0)
    device.connect(rsa_keys=[signer], auth_timeout_s=10.0)
    out = device.shell(cmd, read_timeout_s=30.0, timeout_s=45.0) or ""
    print(out.rstrip("\n"))
    device.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
