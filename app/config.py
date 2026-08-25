"""Runtime configuration, loaded from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SERVICE_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"

VERSION = "0.1.0"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    profile_dirs: tuple[Path, ...]
    audit_interval_s: int
    audit_startup_delay_s: int
    adb_auth_timeout_s: float
    discovery_subnet: str
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
        (data_dir / "keys").mkdir(parents=True, exist_ok=True)

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
        )
