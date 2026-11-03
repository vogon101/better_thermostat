"""FLAT: valve maintenance quiet hours from input_datetime helpers."""

from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util
import pytest

from custom_components.better_thermostat.utils.flat_quiet_hours import (
    flat_quiet_entity_ids,
    outside_quiet_hours,
    quiet_until,
    read_quiet_window,
)

# pytest-homeassistant pins its own default zone; build local times in it.
LOCAL = dt_util.get_default_time_zone()


class _States:
    def __init__(self, values):
        self._values = values

    def get(self, entity_id):
        if entity_id not in self._values:
            return None
        return SimpleNamespace(state=self._values[entity_id])


def _hass(values):
    return SimpleNamespace(states=_States(values))


def _bedroom(start="22:00:00", end="09:00:00"):
    s, e = flat_quiet_entity_ids("climate.bedroom")
    return _hass({s: start, e: end})


def test_entity_ids():
    assert flat_quiet_entity_ids("climate.bedroom") == (
        "input_datetime.bedroom_valve_maintenance_quiet_start",
        "input_datetime.bedroom_valve_maintenance_quiet_end",
    )


def test_missing_or_bad_helpers_mean_no_window():
    assert read_quiet_window(_hass({}), "climate.bedroom") is None
    assert read_quiet_window(_bedroom("unavailable"), "climate.bedroom") is None
    assert (
        read_quiet_window(_bedroom("09:00:00", "09:00:00"), "climate.bedroom") is None
    )
    assert read_quiet_window(None, "climate.bedroom") is None
    assert read_quiet_window(_bedroom(), None) is None


def test_other_room_unaffected():
    when = datetime(2026, 11, 2, 23, 0, tzinfo=LOCAL)
    assert outside_quiet_hours(_bedroom(), "climate.middle_room", when) == when


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        # evening, inside: to 09:00 next day
        (datetime(2026, 11, 2, 22, 16), datetime(2026, 11, 3, 9, 0)),
        (datetime(2026, 11, 2, 22, 0), datetime(2026, 11, 3, 9, 0)),
        # after midnight, inside: to 09:00 the same day
        (datetime(2026, 11, 3, 3, 30), datetime(2026, 11, 3, 9, 0)),
        # outside
        (datetime(2026, 11, 3, 9, 0), None),
        (datetime(2026, 11, 3, 14, 18), None),
        (datetime(2026, 11, 3, 21, 59), None),
    ],
)
def test_window_crossing_midnight(when, expected):
    window = (time(22, 0), time(9, 0))
    got = quiet_until(window, when.replace(tzinfo=LOCAL))
    if expected is None:
        assert got is None
    else:
        assert got == expected.replace(tzinfo=LOCAL)


def test_window_within_a_day():
    window = (time(13, 0), time(15, 0))
    assert quiet_until(window, datetime(2026, 11, 3, 14, 0, tzinfo=LOCAL)) == datetime(
        2026, 11, 3, 15, 0, tzinfo=LOCAL
    )
    assert quiet_until(window, datetime(2026, 11, 3, 15, 0, tzinfo=LOCAL)) is None


def test_utc_input_uses_local_time():
    local = datetime(2026, 7, 1, 23, 30, tzinfo=LOCAL)
    when = local.astimezone(ZoneInfo("UTC"))
    got = quiet_until((time(22, 0), time(9, 0)), when)
    assert got == datetime(2026, 7, 2, 9, 0, tzinfo=LOCAL)


def test_outside_quiet_hours_reads_helpers():
    when = datetime(2026, 11, 2, 22, 16, tzinfo=LOCAL)
    assert outside_quiet_hours(_bedroom(), "climate.bedroom", when) == datetime(
        2026, 11, 3, 9, 0, tzinfo=LOCAL
    )
