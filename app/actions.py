"""Evaluate and enforce profile actions over a connector.

Pure logic — connectors are duck-typed (anything with .shell() and .supports),
which keeps this unit-testable without a real device.
"""

import re
import shlex
from dataclasses import dataclass

VAR_RE = re.compile(r"\{var:([A-Za-z0-9_]+)\}")


class MissingVar(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(name)


@dataclass
class ActionResult:
    action_id: str
    name: str
    status: str  # compliant | drifted | fixed | na | disabled | skipped | unsupported | error
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
            installed = pkg in _pm_packages(conn, cache, "pm_all", "pm list packages")
            if not installed:
                return _result(action, "na", expected="disabled", observed="not installed",
                               detail="Package not present on this device")
            disabled = pkg in _pm_packages(conn, cache, "pm_disabled", "pm list packages -d")
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
