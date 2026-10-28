"""Profile action audit/enforce/revert against a simulated Android 14 TV, and the engine around it."""

import re

import pytest

from app import enforcer as enforcer_mod
from app.actions import disable_blocker, enforce_action, evaluate_action, revert_action
from app.db import Database
from app.enforcer import Engine
from app.profiles import Profile

ADS = "com.google.android.adservices.api"
FEEDBACK = "com.google.android.feedback"
TVINPUT = "com.tcl.tvinput"
SETUPWRAITH = "com.google.android.tungsten.setupwraith"


class FakeTv:
    """Answers `pm`, `dumpsys`, `settings` and `getprop` the way Android 14 does.

    `users` maps a user id to {package: "enabled" | "disabled"}. Without --user, `pm list
    packages` asks every user and prints the union, as Android 14's runListPackages does.
    """

    supports = frozenset({"shell", "package_disable", "setting"})

    def __init__(self, users=None, dumps=None, wifi_owner="android.uid.system:1000",
                 settings=None, fingerprint="tcl/g08/AU14", handlers=None):
        self.users = users or {"0": {ADS: "enabled", FEEDBACK: "enabled"}}
        self.dumps = dumps or {}
        self.wifi_owner = wifi_owner
        self.settings = settings or {}
        self.fingerprint = fingerprint
        self.handlers = handlers or {"inputs": ["com.tcl.tv"], "settings": ["com.android.tv.settings"]}
        self.commands: list[str] = []

    def connect(self, auth_timeout_s=None):
        return {"manufacturer": "TCL", "model": "Smart TV Pro", "os": "Android 14",
                "fingerprint": self.fingerprint}

    def close(self):
        pass

    def shell(self, cmd: str) -> str:
        self.commands.append(cmd)
        words = cmd.split()
        if words[:3] == ["pm", "list", "packages"]:
            users = [words[words.index("--user") + 1]] if "--user" in words else list(self.users)
            want = "disabled" if "-d" in words else None
            names = {p for u in users for p, s in self.users[u].items() if want in (None, s)}
            return "".join(f"package:{n}\n" for n in sorted(names))
        if words[:2] == ["pm", "disable-user"]:
            self.users[words[3]][words[4]] = "disabled"
            return f"Package {words[4]} new state: disabled-user"
        if words[:2] == ["pm", "enable"]:
            self.users[words[3]][words[4]] = "enabled"
            return f"Package {words[4]} new state: enabled"
        if words[:2] == ["dumpsys", "package"]:
            return self.dumps.get(words[2], f"Packages:\n  Package [{words[2]}] (1a2b3c):\n")
        if words[:2] == ["dumpsys", "wifi"]:
            return f"WifiConfigManager: ... cname={self.wifi_owner} ..."
        if "query-activities" in cmd:
            names = self.handlers["settings" if "SETTINGS" in cmd else "inputs"]
            return "".join(f"  {n}/.Main\n" for n in names)
        if words[:2] == ["settings", "get"]:
            return self.settings.get((words[2], words[3]), "null") + "\n"
        if words[:2] == ["settings", "put"]:
            self.settings[(words[2], words[3])] = words[4].strip("'")
            return ""
        if words[:2] == ["settings", "delete"]:
            self.settings.pop((words[2], words[3]), None)
            return ""
        return ""


def pkg_action(pkg, **kw):
    return {"id": f"disable-{pkg}", "name": pkg, "type": "package_disable", "package": pkg, **kw}


DNS_ACTION = {"id": "private-dns-mode", "name": "Private DNS", "type": "setting",
              "namespace": "global", "key": "private_dns_mode", "value": "hostname"}


# ---------------------------------------------------------------- user 0 only


def test_package_disabled_only_for_second_user_reads_as_drifted():
    # The Chromecast's dormant user 10 has the package disabled; user 0 still runs it
    tv = FakeTv(users={"0": {ADS: "enabled"}, "10": {ADS: "disabled"}})
    result = evaluate_action(tv, pkg_action(ADS), {}, {})
    assert result.status == "drifted"
    assert all("--user 0" in c for c in tv.commands if c.startswith("pm list"))


def test_package_installed_only_for_second_user_is_not_installed():
    tv = FakeTv(users={"0": {}, "10": {ADS: "enabled"}})
    assert evaluate_action(tv, pkg_action(ADS), {}, {}).status == "na"


# --------------------------------------------------------------- safety guards


@pytest.mark.parametrize("pkg", ["android", "com.android.systemui", "com.android.tv.settings",
                                 "com.android.providers.settings", "com.mediatek.tv.service"])
def test_core_packages_are_refused(pkg):
    tv = FakeTv(users={"0": {pkg: "enabled"}})
    result = enforce_action(tv, pkg_action(pkg), {}, {})
    assert result.status == "blocked"
    assert tv.users["0"][pkg] == "enabled"
    assert not any(c.startswith("pm disable") for c in tv.commands)


def test_input_hardware_package_is_refused():
    dump = (f"Packages:\n  Package [{TVINPUT}] (1):\n    requested permissions:\n"
            "      android.permission.TV_INPUT_HARDWARE\n")
    tv = FakeTv(users={"0": {TVINPUT: "enabled"}}, dumps={TVINPUT: dump})
    result = enforce_action(tv, pkg_action(TVINPUT), {}, {})
    assert result.status == "blocked"
    assert "HDMI" in result.detail


def test_package_unknown_to_dumpsys_fails_closed():
    tv = FakeTv(dumps={ADS: "Unable to find package: " + ADS})
    assert enforce_action(tv, pkg_action(ADS), {}, {}).status == "blocked"


def test_settings_and_input_handlers_are_refused():
    tv = FakeTv(users={"0": {"com.tcl.tv": "enabled", "com.vendor.settings": "enabled"}},
                handlers={"inputs": ["com.tcl.tv"], "settings": ["com.vendor.settings"]})
    assert "inputs" in disable_blocker(tv, "com.tcl.tv", {})
    assert "Settings" in disable_blocker(tv, "com.vendor.settings", {})


@pytest.mark.parametrize("owner, blocked", [
    ("com.google.android.tungsten.setupwraith", True),
    ("android.uid.system:1000", False),
])
def test_setupwraith_is_refused_while_it_owns_the_wifi_network(owner, blocked):
    tv = FakeTv(users={"0": {SETUPWRAITH: "enabled"}}, wifi_owner=owner)
    assert (disable_blocker(tv, SETUPWRAITH, {}) is not None) is blocked


def test_force_skips_the_guards():
    tv = FakeTv(users={"0": {TVINPUT: "enabled"}},
                dumps={TVINPUT: "android.permission.TV_INPUT_HARDWARE"})
    assert enforce_action(tv, pkg_action(TVINPUT, force=True), {}, {}).status == "fixed"


def test_ordinary_tracker_is_disabled():
    tv = FakeTv()
    assert enforce_action(tv, pkg_action(ADS), {}, {}).status == "fixed"
    assert tv.users["0"][ADS] == "disabled"


# --------------------------------------------------------------------- revert


def test_revert_reenables_package():
    tv = FakeTv(users={"0": {ADS: "disabled"}})
    assert revert_action(tv, pkg_action(ADS), {}, None).status == "reverted"
    assert tv.users["0"][ADS] == "enabled"


@pytest.mark.parametrize("original, expected", [("off", "off"), ("null", None)])
def test_revert_restores_or_deletes_setting(original, expected):
    tv = FakeTv(settings={("global", "private_dns_mode"): "hostname"})
    assert revert_action(tv, DNS_ACTION, {}, original).status == "reverted"
    assert tv.settings.get(("global", "private_dns_mode")) == expected


def test_revert_setting_without_original_is_an_error():
    assert revert_action(FakeTv(), DNS_ACTION, {}, None).status == "error"


# --------------------------------------------------------------------- engine


class Settings:
    audit_startup_delay_s = 0
    audit_interval_s = 900


class Notifier:
    def __init__(self):
        self.sent = []

    def notify(self, title, message, tags=None):
        self.sent.append((title, message))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    db = Database(tmp_path / "w.db")
    profile = Profile(id="p", name="P", connector="adb", default_port=5555, description="",
                      howto="", match={}, vars=[], source="p.yaml",
                      actions=[pkg_action(ADS), pkg_action(TVINPUT), DNS_ACTION])
    notifier = Notifier()
    engine = Engine(db, {"p": profile}, Settings(), notifier=notifier)
    device = db.create_device(name="Den TV", host="192.0.2.5", port=5555, connector="adb",
                              profile_id="p", mode="enforce")
    tv = FakeTv(users={"0": {ADS: "enabled", TVINPUT: "enabled"}},
                dumps={TVINPUT: "android.permission.TV_INPUT_HARDWARE"},
                settings={("global", "private_dns_mode"): "off"})
    monkeypatch.setattr(enforcer_mod, "make_connector", lambda d, s: tv)
    return db, engine, device["id"], tv, notifier


def test_enforce_records_blocked_and_setting_original(rig):
    db, engine, dev_id, tv, _ = rig
    out = engine.run(dev_id)
    by_id = {r["action_id"]: r["status"] for r in out["results"]}
    assert by_id == {f"disable-{ADS}": "fixed", f"disable-{TVINPUT}": "blocked",
                     "private-dns-mode": "fixed"}
    assert out["status"] == "drifted"  # the refused action is still outstanding
    device = db.get_device(dev_id)
    assert device["originals"] == {"private-dns-mode": "off"}
    assert any("refused as unsafe" in e["message"] for e in db.list_events(dev_id))


def test_revert_all_undoes_and_turns_actions_off(rig):
    db, engine, dev_id, tv, _ = rig
    engine.run(dev_id)
    out = engine.revert(dev_id)
    assert tv.users["0"][ADS] == "enabled"
    assert tv.settings[("global", "private_dns_mode")] == "off"
    device = db.get_device(dev_id)
    assert device["action_overrides"][f"disable-{ADS}"] is False
    assert device["originals"] == {}
    assert out["ok"]
    # Enforce mode leaves reverted actions alone from now on
    engine.run(dev_id)
    assert tv.users["0"][ADS] == "enabled"


def test_firmware_update_reports_new_preloads(rig):
    db, engine, dev_id, tv, notifier = rig
    engine.run(dev_id)
    assert not notifier.sent  # first audit only takes the snapshot
    tv.fingerprint = "tcl/g08/AU16"
    tv.users["0"]["com.tcl.newpromo"] = "enabled"
    engine.run(dev_id)
    events = db.list_events(dev_id)
    firmware = next(e for e in events if e["kind"] == "firmware")
    assert "AU16" in firmware["message"] and "com.tcl.newpromo" in firmware["detail"]
    assert re.search("firmware update", notifier.sent[-1][0])
    assert "com.tcl.newpromo" in notifier.sent[-1][1]
