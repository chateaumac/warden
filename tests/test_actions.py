"""Unit tests for enforcement action execution."""

from unittest.mock import MagicMock

import pytest

from app.guard.actions import execute_action, normalize_tune_url


def test_execute_auto_skip():
    mock_conn = MagicMock()
    res = execute_action(
        connector=mock_conn,
        action="auto_skip",
        target_pkg="com.google.android.youtube.tvunplugged",
        key_sequence=["KEYCODE_CHANNEL_UP"],
    )
    assert res["status"] == "executed"
    mock_conn.shell.assert_called_once_with("input keyevent KEYCODE_CHANNEL_UP")


def test_execute_force_stop():
    mock_conn = MagicMock()
    res = execute_action(
        connector=mock_conn,
        action="force_stop",
        target_pkg="com.google.android.youtube.tvunplugged",
    )
    assert res["status"] == "executed"
    mock_conn.shell.assert_called_once_with("am force-stop com.google.android.youtube.tvunplugged")


def test_execute_back():
    mock_conn = MagicMock()
    res = execute_action(
        connector=mock_conn,
        action="back",
    )
    assert res["status"] == "executed"
    assert "KEYCODE_BACK" in mock_conn.shell.call_args[0][0]


def test_execute_tune_opens_watch_link_in_youtube_tv():
    mock_conn = MagicMock()
    res = execute_action(
        connector=mock_conn,
        action="tune",
        tune_url="https://tv.youtube.com/watch/AW9-z6zluec",
    )
    assert res["status"] == "executed"
    mock_conn.shell.assert_called_once_with(
        "am start -a android.intent.action.VIEW -d https://tv.youtube.com/watch/AW9-z6zluec "
        "-n com.google.android.youtube.tvunplugged/"
        "com.google.android.apps.youtube.tvunplugged.activity.MainActivity"
    )


def test_execute_tune_without_link_fails_without_shell():
    mock_conn = MagicMock()
    res = execute_action(connector=mock_conn, action="tune", tune_url="")
    assert res["status"] == "failed"
    mock_conn.shell.assert_not_called()


@pytest.mark.parametrize("value", [
    "https://tv.youtube.com/watch/AW9-z6zluec",
    "tv.youtube.com/watch/AW9-z6zluec?vp=0gEEEgIwAQ%3D%3D",
    "AW9-z6zluec",
])
def test_normalize_tune_url(value):
    assert normalize_tune_url(value) == "https://tv.youtube.com/watch/AW9-z6zluec"


@pytest.mark.parametrize("value", [
    "https://evil.example/watch/AW9-z6zluec",
    "AW9-z6zluec; reboot",
    "$(reboot)",
    "",
])
def test_normalize_tune_url_rejects_other_input(value):
    with pytest.raises(ValueError):
        normalize_tune_url(value)
