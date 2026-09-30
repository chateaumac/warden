"""Guard poll-step tests replaying media session sequences observed on a real TCL / YouTube TV."""

import time as real_time

import pytest

from app.guard import engine as engine_mod
from app.guard.engine import GuardEngine
from app.guard.state import DeviceState, GuardState
from tests.test_parser import NO_YTTV_SESSION, real_dump

CNN_LINK = "https://tv.youtube.com/watch/AW9-z6zluec"
DEAD_LINK = "https://tv.youtube.com/watch/ZZZZZZZZZZZ"

FOX_RULE = {
    "id": 1,
    "name": "Block Fox News channels",
    "enabled": True,
    "target_packages": ["com.google.android.youtube.tvunplugged"],
    "channels": ["FOX News", "FOX Business", "LiveNOW from FOX"],
    "patterns": [],
    "action": "tune",
    "key_sequence": [],
    "tune_urls": [CNN_LINK],
}
DEVICE = {"id": 7, "name": "Living Room TV", "host": "192.0.2.10"}

FOX = real_dump("The Five, FOX News, null")
FOX_BUSINESS = real_dump("The Evening Edit, FOX Business, null")
CNN = real_dump("The Lead With Jake Tapper, CNN, null")
SWITCHING = [FOX, NO_YTTV_SESSION, real_dump("null, null, null")]


class FakeTime:
    """Monotonic clock that only advances when the engine sleeps."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return real_time.time()


class FakeConnector:
    """Replays `dumpsys media_session` outputs; after each tune, switches to that link's script.

    `links` maps a tune URL to the dumps shown after it is opened; unknown links (and the
    initial state) repeat their last dump, like a TV that ignored the intent.
    """

    def __init__(self, dumps: list[str], links: dict[str, list[str]] | None = None):
        self.dumps = list(dumps)
        self.links = links or {}
        self.commands: list[str] = []

    def connect(self, auth_timeout_s=None):
        return {}

    def shell(self, cmd: str) -> str:
        self.commands.append(cmd)
        if cmd == "dumpsys media_session":
            return self.dumps.pop(0) if len(self.dumps) > 1 else self.dumps[0]
        for url, script in self.links.items():
            if url in cmd:
                self.dumps = list(script)
        return ""

    def close(self):
        pass

    @property
    def actions(self) -> list[str]:
        return [c for c in self.commands if c != "dumpsys media_session"]


class FakeDb:
    def __init__(self, rules):
        self.rules = rules
        self.events: list[dict] = []

    def list_channel_rules(self):
        return self.rules

    def add_event(self, **event):
        self.events.append(event)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(engine_mod, "time", fake)
    return fake


@pytest.fixture
def run_poll(monkeypatch, clock):
    def run(dumps, links=None, tune_urls=(CNN_LINK,)):
        conn = FakeConnector(dumps, links)
        monkeypatch.setattr(engine_mod, "make_connector", lambda device, settings: conn)
        db = FakeDb([{**FOX_RULE, "tune_urls": list(tune_urls)}])
        guard = GuardEngine(db=db, settings=None)
        state = GuardState(device_id=DEVICE["id"])
        guard._poll_step(DEVICE, {"cooldown_s": 15.0}, state)
        return conn, db, state

    return run


def test_tune_away_from_fox_news_lands_on_cnn(run_poll):
    # Fox News keeps reporting ~1s after the tune, then the session drops and CNN starts
    conn, db, state = run_poll([FOX], links={CNN_LINK: [*SWITCHING, CNN]})
    assert conn.actions == [
        "am start -a android.intent.action.VIEW -d https://tv.youtube.com/watch/AW9-z6zluec "
        "-n com.google.android.youtube.tvunplugged/"
        "com.google.android.apps.youtube.tvunplugged.activity.MainActivity"
    ]
    assert state.state == DeviceState.COOLDOWN
    assert state.last_action_name == "tune"
    assert state.last_violation_detail == "Blocked: FOX News: The Five"
    assert db.events[0]["level"] == "warning"
    assert "landed on CNN" in db.events[0]["detail"]


def test_dead_link_is_abandoned_fast_for_the_next_one(run_poll, clock):
    conn, db, state = run_poll(
        [FOX],
        links={CNN_LINK: [*SWITCHING, CNN]},  # DEAD_LINK leaves Fox News playing
        tune_urls=(DEAD_LINK, CNN_LINK),
    )
    assert [a.split(" -d ")[1].split()[0] for a in conn.actions] == [DEAD_LINK, CNN_LINK]
    assert state.last_action_name == "tune"
    detail = db.events[0]["detail"]
    assert "landed on CNN after 1 failed link(s)" in detail
    assert f"{DEAD_LINK}: no channel change (dead link?)" in detail
    # Dead link rejected at TUNE_REJECT_S, not the full verify timeout; then ~4s to land on CNN
    assert clock.now <= engine_mod.TUNE_REJECT_S + 5


def test_all_links_dead_falls_back_to_home(run_poll):
    conn, db, state = run_poll([FOX], tune_urls=(DEAD_LINK, "https://tv.youtube.com/watch/YYYYYYYYYYY"))
    assert conn.actions[-1] == "input keyevent KEYCODE_HOME"
    assert len(conn.actions) == 3
    assert state.last_action_name == "tune→home"
    assert db.events[0]["level"] == "error"
    assert "update the rule's tune links" in db.events[0]["detail"]


def test_link_to_another_blocked_channel_tries_the_next(run_poll):
    fox_business_link = "https://tv.youtube.com/watch/FoxBiz12345"
    conn, db, state = run_poll(
        [FOX],
        links={fox_business_link: [*SWITCHING, FOX_BUSINESS], CNN_LINK: [FOX_BUSINESS, NO_YTTV_SESSION, CNN]},
        tune_urls=(fox_business_link, CNN_LINK),
    )
    assert state.last_action_name == "tune"
    assert f"{fox_business_link}: landed on blocked FOX Business" in db.events[0]["detail"]
    assert "landed on CNN" in db.events[0]["detail"]


def test_tune_rule_without_links_goes_home(run_poll):
    conn, db, state = run_poll([FOX], tune_urls=())
    assert conn.actions == ["input keyevent KEYCODE_HOME"]
    assert "rule has no tune links" in db.events[0]["detail"]


@pytest.mark.parametrize("dump", [
    real_dump("null, null, null"),
    NO_YTTV_SESSION,
    real_dump("The Five, FOX News, null", state=2),  # paused
])
def test_no_action_without_a_playing_channel(run_poll, dump):
    conn, db, state = run_poll([dump])
    assert conn.actions == []
    assert state.state == DeviceState.IDLE
    assert db.events == []


def test_allowed_channel_is_monitored(run_poll):
    conn, db, state = run_poll([real_dump("The Herd With Colin Cowherd, FS1, null")])
    assert conn.actions == []
    assert state.state == DeviceState.MONITORING
    assert state.status_detail == "Monitoring FS1: The Herd With Colin Cowherd"


def test_poll_reads_only_media_session(run_poll):
    conn, _, _ = run_poll([real_dump("The Herd With Colin Cowherd, FS1, null")])
    assert conn.commands == ["dumpsys media_session"]


def test_pending_approval_holds_one_connection_instead_of_reprompting(monkeypatch, clock):
    """Each unauthorized connect pops another dialog on the TV, so after the first one
    the guard waits on a single long-lived connection."""
    from app.connectors.base import Unauthorized

    timeouts = []

    class PromptingConnector(FakeConnector):
        def connect(self, auth_timeout_s=None):
            timeouts.append(auth_timeout_s)
            if len(timeouts) < 3:
                raise Unauthorized("waiting for Allow USB debugging")
            return {}

    conn = PromptingConnector([real_dump("The Herd With Colin Cowherd, FS1, null")])
    monkeypatch.setattr(engine_mod, "make_connector", lambda device, settings: conn)
    guard = GuardEngine(db=FakeDb([FOX_RULE]), settings=None)
    state = GuardState(device_id=DEVICE["id"])
    for _ in range(3):
        guard._poll_step(DEVICE, {"cooldown_s": 15.0}, state)

    assert timeouts == [3.0, engine_mod.AUTH_PROMPT_WAIT_S, engine_mod.AUTH_PROMPT_WAIT_S]
    assert state.auth_pending is False
    assert state.state == DeviceState.MONITORING
