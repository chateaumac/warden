"""Unit tests for device state machine and power lifecycle."""

from app.guard.state import DeviceState, GuardState


def test_device_state_intervals():
    state = GuardState(device_id=1, state=DeviceState.OFFLINE)
    assert state.get_poll_interval(1.0) == 20.0

    state.state = DeviceState.STANDBY
    assert state.get_poll_interval(1.0) == 10.0

    state.state = DeviceState.IDLE
    assert state.get_poll_interval(1.0) == 2.5

    state.state = DeviceState.MONITORING
    assert state.get_poll_interval(1.2) == 1.2


def test_snooze_functionality():
    state = GuardState(device_id=1)
    assert state.is_snoozed is False

    state.snooze(duration_s=300)
    assert state.is_snoozed is True
    assert state.snooze_remaining_s > 0
    assert state.get_poll_interval(1.0) == 15.0

    state.unsnooze()
    assert state.is_snoozed is False
    assert state.snooze_remaining_s == 0
