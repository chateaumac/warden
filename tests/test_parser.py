"""Unit tests for zero-wear media session and window focus parser."""

from app.guard.parser import parse_media_session, parse_window_focus

YTTV_SAMPLE_SESSION = """
Sessions Stack - user 0 - 1 sessions
  * com.google.android.youtube.tvunplugged/androidx.media3.session.MediaSessionService (userId=0)
    ownerPid=1234, ownerUid=10123, userId=0
    package=com.google.android.youtube.tvunplugged
    tag=androidx.media3.session.MediaSessionService
    PlaybackState {state=3, position=42000, buffered position=45000, speed=1.0, updated=1000, actions=512, custom actions=[], active item id=-1, error=null}
    metadata: size=4, description=Fox News Channel HD, Live Broadcast
    title="Fox News Channel HD", subtitle="The Five - Live"
"""

PAUSED_SESSION = """
  * com.google.android.youtube.tvunplugged/MediaSessionService
    package=com.google.android.youtube.tvunplugged
    PlaybackState {state=2, position=10000}
    title="ESPN HD", subtitle="SportsCenter"
"""

WINDOW_DUMP = """
WINDOW MANAGER FOCUS & FOCUSABLE WINDOWS (dumpsys window windows)
  mCurrentFocus=Window{1a2b3c4 u0 com.google.android.youtube.tvunplugged/com.google.android.apps.youtube.tvunplugged.activity.MainActivity}
  mFocusedApp=ActivityRecord{5d6e7f8 u0 com.google.android.youtube.tvunplugged/.MainActivity t123}
"""


def test_parse_window_focus():
    pkg = parse_window_focus(WINDOW_DUMP)
    assert pkg == "com.google.android.youtube.tvunplugged"


def test_parse_media_session_playing():
    meta = parse_media_session(YTTV_SAMPLE_SESSION, foreground_pkg="com.google.android.youtube.tvunplugged")
    assert meta.package == "com.google.android.youtube.tvunplugged"
    assert meta.is_playing is True
    assert meta.playback_state == "playing"
    assert meta.title == "Fox News Channel HD"
    assert meta.subtitle == "The Five - Live"
    assert "Fox News Channel HD" in meta.full_text


def test_parse_media_session_paused():
    meta = parse_media_session(PAUSED_SESSION, foreground_pkg="com.google.android.youtube.tvunplugged")
    assert meta.is_playing is False
    assert meta.playback_state == "paused"
    assert meta.title == "ESPN HD"
    assert meta.subtitle == "SportsCenter"


def test_parse_media_session_empty():
    meta = parse_media_session("", foreground_pkg="")
    assert meta.is_playing is False
    assert meta.title == ""
    assert meta.full_text == ""
