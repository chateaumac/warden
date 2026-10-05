"""Runtime configuration, loaded from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SERVICE_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"

VERSION = "0.3.3"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    profile_dirs: tuple[Path, ...]
    audit_interval_s: int
    audit_startup_delay_s: int
    adb_auth_timeout_s: float
    discovery_subnet: str
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    notify_url: str = ""
    adb_key_file: Path | None = None   # externally provisioned private key (+ "<file>.pub")
    adb_keygen: bool = True            # False: never mint a new ADB identity, fail instead
    version: str = VERSION

    @property
    def db_path(self) -> Path:
        return self.data_dir / "warden.db"

    @property
    def keys_dir(self) -> Path:
        return self.data_dir / "keys"

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(os.environ.get("WARDEN_DATA_DIR", str(SERVICE_DIR / "data")))
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "keys").mkdir(mode=0o700, parents=True, exist_ok=True)

        profile_dirs = [Path(os.environ.get("WARDEN_PROFILE_DIR", str(SERVICE_DIR / "profiles")))]
        user_profiles = data_dir / "profiles"  # drop-in dir for custom/override profiles
        if user_profiles.is_dir():
            profile_dirs.append(user_profiles)

        return cls(
            data_dir=data_dir,
            profile_dirs=tuple(profile_dirs),
            audit_interval_s=int(os.environ.get("WARDEN_AUDIT_INTERVAL", "900")),
            audit_startup_delay_s=int(os.environ.get("WARDEN_AUDIT_STARTUP_DELAY", "15")),
            adb_auth_timeout_s=float(os.environ.get("WARDEN_ADB_AUTH_TIMEOUT", "8")),
            discovery_subnet=os.environ.get("WARDEN_DISCOVERY_SUBNET", ""),
            mqtt_host=os.environ.get("MQTT_HOST", ""),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            mqtt_user=os.environ.get("MQTT_USER", ""),
            mqtt_password=os.environ.get("MQTT_PASSWORD", ""),
            notify_url=os.environ.get("NOTIFY_URL", os.environ.get("WARDEN_NOTIFY_URL", "")),
            adb_key_file=Path(os.environ["WARDEN_ADB_KEY_FILE"]) if os.environ.get("WARDEN_ADB_KEY_FILE") else None,
            adb_keygen=os.environ.get("WARDEN_ADB_KEYGEN", "true").strip().lower() not in ("0", "false", "no"),
        )
