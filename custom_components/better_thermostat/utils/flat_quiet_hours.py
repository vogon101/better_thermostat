"""FLAT: quiet hours for valve maintenance, set from Home Assistant.

For a Better Thermostat ``climate.<obj>``, two time-only helpers
``input_datetime.<obj>_valve_maintenance_quiet_start`` and
``input_datetime.<obj>_valve_maintenance_quiet_end`` give a daily window in
which the valve exercise must not run (the motors are loud in a bedroom at
night). A window may cross midnight (22:00 to 09:00). A missing, unavailable
or unparsable helper, or start == end, means no quiet window.

A run that falls due inside the window is moved to the window's end. The
manual ``run_valve_maintenance`` service is not affected.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

FLAT_QUIET_DOMAIN = "input_datetime"
FLAT_QUIET_START_SUFFIX = "_valve_maintenance_quiet_start"
FLAT_QUIET_END_SUFFIX = "_valve_maintenance_quiet_end"


def flat_quiet_entity_ids(bt_entity_id: str) -> tuple[str, str]:
    """Return the (start, end) helper entity ids for ``climate.<obj>``."""
    obj = str(bt_entity_id).split(".", 1)[-1]
    return (
        f"{FLAT_QUIET_DOMAIN}.{obj}{FLAT_QUIET_START_SUFFIX}",
        f"{FLAT_QUIET_DOMAIN}.{obj}{FLAT_QUIET_END_SUFFIX}",
    )


def _read_time(hass: Any, entity_id: str) -> time | None:
    try:
        state = hass.states.get(entity_id)
    except Exception:
        return None
    raw = getattr(state, "state", None)
    if not isinstance(raw, str):
        return None
    try:
        return time.fromisoformat(raw)
    except ValueError:
        return None


def read_quiet_window(hass: Any, bt_entity_id: str | None) -> tuple[time, time] | None:
    """Return the (start, end) quiet window, or None when there is none."""
    if hass is None or not bt_entity_id:
        return None
    start_id, end_id = flat_quiet_entity_ids(bt_entity_id)
    start = _read_time(hass, start_id)
    end = _read_time(hass, end_id)
    if start is None or end is None or start == end:
        return None
    return start, end


def quiet_until(window: tuple[time, time] | None, when: datetime) -> datetime | None:
    """If ``when`` lies inside the window, return the window's end, else None."""
    if window is None or when is None:
        return None
    start, end = window
    local = dt_util.as_local(when)
    tod = local.time().replace(tzinfo=None)
    if start < end:
        inside = start <= tod < end
    else:  # crosses midnight
        inside = tod >= start or tod < end
    if not inside:
        return None
    end_dt = local.replace(
        hour=end.hour, minute=end.minute, second=end.second, microsecond=0
    )
    if end_dt <= local:
        end_dt += timedelta(days=1)
    return end_dt


def outside_quiet_hours(
    hass: Any, bt_entity_id: str | None, when: datetime
) -> datetime:
    """Return ``when``, or the end of the quiet window if it falls inside it."""
    later = quiet_until(read_quiet_window(hass, bt_entity_id), when)
    return later if later is not None else when
