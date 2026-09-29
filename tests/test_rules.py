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


FOX_RULE = ChannelRule(
    id=2,
    name="Block Fox News channels",
    channels=["FOX News", "FOX Business", "LiveNOW from FOX"],
    action="tune",
    tune_urls=["https://tv.youtube.com/watch/AW9-z6zluec"],
)


def live(title: str, channel: str) -> MediaMetadata:
    return MediaMetadata(
        package="com.google.android.youtube.tvunplugged",
        is_playing=True,
        playback_state="playing",
        title=title,
        subtitle=channel,
    )


def test_channel_rule_matches_exact_channels():
    for title, channel in [
        ("The Five", "FOX News"),
        ("The Evening Edit", "FOX Business"),
        ("LiveNOW from FOX", "LiveNOW from FOX"),
        ("The Five", "fox news"),  # case-insensitive
    ]:
        match = evaluate_rules([FOX_RULE], live(title, channel))
        assert match is not None, channel
        assert match.action == "tune"
        assert match.tune_urls == ["https://tv.youtube.com/watch/AW9-z6zluec"]
        assert match.matched_text == channel


def test_channel_rule_ignores_other_fox_channels_and_titles():
    for title, channel in [
        ("The Herd With Colin Cowherd", "FS1"),
        ("FOX News Sunday", "FOX 29"),  # Fox News program title on a local FOX station
        ("The Lead With Jake Tapper", "CNN"),
    ]:
        assert evaluate_rules([FOX_RULE], live(title, channel)) is None, channel
