"""Unit tests for enforcement action execution."""

from unittest.mock import MagicMock

from app.guard.actions import execute_action


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
