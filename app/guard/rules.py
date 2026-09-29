"""Rule definitions and regex pattern evaluation for content governance."""

import re
from dataclasses import dataclass, field
from typing import Any

from .parser import MediaMetadata


@dataclass
class ChannelRule:
    id: int = 0
    name: str = ""
    enabled: bool = True
    target_packages: list[str] = field(
        default_factory=lambda: ["com.google.android.youtube.tvunplugged"]
    )
    channels: list[str] = field(default_factory=list)  # exact channel names, case-insensitive
    patterns: list[str] = field(default_factory=list)
    action: str = "auto_skip"  # auto_skip | tune | force_stop | back | home | mute
    key_sequence: list[str] = field(
        default_factory=lambda: ["KEYCODE_CHANNEL_UP"]
    )
    # tv.youtube.com/watch/<id> links tried in order by the tune action
    tune_urls: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "target_packages": self.target_packages,
            "channels": self.channels,
            "patterns": self.patterns,
            "action": self.action,
            "key_sequence": self.key_sequence,
            "tune_urls": self.tune_urls,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChannelRule":
        return cls(
            id=data.get("id", 0),
            name=data.get("name", ""),
            enabled=bool(data.get("enabled", True)),
            target_packages=list(data.get("target_packages", ["com.google.android.youtube.tvunplugged"])),
            channels=list(data.get("channels", [])),
            patterns=list(data.get("patterns", [])),
            action=data.get("action", "auto_skip"),
            key_sequence=list(data.get("key_sequence", ["KEYCODE_CHANNEL_UP"])),
            tune_urls=list(data.get("tune_urls", [])),
            description=data.get("description", ""),
        )


@dataclass
class RuleMatch:
    matched: bool
    rule: ChannelRule | None = None
    matched_pattern: str = ""
    matched_text: str = ""
    action: str = "auto_skip"
    key_sequence: list[str] = field(default_factory=list)
    tune_urls: list[str] = field(default_factory=list)


def evaluate_rules(
    rules: list[ChannelRule],
    meta: MediaMetadata,
    active_pkg: str = "",
) -> RuleMatch | None:
    """Evaluate active media metadata against all configured channel rules."""
    target_pkg = active_pkg or meta.package
    channel = meta.channel.strip().casefold()
    search_haystack = meta.full_text

    if not search_haystack:
        return None

    for rule in rules:
        if not rule.enabled:
            continue

        # Package scope check
        if rule.target_packages and "*" not in rule.target_packages:
            if target_pkg and not any(p in target_pkg for p in rule.target_packages):
                continue

        # Exact channel-name check
        if channel and any(c.strip().casefold() == channel for c in rule.channels):
            return _match(rule, pattern=f"channel:{meta.channel}", text=meta.channel)

        # Pattern check
        for pattern_str in rule.patterns:
            if not pattern_str.strip():
                continue
            try:
                m = re.search(pattern_str, search_haystack, re.IGNORECASE)
            except re.error:
                continue
            if m:
                return _match(rule, pattern=pattern_str, text=m.group(0))

    return None


def _match(rule: ChannelRule, pattern: str, text: str) -> RuleMatch:
    return RuleMatch(
        matched=True,
        rule=rule,
        matched_pattern=pattern,
        matched_text=text,
        action=rule.action,
        key_sequence=rule.key_sequence,
        tune_urls=rule.tune_urls,
    )
