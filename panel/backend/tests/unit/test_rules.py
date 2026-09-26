from datetime import UTC, date, datetime, timedelta

from app.domain.rules import (
    UserState,
    after_expiry_change,
    expiry_instant,
    is_config_active,
    is_expired,
    traffic_delta,
    user_can_unblock,
    user_status,
)

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
ACTIVE = UserState(blocked_by=None, deleting_at=None, expires_at=None)


def test_config_of_active_user_is_active():
    assert is_config_active(None, None, ACTIVE, NOW)


def test_config_blocked_by_user_or_admin_is_inactive():
    assert not is_config_active("user", None, ACTIVE, NOW)
    assert not is_config_active("admin", None, ACTIVE, NOW)


def test_deleted_config_is_inactive():
    assert not is_config_active(None, NOW, ACTIVE, NOW)


def test_config_of_blocked_or_deleting_user_is_inactive():
    assert not is_config_active(None, None, UserState("admin", None, None), NOW)
    assert not is_config_active(None, None, UserState("expiry", None, None), NOW)
    assert not is_config_active(None, None, UserState(None, NOW, None), NOW)


def test_expiry_is_inclusive_at_the_instant():
    assert not is_config_active(None, None, UserState(None, None, NOW), NOW)
    assert is_config_active(None, None, UserState(None, None, NOW + timedelta(seconds=1)), NOW)


def test_orphan_config_is_active_unless_blocked_or_deleted():
    assert is_config_active(None, None, None, NOW)
    assert not is_config_active("admin", None, None, NOW)
    assert not is_config_active(None, NOW, None, NOW)


def test_user_can_unblock_only_own_block():
    assert user_can_unblock("user")
    assert not user_can_unblock("admin")
    assert not user_can_unblock(None)


def test_expiry_instant_is_next_midnight_in_panel_tz():
    assert expiry_instant(date(2026, 9, 30), "Europe/Moscow") == datetime(2026, 9, 30, 21, tzinfo=UTC)
    assert expiry_instant(date(2026, 9, 30), "UTC") == datetime(2026, 10, 1, tzinfo=UTC)


def test_is_expired():
    assert is_expired(NOW, NOW)
    assert not is_expired(None, NOW)
    assert not is_expired(NOW + timedelta(days=1), NOW)


def test_extending_expiry_lifts_expiry_block_only():
    assert after_expiry_change("expiry", NOW + timedelta(days=1), NOW) is None
    assert after_expiry_change("expiry", None, NOW) is None
    assert after_expiry_change("expiry", NOW - timedelta(days=1), NOW) == "expiry"
    assert after_expiry_change("admin", NOW + timedelta(days=1), NOW) == "admin"
    assert after_expiry_change(None, NOW + timedelta(days=1), NOW) is None


def test_setting_past_expiry_on_active_user_blocks_immediately():
    assert after_expiry_change(None, NOW - timedelta(days=1), NOW) == "expiry"


def test_user_status():
    assert user_status(ACTIVE, NOW) == "active"
    assert user_status(UserState("admin", None, None), NOW) == "blocked"
    assert user_status(UserState("expiry", None, NOW), NOW) == "expired"
    assert user_status(UserState(None, None, NOW), NOW) == "expired"
    assert user_status(UserState(None, NOW, None), NOW) == "deleting"


def test_traffic_delta_first_sample_counts_fully():
    assert traffic_delta(None, None, 100, None) == 100


def test_traffic_delta_grows():
    assert traffic_delta(100, None, 150, None) == 50


def test_traffic_delta_counter_reset_counts_new_value():
    assert traffic_delta(150, None, 20, None) == 20


def test_traffic_delta_new_session_counts_new_value():
    assert traffic_delta(150, "s1", 170, "s2") == 170
    assert traffic_delta(150, "s1", 170, "s1") == 20
