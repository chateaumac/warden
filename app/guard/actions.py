"""Action execution logic for content enforcement (Auto-Skip, Force-Stop, Back, Home, Mute)."""

import logging
import re
import shlex

log = logging.getLogger(__name__)

YTTV_PACKAGE = "com.google.android.youtube.tvunplugged"
YTTV_ACTIVITY = "com.google.android.apps.youtube.tvunplugged.activity.MainActivity"
# Accepts "https://tv.youtube.com/watch/<id>", "tv.youtube.com/watch/<id>?..." or a bare id
WATCH_ID_RE = re.compile(r"^(?:(?:https?://)?tv\.youtube\.com/watch/)?([A-Za-z0-9_-]{6,64})(?:[?#].*)?$")


def normalize_tune_url(value: str) -> str:
    """Return the canonical tv.youtube.com watch link, or raise ValueError."""
    m = WATCH_ID_RE.match((value or "").strip())
    if not m:
        raise ValueError(f"not a tv.youtube.com/watch link or id: {value!r}")
    return f"https://tv.youtube.com/watch/{m.group(1)}"


def execute_action(
    connector,
    action: str,
    target_pkg: str = "",
    key_sequence: list[str] | None = None,
    tune_url: str = "",
) -> dict[str, str]:
    """Execute the specified enforcement action on the target device via ADB shell."""
    result = {"action": action, "status": "executed", "detail": ""}

    try:
        if action == "auto_skip":
            keys = key_sequence or ["KEYCODE_CHANNEL_UP"]
            cmd_parts = []
            for i, key in enumerate(keys):
                cmd_parts.append(f"input keyevent {shlex.quote(key)}")
                if i < len(keys) - 1:
                    cmd_parts.append("sleep 0.25")
            full_cmd = " && ".join(cmd_parts)
            connector.shell(full_cmd)
            result["detail"] = f"Sent key sequence: {' -> '.join(keys)}"
            log.info("Enforced auto_skip on %s via %s", target_pkg or "device", keys)

        elif action == "tune":
            url = normalize_tune_url(tune_url)
            # Delivered to the running YouTube TV instance, so it switches channel in place
            connector.shell(
                f"am start -a android.intent.action.VIEW -d {shlex.quote(url)} "
                f"-n {YTTV_PACKAGE}/{YTTV_ACTIVITY}"
            )
            result["detail"] = f"Opened {url}"
            log.info("Enforced tune to %s", url)

        elif action == "force_stop":
            pkg = target_pkg or "com.google.android.youtube.tvunplugged"
            connector.shell(f"am force-stop {shlex.quote(pkg)}")
            result["detail"] = f"Force-stopped package {pkg}"
            log.info("Enforced force_stop on %s", pkg)

        elif action == "back":
            connector.shell("input keyevent KEYCODE_BACK && sleep 0.3 && input keyevent KEYCODE_BACK")
            result["detail"] = "Sent KEYCODE_BACK x2"
            log.info("Enforced back on device")

        elif action == "home":
            connector.shell("input keyevent KEYCODE_HOME")
            result["detail"] = "Sent KEYCODE_HOME"
            log.info("Enforced home on device")

        elif action == "mute":
            connector.shell("input keyevent KEYCODE_VOLUME_MUTE")
            result["detail"] = "Muted audio output"
            log.info("Enforced mute on device")

        else:
            # Fallback to force stop
            pkg = target_pkg or "com.google.android.youtube.tvunplugged"
            connector.shell(f"am force-stop {shlex.quote(pkg)}")
            result["detail"] = f"Unknown action '{action}', fell back to force-stop {pkg}"

    except Exception as exc:
        result["status"] = "failed"
        result["detail"] = f"Action failed: {exc}"
        log.error("Failed to execute action %s: %s", action, exc)

    return result
