"""Unit tests for the media session and window focus parser, using real TCL / YouTube TV dumps."""

from app.guard.parser import parse_media_session, parse_window_focus

YTTV = "com.google.android.youtube.tvunplugged"

# Captured from a TCL Google TV (Android 11, YouTube TV 3.83.01) during live playback
REAL_DUMP_TEMPLATE = """MEDIA SESSION SERVICE (dumpsys media_session)

5 sessions listeners.
Global priority session is null
User Records:
Record for full_user=0, profile_user=11
  Media button session is com.google.android.youtube.tvunplugged/starboard (userId=0)
  Sessions Stack - have 2 sessions:
    starboard com.google.android.youtube.tvunplugged/starboard (userId=0)
      ownerPid=1266, ownerUid=10137, userId=0
      package=com.google.android.youtube.tvunplugged
      launchIntent=null
      mediaButtonReceiver=null
      active=true
      flags=3
      rating type=0
      controllers: 4
      state=PlaybackState {{state={state}, position=46800405, buffered position=0, speed=1.0, updated=6035335948, actions=379, custom actions=[], active item id=-1, error=null}}
      audioAttrs=AudioAttributes: usage=USAGE_MEDIA content=CONTENT_TYPE_UNKNOWN flags=0x800 tags= bundle=null
      volumeType=1, controlType=2, max=0, current=0
      metadata: {metadata}
      queueTitle=null, size=0
    BluetoothMediaBrowserService com.android.bluetooth/BluetoothMediaBrowserService (userId=0)
      ownerPid=3170, ownerUid=1002, userId=0
      package=com.android.bluetooth
      launchIntent=null
      mediaButtonReceiver=null
      active=false
      flags=3
      rating type=0
      controllers: 0
      state=PlaybackState {{state=7, position=0, buffered position=0, speed=0.0, updated=6034398466, actions=0, custom actions=[], active item id=-1, error=Bluetooth audio disconnected}}
      audioAttrs=AudioAttributes: usage=USAGE_MEDIA content=CONTENT_TYPE_UNKNOWN flags=0x800 tags= bundle=null
      volumeType=1, controlType=2, max=0, current=0
      metadata: null
      queueTitle=Now Playing, size=0
Audio playback (lastly played comes first)
  uid=10137 packages=com.google.android.youtube.tvunplugged
"""

# Mid channel-switch: YouTube TV's session is gone, only Bluetooth remains
NO_YTTV_SESSION = """MEDIA SESSION SERVICE (dumpsys media_session)
  Sessions Stack - have 1 sessions:
    BluetoothMediaBrowserService com.android.bluetooth/BluetoothMediaBrowserService (userId=0)
      package=com.android.bluetooth
      active=false
      state=PlaybackState {state=7, position=0, buffered position=0, speed=0.0, updated=6034398466, actions=0, custom actions=[], active item id=-1, error=Bluetooth audio disconnected}
      metadata: null
"""


def real_dump(description: str | None, state: int = 3) -> str:
    metadata = "null" if description is None else f"size=5, description={description}"
    return REAL_DUMP_TEMPLATE.format(state=state, metadata=metadata)


WINDOW_DUMP = """
WINDOW MANAGER FOCUS & FOCUSABLE WINDOWS (dumpsys window windows)
  mCurrentFocus=Window{1a2b3c4 u0 com.google.android.youtube.tvunplugged/com.google.android.apps.youtube.tvunplugged.activity.MainActivity}
  mFocusedApp=ActivityRecord{5d6e7f8 u0 com.google.android.youtube.tvunplugged/.MainActivity t123}
"""


def test_parse_window_focus():
    pkg = parse_window_focus(WINDOW_DUMP)
    assert pkg == YTTV


def test_parse_live_channel():
    meta = parse_media_session(real_dump("The Five, FOX News, null"), target_pkg=YTTV)
    assert meta.package == YTTV
    assert meta.playback_state == "playing"
    assert meta.is_playing is True
    assert meta.title == "The Five"
    assert meta.channel == "FOX News"
    assert meta.description == ""
    assert meta.is_enforceable is True
    assert meta.full_text == "The Five FOX News"


def test_parse_title_containing_commas():
    meta = parse_media_session(real_dump("Hannity, Special Edition, FOX News, null"), target_pkg=YTTV)
    assert meta.title == "Hannity, Special Edition"
    assert meta.channel == "FOX News"


def test_parse_without_target_uses_active_session():
    meta = parse_media_session(real_dump("The Herd With Colin Cowherd, FS1, null"))
    assert meta.package == YTTV
    assert meta.channel == "FS1"


def test_transitional_null_metadata_not_enforceable():
    meta = parse_media_session(real_dump("null, null, null"), target_pkg=YTTV)
    assert meta.title == meta.channel == ""
    assert meta.is_enforceable is False


def test_missing_session_not_enforceable():
    meta = parse_media_session(NO_YTTV_SESSION, target_pkg=YTTV)
    assert meta.package == YTTV
    assert meta.playback_state == "unknown"
    assert meta.is_enforceable is False


def test_paused_channel_not_enforceable():
    meta = parse_media_session(real_dump("The Five, FOX News, null", state=2), target_pkg=YTTV)
    assert meta.playback_state == "paused"
    assert meta.channel == "FOX News"
    assert meta.is_enforceable is False


def test_parse_media_session_empty():
    meta = parse_media_session("", target_pkg="")
    assert meta.is_playing is False
    assert meta.title == ""
    assert meta.full_text == ""
