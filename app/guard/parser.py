"""Zero-wear streaming parser for Android TV media sessions and window hierarchy."""

import re
from dataclasses import dataclass

# Regex patterns for dumpsys media_session
PACKAGE_RE = re.compile(r"package=([a-zA-Z0-9_.]+)", re.IGNORECASE)
OWNER_PKG_RE = re.compile(r"ownerPkg=([a-zA-Z0-9_.]+)", re.IGNORECASE)
PLAYBACK_STATE_RE = re.compile(r"state=(\d+)", re.IGNORECASE)
TITLE_RE = re.compile(r"(?:title|description|android\.media\.metadata\.TITLE)=([^,\n\r]+)", re.IGNORECASE)
SUBTITLE_RE = re.compile(r"(?:subtitle|artist|android\.media\.metadata\.ARTIST)=([^,\n\r]+)", re.IGNORECASE)
DESC_LINE_RE = re.compile(r"description=([^\n\r]+)", re.IGNORECASE)

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


def parse_media_session(session_dump: str, foreground_pkg: str = "") -> MediaMetadata:
    """Parse media session metadata without disk writes or slow UI dumps."""
    if not session_dump:
        return MediaMetadata(package=foreground_pkg, raw_session="")

    meta = MediaMetadata(package=foreground_pkg, raw_session=session_dump)

    # Extract package if not provided
    if not meta.package:
        pkg_m = PACKAGE_RE.search(session_dump) or OWNER_PKG_RE.search(session_dump)
        if pkg_m:
            meta.package = pkg_m.group(1).strip()

    # Extract playback state
    state_m = PLAYBACK_STATE_RE.search(session_dump)
    if state_m:
        try:
            state_code = int(state_m.group(1))
            meta.playback_state = PLAYBACK_STATE_MAP.get(state_code, f"code_{state_code}")
            meta.is_playing = state_code == 3  # STATE_PLAYING
        except (ValueError, TypeError):
            pass

    # Extract description lines
    desc_lines = []
    for line in session_dump.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        if "description=" in line_clean or "metadata:" in line_clean or "title=" in line_clean:
            desc_lines.append(line_clean)

    meta.description = "\n".join(desc_lines)

    # Extract title
    title_m = TITLE_RE.search(session_dump)
    if title_m:
        val = title_m.group(1).strip().strip('"').strip("'")
        if val and val.lower() != "null":
            meta.title = val

    # Extract subtitle / artist / show
    sub_m = SUBTITLE_RE.search(session_dump)
    if sub_m:
        val = sub_m.group(1).strip().strip('"').strip("'")
        if val and val.lower() != "null" and val != meta.title:
            meta.subtitle = val

    return meta
