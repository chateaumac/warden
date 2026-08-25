"""Unit tests for channel rule evaluation and regex matching."""

from app.guard.parser import MediaMetadata
from app.guard.rules import ChannelRule, evaluate_rules


def test_evaluate_rules_match():
    rules = [
        ChannelRule(
            id=1,
            name="Block Fox News",
            enabled=True,
            target_packages=["com.google.android.youtube.tvunplugged"],
            patterns=[r"fox\s*news", r"\bFNC\b"],
            action="auto_skip",
            key_sequence=["KEYCODE_CHANNEL_UP"],
        )
    ]

    meta = MediaMetadata(
        package="com.google.android.youtube.tvunplugged",
        is_playing=True,
        title="Live: Fox News Channel HD",
        subtitle="Special Report",
    )

    match = evaluate_rules(rules, meta, active_pkg="com.google.android.youtube.tvunplugged")
    assert match is not None
    assert match.matched is True
    assert match.action == "auto_skip"
    assert match.matched_text.lower() == "fox news"
    assert match.key_sequence == ["KEYCODE_CHANNEL_UP"]


def test_evaluate_rules_no_match():
    rules = [
        ChannelRule(
            id=1,
            name="Block Fox News",
            enabled=True,
            target_packages=["com.google.android.youtube.tvunplugged"],
            patterns=[r"fox\s*news"],
            action="auto_skip",
        )
    ]

    meta = MediaMetadata(
        package="com.google.android.youtube.tvunplugged",
        is_playing=True,
        title="ESPN Live: NBA Basketball",
        subtitle="Game 5",
    )

    match = evaluate_rules(rules, meta, active_pkg="com.google.android.youtube.tvunplugged")
    assert match is None


def test_evaluate_rules_disabled():
    rules = [
        ChannelRule(
            id=1,
            name="Block Fox News",
            enabled=False,
            target_packages=["com.google.android.youtube.tvunplugged"],
            patterns=[r"fox\s*news"],
            action="auto_skip",
        )
    ]

    meta = MediaMetadata(
        package="com.google.android.youtube.tvunplugged",
        is_playing=True,
        title="Fox News",
    )

    match = evaluate_rules(rules, meta, active_pkg="com.google.android.youtube.tvunplugged")
    assert match is None
