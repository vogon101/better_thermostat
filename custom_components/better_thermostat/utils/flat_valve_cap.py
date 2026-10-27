"""FLAT: a per-TRV maximum valve opening that Home Assistant sets at runtime.

For a TRV ``climate.<obj>``, the entity ``input_number.<obj>_max_valve``
(0-100) caps that TRV's valve opening. A missing, unavailable or non-numeric
entity means no cap. The cap combines with Better Thermostat's own per-TRV
maximum-opening number entity: the lower of the two wins.

In a group of TRVs under one Better Thermostat, the opening a capped TRV
cannot take is handed to the other TRVs that still have room, so the room
gets roughly the same total heat. The ratio of what was applied to what was
commanded is what the controllers learn from (see ``apply_flat_caps``).
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

FLAT_CAP_DOMAIN = "input_number"
FLAT_CAP_SUFFIX = "_max_valve"


def flat_cap_entity_id(trv_entity_id: str) -> str:
    """Return ``input_number.<obj>_max_valve`` for ``climate.<obj>``."""
    obj = str(trv_entity_id).split(".", 1)[-1]
    return f"{FLAT_CAP_DOMAIN}.{obj}{FLAT_CAP_SUFFIX}"


def read_flat_cap(hass: Any, trv_entity_id: str) -> int | None:
    """Return the TRV's cap in whole percent, or None when there is none."""
    if hass is None:
        return None
    try:
        state = hass.states.get(flat_cap_entity_id(trv_entity_id))
    except Exception:
        return None
    raw = getattr(state, "state", None)
    # Home Assistant states are strings; anything else (a test double) is no cap.
    if not isinstance(raw, str):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return int(max(0, min(100, round(value))))


def flat_cap_active(hass: Any, trv_entity_id: str) -> bool:
    """Whether the TRV has a FLAT cap below 100 %."""
    cap = read_flat_cap(hass, trv_entity_id)
    return cap is not None and cap < 100


def trv_valve_limit(bt: Any, trv_entity_id: str) -> int:
    """Return the TRV's effective max opening: BT's own number and the FLAT cap."""
    limit = 100.0
    trv = (getattr(bt, "real_trvs", None) or {}).get(trv_entity_id)
    own = getattr(trv, "valve_max_opening", None)
    if isinstance(own, (int, float)) and not isinstance(own, bool):
        if math.isfinite(float(own)):
            limit = min(limit, max(0.0, float(own)))
    cap = read_flat_cap(getattr(bt, "hass", None), trv_entity_id)
    if cap is not None:
        limit = min(limit, float(cap))
    return int(math.floor(limit))


def cap_and_redistribute(
    commanded: Mapping[str, float], limits: Mapping[str, float]
) -> tuple[dict[str, float], float]:
    """Clamp each TRV to its limit and hand the excess to the TRVs with room.

    The excess (what the capped TRVs were asked for above their limit) is
    shared among the TRVs still below their limit in proportion to their
    headroom, so none of them passes its own limit. When the headroom is too
    small the rest is lost.

    Returns the applied openings and the ratio ``sum(applied) / sum(commanded)``
    (1.0 when the excess fitted or nothing was commanded).
    """
    applied: dict[str, float] = {}
    excess = 0.0
    for eid, raw_pct in commanded.items():
        lim = float(limits.get(eid, 100.0))
        pct = float(raw_pct)
        if pct > lim:
            excess += pct - lim
            applied[eid] = lim
        else:
            applied[eid] = pct

    if excess > 0.0:
        headroom = {
            eid: float(limits.get(eid, 100.0)) - pct
            for eid, pct in applied.items()
            if float(limits.get(eid, 100.0)) - pct > 0.0
        }
        total = sum(headroom.values())
        if total > 0.0:
            share = min(1.0, excess / total)
            for eid, room in headroom.items():
                applied[eid] += room * share

    total_cmd = sum(float(v) for v in commanded.values())
    if total_cmd <= 0.0:
        return applied, 1.0
    ratio = sum(applied.values()) / total_cmd
    return applied, max(0.0, min(1.0, ratio))


def apply_flat_caps(
    bt: Any, commanded: Mapping[str, float]
) -> tuple[dict[str, float], float, dict[str, int]]:
    """Cap and redistribute ``commanded`` with each TRV's effective limit.

    Returns ``(applied, ratio, limits)``. ``ratio`` scales the group command
    to the room's effective heating input: the controllers learn from
    ``group_command * ratio``, which equals the command whenever the excess
    found room on the other TRVs.
    """
    limits = {eid: trv_valve_limit(bt, eid) for eid in commanded}
    applied, ratio = cap_and_redistribute(commanded, limits)
    return applied, ratio, limits
