"""Evaluate and enforce profile actions over a connector.

Pure logic — connectors are duck-typed (anything with .shell() and .supports),
which keeps this unit-testable without a real device.
"""

import re
import shlex
from dataclasses import dataclass

VAR_RE = re.compile(r"\{var:([A-Za-z0-9_]+)\}")

# Packages a TV cannot run without; never disabled, whatever a profile says. Taken from
# cleanyourtv's tools/recovery.py, where each entry cost someone a TV.
CORE_RE = re.compile(
    r"^(android|android\.ext\.(services|shared)|com\.android\.systemui|com\.android\.shell"
    r"|com\.android\.phone|com\.android\.settings|com\.android\.tv\.settings"
    r"|com\.(google\.)?android\.(permissioncontroller|packageinstaller|ext\.services|ext\.shared"
    r"|modulemetadata)|com\.google\.android\.overlay\.modules.*|com\.android\.providers\..*"
    r"|com\.cltv\.mal|com\.mediatek\.(tis|tv\.service|tv\.agent))$"
)
SETUPWRAITH = "com.google.android.tungsten.setupwraith"
INPUT_PERMISSION = "android.permission.TV_INPUT_HARDWARE"
TV_INPUT_SERVICE = "android.media.tv.TvInputService"
HANDLER_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/", re.M)


class MissingVar(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(name)


@dataclass
class ActionResult:
    action_id: str
    name: str
    # compliant | drifted | fixed | blocked | reverted | na | disabled | skipped | unsupported | error
    status: str
    detail: str = ""
    expected: str = ""
    observed: str = ""


def render(template: str, variables: dict | None) -> str:
    """Substitute {var:name} placeholders; empty/missing vars raise MissingVar."""

    def sub(match: re.Match) -> str:
        value = (variables or {}).get(match.group(1), "")
        if not value:
            raise MissingVar(match.group(1))
        return value

    return VAR_RE.sub(sub, str(template))


def _missing_vars(action: dict, variables: dict | None) -> list[str]:
    return [v for v in action.get("requires_vars", []) if not (variables or {}).get(v)]


def _pm_packages(conn, cache: dict, key: str, cmd: str) -> set[str]:
    if key not in cache:
        out = conn.shell(cmd)
        cache[key] = {
            line.split(":", 1)[1].strip()
            for line in out.splitlines()
            if line.startswith("package:")
        }
    return cache[key]


def installed_packages(conn, cache: dict) -> set[str]:
    """Packages installed for user 0."""
    return _pm_packages(conn, cache, "pm_all", "pm list packages --user 0")


def _asks_for_input_hardware(conn, pkg: str) -> bool | None:
    """Whether the package drives the HDMI/AV/tuner inputs; None when dumpsys doesn't know it.

    The permission only shows up in the package's own dump (requested, install or runtime
    block), and the input service in its resolver table, so a mention of either is enough.
    A package that asks for no permissions has no permission block at all, hence the check
    for its "Package [...]" header instead of for a block.
    """
    dump = conn.shell(f"dumpsys package {shlex.quote(pkg)}")
    if INPUT_PERMISSION in dump or TV_INPUT_SERVICE in dump:
        return True
    if f"Package [{pkg}]" in dump:
        return False
    return None


def _handler_apps(conn, cache: dict) -> tuple[set[str], set[str]]:
    """The apps the TV opens its inputs with and the ones it opens Settings with."""
    if "handlers" not in cache:
        query = "cmd package query-activities --brief"
        inputs = conn.shell(f"{query} -a android.intent.action.VIEW "
                            "-t vnd.android.cursor.item/channel")
        settings = conn.shell(f"{query} -a android.settings.SETTINGS")
        cache["handlers"] = (set(HANDLER_RE.findall(inputs)), set(HANDLER_RE.findall(settings)))
    return cache["handlers"]


def disable_blocker(conn, pkg: str, cache: dict) -> str | None:
    """Why disabling `pkg` could leave the TV unusable from the couch, or None if it looks safe.

    Ported from cleanyourtv's disable.sh/check.py guards. Unattended enforcement repeats a bad
    profile line on every audit, so anything the TV cannot confirm is treated as unsafe.
    """
    if CORE_RE.match(pkg):
        return "core Android package; the TV does not boot or work properly without it"
    if pkg == SETUPWRAITH:
        owners = re.findall(r"cname=(\S*)", conn.shell("dumpsys wifi"))
        if not owners or any("setupwraith" in o for o in owners):
            return ("setup wizard owns (or may own) the saved Wi-Fi network; disabling it deletes "
                    "that network. Forget and re-join the network in Settings first")
    hardware = _asks_for_input_hardware(conn, pkg)
    if hardware is None:
        return ("could not tell from dumpsys whether it drives the HDMI/AV inputs; "
                "set force: true on the action once you have checked")
    if hardware:
        return (f"requests {INPUT_PERMISSION}: it runs the HDMI/AV/tuner inputs, "
                "which stay black while it is disabled")
    inputs, settings = _handler_apps(conn, cache)
    if pkg in inputs:
        return "the TV opens its HDMI inputs and channels with it"
    if pkg in settings:
        return "the TV opens Settings with it"
    return None


def _result(action: dict, status: str, **kw) -> ActionResult:
    return ActionResult(action_id=action["id"], name=action.get("name", action["id"]),
                        status=status, **kw)


def evaluate_action(conn, action: dict, variables: dict | None, cache: dict) -> ActionResult:
    """Check whether the device currently complies with one action. Never raises."""
    action_type = action["type"]
    if action_type not in getattr(conn, "supports", frozenset()):
        return _result(action, "unsupported",
                       detail=f"This device's connector cannot run '{action_type}' actions")
    missing = _missing_vars(action, variables)
    if missing:
        return _result(action, "skipped",
                       detail=f"Set device variable(s) {', '.join(missing)} to enable this action")
    try:
        if action_type == "package_disable":
            pkg = action["package"]
            # --user 0 throughout: Android 14's pm merges every user's answer without it, so a
            # package disabled only for a dormant second user (the Chromecast ships one) would
            # read as disabled while user 0, the one we disable it for, still runs it.
            installed = pkg in installed_packages(conn, cache)
            if not installed:
                return _result(action, "na", expected="disabled", observed="not installed",
                               detail="Package not present on this device")
            disabled = pkg in _pm_packages(conn, cache, "pm_disabled",
                                           "pm list packages -d --user 0")
            return _result(action, "compliant" if disabled else "drifted",
                           expected="disabled", observed="disabled" if disabled else "enabled")

        if action_type == "setting":
            expected = render(action["value"], variables)
            observed = conn.shell(
                f"settings get {action['namespace']} {action['key']}"
            ).strip()
            return _result(action, "compliant" if observed == expected else "drifted",
                           expected=expected, observed=observed)

        # generic shell check
        out = conn.shell(render(action["check_cmd"], variables))
        if action.get("expect_regex"):
            ok = re.search(action["expect_regex"], out) is not None
            expected = f"output matches /{action['expect_regex']}/"
        else:
            expect = render(action.get("expect", ""), variables)
            ok = expect in out
            expected = f"output contains {expect!r}"
        return _result(action, "compliant" if ok else "drifted",
                       expected=expected, observed=out.strip()[:400])

    except MissingVar as exc:
        return _result(action, "skipped",
                       detail=f"Set the '{exc.name}' variable on this device to enable this action")
    except Exception as exc:  # connector/IO errors must not abort the whole audit
        return _result(action, "error", detail=str(exc))


def enforce_action(conn, action: dict, variables: dict | None, cache: dict) -> ActionResult:
    """Apply one action, then re-evaluate. 'fixed' means the value now sticks."""
    action_type = action["type"]
    missing = _missing_vars(action, variables)
    if missing:
        return _result(action, "skipped",
                       detail=f"Set device variable(s) {', '.join(missing)} to enable this action")
    try:
        if action_type == "package_disable":
            if not action.get("force"):
                reason = disable_blocker(conn, action["package"], cache)
                if reason:
                    return _result(action, "blocked", expected="disabled", observed="enabled",
                                   detail=f"Not disabled: {reason}")
            conn.shell(f"pm disable-user --user 0 {shlex.quote(action['package'])}")
            cache.pop("pm_disabled", None)  # force a fresh read on re-evaluation
        elif action_type == "setting":
            value = render(action["value"], variables)
            conn.shell(f"settings put {action['namespace']} {action['key']} {shlex.quote(value)}")
        else:
            conn.shell(render(action["enforce_cmd"], variables))
    except MissingVar as exc:
        return _result(action, "skipped",
                       detail=f"Set the '{exc.name}' variable on this device to enable this action")
    except Exception as exc:
        return _result(action, "error", detail=f"enforce failed: {exc}")

    result = evaluate_action(conn, action, variables, cache)
    if result.status == "compliant":
        result.status = "fixed"
        result.detail = "Drift detected — setting re-applied"
    elif result.status == "drifted":
        result.status = "error"
        result.detail = "Enforce command ran but the device did not keep the value"
    return result


def revert_action(conn, action: dict, variables: dict | None, original: str | None) -> ActionResult:
    """Undo one action: re-enable the package, or put back the setting's value from before the
    first enforce (`original`). Never raises."""
    action_type = action["type"]
    try:
        if action_type == "package_disable":
            pkg = action["package"]
            out = conn.shell(f"pm enable --user 0 {shlex.quote(pkg)}")
            disabled = conn.shell("pm list packages -d --user 0")
            if f"package:{pkg}" in disabled.split():
                return _result(action, "error", expected="enabled", observed="disabled",
                               detail=f"pm enable did not stick: {out.strip()[:200]}")
            return _result(action, "reverted", expected="enabled", observed="enabled",
                           detail="Package re-enabled; Warden no longer manages it on this device")
        if action_type == "setting":
            if original is None:
                return _result(action, "error",
                               detail="No value from before Warden changed it was recorded")
            ns, key = action["namespace"], action["key"]
            if original in ("", "null"):
                conn.shell(f"settings delete {ns} {key}")
            else:
                conn.shell(f"settings put {ns} {key} {shlex.quote(original)}")
            observed = conn.shell(f"settings get {ns} {key}").strip()
            return _result(action, "reverted", expected=original or "null", observed=observed,
                           detail="Setting restored; Warden no longer manages it on this device")
        if action.get("revert_cmd"):
            conn.shell(render(action["revert_cmd"], variables))
            return _result(action, "reverted", detail="Revert command ran")
        return _result(action, "unsupported", detail="This action has no revert_cmd")
    except MissingVar as exc:
        return _result(action, "skipped",
                       detail=f"Set the '{exc.name}' variable on this device to revert this action")
    except Exception as exc:
        return _result(action, "error", detail=f"revert failed: {exc}")
