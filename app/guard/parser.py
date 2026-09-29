"""Zero-wear streaming parser for Android TV media sessions and window hierarchy."""

import re
from dataclasses import dataclass

# Session header inside "Sessions Stack", e.g.
#   "    starboard com.google.android.youtube.tvunplugged/starboard (userId=0)"
SESSION_HEADER_RE = re.compile(r"^\s+\S+ ([a-zA-Z0-9_.]+)/\S+ \(userId=\d+\)\s*$", re.MULTILINE)
PLAYBACK_STATE_RE = re.compile(r"PlaybackState \{state=(\d+)")
ACTIVE_RE = re.compile(r"^\s*active=(true|false)\s*$", re.MULTILINE)
# MediaDescription.toString() is "<title>, <subtitle>, <description>" with "null" for unset
# fields. YouTube TV puts the program in title and the channel name in subtitle.
METADATA_RE = re.compile(r"metadata: size=\d+, description=([^\n\r]*)")

# Regex patterns for dumpsys window / activity top
FOCUS_WINDOW_RE = re.compile(r"(?:mCurrentFocus|mFocusedApp|topResumedActivity|mFocusedWindow)[^\n\r]*?([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.]+", re.IGNORECASE)
PKG_SLASH_RE = re.compile(r"([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.]+")

# Android PlaybackState state codes
PLAYBACK_STATE_MAP = {
    0: "none",
    1: "stopped",
    2: "paused",
    3: "playing",
    4: "fast_forwarding",
    5: "rewinding",
    6: "buffering",
    7: "error",
    8: "connecting",
    9: "skipping_to_previous",
    10: "skipping_to_next",
    11: "skipping_to_queue_item",
}

# States where content is (about to be) on screen and worth enforcing against
ENFORCEABLE_STATES = {3, 6, 8}


@dataclass
class MediaMetadata:
    package: str = ""
    is_playing: bool = False
    playback_state: str = "unknown"
    title: str = ""
    subtitle: str = ""
    description: str = ""
    raw_session: str = ""
    raw_window: str = ""

    @property
    def channel(self) -> str:
        """Live channel name; YouTube TV reports it as the media subtitle."""
        return self.subtitle

    @property
    def is_enforceable(self) -> bool:
        """True when a channel is identified and playing, buffering or connecting.

        While YouTube TV switches channels it briefly reports no session, then
        "null, null, null" — those transitional readings are never enforceable.
        """
        return bool(self.channel) and self.playback_state in {
            PLAYBACK_STATE_MAP[s] for s in ENFORCEABLE_STATES
        }

    @property
    def full_text(self) -> str:
        """Combined searchable text for rule matching."""
        parts = [self.title, self.subtitle, self.description]
        return " ".join(p.strip() for p in parts if p and p.strip())


def parse_window_focus(window_dump: str) -> str:
    """Extract foreground package name from dumpsys window or dumpsys activity output."""
    if not window_dump:
        return ""

    # Check focused window patterns first
    m = FOCUS_WINDOW_RE.search(window_dump)
    if m:
        return m.group(1).strip()

    # Fallback to general package/activity pattern
    m2 = PKG_SLASH_RE.search(window_dump)
    if m2:
        return m2.group(1).strip()

    return ""


def _session_blocks(session_dump: str) -> list[tuple[str, str]]:
    """Split a dumpsys media_session dump into (package, block_text) per session."""
    headers = list(SESSION_HEADER_RE.finditer(session_dump))
    blocks = []
    for i, h in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(session_dump)
        blocks.append((h.group(1), session_dump[h.start():end]))
    return blocks


def _clean(value: str) -> str:
    value = value.strip()
    return "" if value == "null" else value


def parse_media_session(session_dump: str, target_pkg: str = "") -> MediaMetadata:
    """Parse the media session of `target_pkg` (or the first active session) from a dump."""
    meta = MediaMetadata(package=target_pkg, raw_session=session_dump or "")
    blocks = _session_blocks(session_dump or "")
    if target_pkg:
        blocks = [b for b in blocks if target_pkg in b[0]]
    if not blocks:
        return meta

    # Prefer the active session when an app registers several
    pkg, block = next(
        (b for b in blocks if (m := ACTIVE_RE.search(b[1])) and m.group(1) == "true"),
        blocks[0],
    )
    meta.package = pkg

    state_m = PLAYBACK_STATE_RE.search(block)
    if state_m:
        code = int(state_m.group(1))
        meta.playback_state = PLAYBACK_STATE_MAP.get(code, f"code_{code}")
        meta.is_playing = code == 3

    desc_m = METADATA_RE.search(block)
    if desc_m:
        # Split from the right: program titles may contain ", ", channel names don't
        fields = desc_m.group(1).rsplit(", ", 2)
        fields += [""] * (3 - len(fields))
        meta.title, meta.subtitle, meta.description = (_clean(f) for f in fields)

    return meta
