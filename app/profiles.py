"""Device profile loading & validation.

A profile is a YAML file describing a class of device: how to recognise it on
the network, how the user enables remote access on it (shown in the UI), and
the list of audit/enforce actions Warden manages on it.
"""

import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

GENERIC_PROFILE_ID = "generic-android-tv"

# action type -> required fields
ACTION_TYPES = {
    "package_disable": ("package",),
    "setting": ("namespace", "key", "value"),
    "shell": ("check_cmd", "enforce_cmd"),
}

DEFAULT_PORTS = {"adb": 5555, "ssh": 22}


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    connector: str
    default_port: int
    description: str
    howto: str
    match: dict
    vars: list[dict]
    actions: list[dict]
    source: str

    def action(self, action_id: str) -> dict | None:
        return next((a for a in self.actions if a["id"] == action_id), None)

    def dump(self) -> dict:
        return asdict(self)


def _validate_action(profile_id: str, action) -> None:
    if not isinstance(action, dict) or not action.get("id"):
        raise ProfileError(f"{profile_id}: every action needs an 'id'")
    action_type = action.get("type")
    if action_type not in ACTION_TYPES:
        raise ProfileError(
            f"{profile_id}/{action['id']}: unknown type {action_type!r} "
            f"(expected one of {sorted(ACTION_TYPES)})"
        )
    missing = [f for f in ACTION_TYPES[action_type] if not action.get(f)]
    if missing:
        raise ProfileError(f"{profile_id}/{action['id']}: missing field(s) {missing}")


def load_profile_file(path: Path) -> Profile:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("name"):
        raise ProfileError(f"{path.name}: profile needs at least 'id' and 'name'")
    actions = raw.get("actions") or []
    if not isinstance(actions, list):
        raise ProfileError(f"{raw['id']}: 'actions' must be a list")
    seen: set[str] = set()
    for action in actions:
        _validate_action(raw["id"], action)
        if action["id"] in seen:
            raise ProfileError(f"{raw['id']}: duplicate action id {action['id']!r}")
        seen.add(action["id"])
    connector = raw.get("connector", "adb")
    return Profile(
        id=str(raw["id"]),
        name=str(raw["name"]),
        connector=connector,
        default_port=int(raw.get("default_port", DEFAULT_PORTS.get(connector, 22))),
        description=str(raw.get("description", "")).strip(),
        howto=str(raw.get("howto", "")).strip(),
        match=raw.get("match") or {},
        vars=raw.get("vars") or [],
        actions=actions,
        source=path.name,
    )


def load_profiles(dirs) -> dict[str, Profile]:
    """Load all profiles; later directories override earlier ones by id."""
    out: dict[str, Profile] = {}
    for directory in dirs:
        directory = Path(directory)
        if not directory.is_dir():
            log.warning("Profile directory %s does not exist, skipping", directory)
            continue
        for path in sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))):
            try:
                profile = load_profile_file(path)
            except (ProfileError, yaml.YAMLError) as exc:
                log.error("Skipping invalid profile %s: %s", path, exc)
                continue
            if profile.id in out:
                log.info("Profile %s overridden by %s", profile.id, path)
            out[profile.id] = profile
    return out


def suggest_profile(profiles: dict[str, Profile], text: str = "",
                    mdns_types: tuple = ()) -> str | None:
    """Best-effort profile match from a device's advertised name/model and mDNS types.

    Keyword hits weigh more than mDNS service types so e.g. a Sony Bravia
    (which also advertises _googlecast._tcp) lands on the Sony profile.
    """
    text = (text or "").lower()
    best, best_score = None, 0
    for profile in profiles.values():
        score = 0
        for keyword in profile.match.get("keywords", []):
            if keyword.lower() in text:
                score += 2
        for mdns_type in profile.match.get("mdns_types", []):
            if mdns_type in mdns_types:
                score += 1
        if score > best_score:
            best, best_score = profile.id, score
    return best
