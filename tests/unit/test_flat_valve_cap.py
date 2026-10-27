"""FLAT: per-TRV max valve opening from ``input_number.<trv>_max_valve``.

Covers the cap itself, the redistribution of a capped TRV's excess to the
other TRVs of the group, that MPC learning sees the applied (capped) input,
and that a missing helper changes nothing.
"""

import asyncio
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from homeassistant.components.climate.const import PRESET_BOOST, HVACMode
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.better_thermostat.calibration import (
    _compute_mpc_balance,
    _compute_mpc_v2_balance,
)
from custom_components.better_thermostat.trv import Trv
from custom_components.better_thermostat.utils.calibration.mpc import (
    MpcInput,
    MpcParams,
    MpcState,
    build_mpc_group_key,
    compute_mpc,
    distribute_valve_percent,
)
from custom_components.better_thermostat.utils.const import (
    CalibrationMode,
    CalibrationType,
)
from custom_components.better_thermostat.utils.controlling import control_trv
from custom_components.better_thermostat.utils.flat_valve_cap import (
    apply_flat_caps,
    cap_and_redistribute,
    flat_cap_entity_id,
    read_flat_cap,
    trv_valve_limit,
)

TRVS = ("climate.trv_1", "climate.trv_2", "climate.trv_3")


def _hass_with(caps: dict[str, object]) -> MagicMock:
    """Return a hass whose states hold ``input_number.<trv>_max_valve`` values."""
    states = {
        flat_cap_entity_id(trv): SimpleNamespace(state=str(value))
        for trv, value in caps.items()
    }
    hass = MagicMock()
    hass.states.get.side_effect = states.get
    return hass


# ---------------------------------------------------------------------------
# Reading the cap
# ---------------------------------------------------------------------------
class TestReadCap:
    def test_entity_id_follows_the_trv(self):
        assert flat_cap_entity_id("climate.bedroom_trv_1") == (
            "input_number.bedroom_trv_1_max_valve"
        )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("40", 40), ("40.0", 40), ("0", 0), ("150", 100), ("-5", 0)],
    )
    def test_numeric_state_is_the_cap(self, raw, expected):
        hass = _hass_with({"climate.trv_1": raw})
        assert read_flat_cap(hass, "climate.trv_1") == expected

    @pytest.mark.parametrize("raw", ["unavailable", "unknown", "nan", "", "abc"])
    def test_unusable_state_is_no_cap(self, raw):
        hass = _hass_with({"climate.trv_1": raw})
        assert read_flat_cap(hass, "climate.trv_1") is None

    def test_missing_entity_is_no_cap(self):
        hass = _hass_with({})
        assert read_flat_cap(hass, "climate.trv_1") is None
        assert read_flat_cap(None, "climate.trv_1") is None

    def test_non_string_state_is_no_cap(self):
        """A bare mock (float() of it is 1.0) must not read as a 1 % cap."""
        hass = MagicMock()
        assert read_flat_cap(hass, "climate.trv_1") is None

    def test_limit_takes_the_lower_of_bt_number_and_cap(self):
        trv = Trv.from_legacy_dict("climate.trv_1", {"advanced": {}})
        bt = SimpleNamespace(real_trvs={"climate.trv_1": trv}, hass=_hass_with({}))
        assert trv_valve_limit(bt, "climate.trv_1") == 100
        bt.hass = _hass_with({"climate.trv_1": "40"})
        assert trv_valve_limit(bt, "climate.trv_1") == 40
        trv.valve_max_opening = 30.0
        assert trv_valve_limit(bt, "climate.trv_1") == 30


# ---------------------------------------------------------------------------
# Clamping and redistribution
# ---------------------------------------------------------------------------
class TestCapAndRedistribute:
    def test_cap_clamps_the_capped_trv(self):
        applied, _ = cap_and_redistribute({"a": 70.0, "b": 70.0}, {"a": 40, "b": 100})
        assert applied["a"] == pytest.approx(40.0)

    def test_excess_moves_to_the_other_trvs(self):
        cmd = {"a": 60.0, "b": 60.0, "c": 60.0}
        applied, ratio = cap_and_redistribute(cmd, {"a": 40, "b": 100, "c": 100})
        assert applied["a"] == pytest.approx(40.0)
        assert applied["b"] == pytest.approx(70.0)
        assert applied["c"] == pytest.approx(70.0)
        assert sum(applied.values()) == pytest.approx(sum(cmd.values()))
        assert ratio == pytest.approx(1.0)

    def test_excess_shared_by_headroom(self):
        """A TRV that is already further open takes a smaller share."""
        applied, ratio = cap_and_redistribute(
            {"a": 80.0, "b": 50.0, "c": 90.0}, {"a": 40, "b": 100, "c": 100}
        )
        # excess 40 over headroom 50 + 10: b gets 40*50/60, c 40*10/60
        assert applied["b"] == pytest.approx(50.0 + 40.0 * 50 / 60)
        assert applied["c"] == pytest.approx(90.0 + 40.0 * 10 / 60)
        assert max(applied.values()) <= 100.0
        assert ratio == pytest.approx(1.0)

    def test_excess_beyond_headroom_is_lost_and_shows_in_ratio(self):
        cmd = {"a": 100.0, "b": 90.0, "c": 90.0}
        applied, ratio = cap_and_redistribute(cmd, {"a": 40, "b": 100, "c": 100})
        assert applied == {"a": 40.0, "b": 100.0, "c": 100.0}
        assert ratio == pytest.approx(240.0 / 280.0)

    def test_no_cap_changes_nothing(self):
        cmd = {"a": 55.0, "b": 70.0}
        applied, ratio = cap_and_redistribute(cmd, {"a": 100, "b": 100})
        assert applied == cmd
        assert ratio == 1.0

    def test_zero_command(self):
        applied, ratio = cap_and_redistribute({"a": 0.0, "b": 0.0}, {"a": 0, "b": 50})
        assert applied == {"a": 0.0, "b": 0.0}
        assert ratio == 1.0

    def test_cap_of_zero_closes_the_trv(self):
        applied, ratio = cap_and_redistribute(
            {"a": 30.0, "b": 30.0}, {"a": 0, "b": 100}
        )
        assert applied == {"a": 0.0, "b": 60.0}
        assert ratio == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# MPC calibration (group of three TRVs)
# ---------------------------------------------------------------------------
class _StateMgr:
    def __init__(self) -> None:
        self.mpc: dict[str, MpcState] = {}

    @property
    def state(self):
        return self

    def get_mpc(self, key):
        return self.mpc.setdefault(key, MpcState())

    def set_mpc(self, key, mpc):
        self.mpc[key] = mpc


def _make_group_bt(caps: dict[str, object]) -> MagicMock:
    bt = MagicMock()
    bt.device_name = "Bedroom"
    bt.unique_id = "uid"
    bt.bt_target_temp = 21.0
    bt.cur_temp = 20.3  # a command of about 60 %
    bt.cur_temp_filtered = None
    bt.temp_slope = 0.0
    bt.tolerance = 0.0
    bt.contact_open = False
    bt.bt_hvac_mode = "heat"
    bt.outdoor_sensor = None
    bt.weather_entity = None
    bt.hass = _hass_with(caps)
    bt.real_trvs = {
        eid: Trv.from_legacy_dict(
            eid,
            {
                "advanced": {},
                "current_temperature": 20.0,
                "min_temp": 5.0,
                "max_temp": 30.0,
            },
        )
        for eid in TRVS
    }
    bt.state_mgr = _StateMgr()
    return bt


def _valves(bt) -> dict[str, int]:
    for eid in TRVS:
        _compute_mpc_balance(bt, eid)
    return {eid: bt.real_trvs[eid].calibration_balance["valve_percent"] for eid in TRVS}


class TestMpcBalance:
    def test_missing_entity_is_no_cap(self):
        bt = _make_group_bt({})
        valves = _valves(bt)
        group = bt.real_trvs[TRVS[0]].calibration_balance["debug"]["group_valve_pct"]
        assert valves == dict.fromkeys(TRVS, round(group))
        state = bt.state_mgr.mpc[build_mpc_group_key(bt)]
        assert state.flat_effective_ratio == 1.0

    def test_cap_clamps_and_others_make_up(self):
        uncapped = _valves(_make_group_bt({}))
        group = uncapped[TRVS[0]]
        assert 40 < group < 100, "the scenario needs a command above the cap"

        bt = _make_group_bt({TRVS[0]: "40"})
        valves = _valves(bt)
        assert valves[TRVS[0]] == 40
        for eid in TRVS[1:]:
            assert valves[eid] > uncapped[eid]
            assert valves[eid] <= 100
        assert sum(valves.values()) == pytest.approx(sum(uncapped.values()), abs=2)
        debug = bt.real_trvs[TRVS[0]].calibration_balance["debug"]
        assert debug["flat_max_valve"] == 40

    def test_unmet_shortfall_reaches_the_learning_ratio(self):
        bt = _make_group_bt({TRVS[0]: "0", TRVS[1]: "0"})
        valves = _valves(bt)
        group = bt.real_trvs[TRVS[2]].calibration_balance["debug"]["group_valve_pct"]
        assert valves[TRVS[0]] == valves[TRVS[1]] == 0
        assert valves[TRVS[2]] == 100
        state = bt.state_mgr.mpc[build_mpc_group_key(bt)]
        assert state.flat_effective_ratio == pytest.approx(
            min(1.0, 100.0 / (3 * group)), rel=1e-3
        )

    def test_capped_trv_marks_its_mpc_input(self):
        bt = _make_group_bt({TRVS[0]: "40"})
        seen = []
        real = compute_mpc

        def spy(inp, *args, **kwargs):
            seen.append((inp.entity_id, inp.flat_trv_capped))
            return real(inp, *args, **kwargs)

        with patch("custom_components.better_thermostat.calibration.compute_mpc", spy):
            _valves(bt)
        assert dict(seen) == {TRVS[0]: True, TRVS[1]: False, TRVS[2]: False}

    def test_mpc_v2_cap_clamps_and_redistributes(self):
        pytest.importorskip("numpy")
        from custom_components.better_thermostat.utils.state_manager import StateManager

        def run(caps):
            bt = _make_group_bt(caps)
            bt.heating_power = None
            bt.heat_loss_rate = None
            bt.state_mgr = StateManager(MagicMock(), "entry")
            for eid in TRVS:
                _compute_mpc_v2_balance(bt, eid)
            return {
                eid: bt.real_trvs[eid].calibration_balance["valve_percent"]
                for eid in TRVS
            }

        uncapped = run({})
        if uncapped[TRVS[0]] <= 40:
            pytest.skip("v2 command below the cap in this scenario")
        valves = run({TRVS[0]: "40"})
        assert valves[TRVS[0]] == 40
        assert valves[TRVS[1]] >= uncapped[TRVS[1]]


# ---------------------------------------------------------------------------
# Learning sees the applied input
# ---------------------------------------------------------------------------
class TestLearningUsesEffectiveValve:
    def _inp(self) -> MpcInput:
        return MpcInput(
            key="uid:group:t21.0",
            target_temp_C=21.0,
            current_temp_C=20.0,
            trv_temp_C=20.0,
            bt_name="t",
            entity_id=TRVS[0],
        )

    def test_integral_uses_the_applied_ratio(self):
        state = MpcState()
        state.last_percent = 80.0
        state.flat_effective_ratio = 0.5
        state.last_integration_ts = time() - 100.0
        compute_mpc(self._inp(), MpcParams(), state=state, all_states={})
        avg = state.u_integral / state.time_integral
        assert avg == pytest.approx(40.0, rel=0.01)

    def test_uncapped_integral_is_the_command(self):
        state = MpcState()
        state.last_percent = 80.0
        state.last_integration_ts = time() - 100.0
        compute_mpc(self._inp(), MpcParams(), state=state, all_states={})
        assert state.u_integral / state.time_integral == pytest.approx(80.0, rel=0.01)

    def test_gain_learning_sees_effective_u(self):
        """The identification step reports the effective, not commanded, u."""
        state = MpcState()
        now = time()
        state.last_percent = 80.0
        state.flat_effective_ratio = 0.5
        state.last_integration_ts = now - 600.0
        state.last_learn_time = now - 600.0
        state.last_learn_temp = 19.5
        state.last_target_C = 21.0
        out, state = compute_mpc(self._inp(), MpcParams(), state=state, all_states={})
        assert out is not None
        u_last = out.debug.get("id_u_last")
        if u_last is None:
            pytest.skip("identification did not run in this scenario")
        assert u_last == pytest.approx(0.40, abs=0.01)

    def test_ratio_resets_when_the_window_opens(self):
        state = MpcState()
        state.last_percent = 80.0
        state.flat_effective_ratio = 0.5
        inp = self._inp()
        inp.window_open = True
        compute_mpc(inp, MpcParams(), state=state, all_states={})
        assert state.flat_effective_ratio == 1.0

    def test_ratio_survives_persistence(self):
        from dataclasses import asdict

        from custom_components.better_thermostat.utils.state_manager import (
            deserialize_mpc,
        )

        state = MpcState()
        state.flat_effective_ratio = 0.75
        assert deserialize_mpc(asdict(state)).flat_effective_ratio == 0.75
        assert deserialize_mpc({}).flat_effective_ratio == 1.0


def test_distribute_then_cap_keeps_total():
    """End to end on the pure helpers: same total heat while the others have room."""
    temps = {TRVS[0]: 22.0, TRVS[1]: 20.0, TRVS[2]: 21.0}
    distributed = distribute_valve_percent(50.0, temps)
    bt = SimpleNamespace(
        real_trvs={eid: Trv.from_legacy_dict(eid, {"advanced": {}}) for eid in TRVS},
        hass=_hass_with({TRVS[1]: "40"}),
    )
    applied, ratio, limits = apply_flat_caps(bt, distributed)
    assert limits[TRVS[1]] == 40
    assert applied[TRVS[1]] == 40
    assert sum(applied.values()) == pytest.approx(sum(distributed.values()))
    assert ratio == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# The write path: boost (and non-MPC modes) are clamped before set_valve
# ---------------------------------------------------------------------------
_CTRL = "custom_components.better_thermostat.utils.controlling"


@pytest.mark.asyncio
async def test_written_valve_never_exceeds_the_cap():
    trv = Trv.from_legacy_dict(
        "climate.trv_1",
        {
            "hvac_modes": [HVACMode.HEAT, HVACMode.OFF],
            "min_temp": 5.0,
            "max_temp": 30.0,
            "temperature": 20.0,
            "last_temperature": 20.0,
            "last_hvac_mode": HVACMode.HEAT,
            "hvac_mode": HVACMode.HEAT,
            "advanced": {
                "calibration_mode": CalibrationMode.MPC_CALIBRATION,
                "calibration": CalibrationType.DIRECT_VALVE_BASED,
                "no_off_system_mode": False,
            },
        },
    )
    trv_state = Mock(state=HVACMode.HEAT, attributes={"temperature": 20.0})
    cap_state = SimpleNamespace(state="40")
    hass = Mock()
    hass.states.get.side_effect = lambda eid: (
        cap_state if eid == "input_number.trv_1_max_valve" else trv_state
    )
    hass.services.async_call = AsyncMock()
    bt = Mock()
    bt.hass = hass
    bt.device_name = "t"
    bt._temp_lock = asyncio.Lock()
    bt.calculate_heating_power = AsyncMock()
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.window_open = bt.door_open = bt.contact_open = False
    bt.call_for_heat = True
    bt.cooler_entity_id = None
    bt.preset_mode = PRESET_BOOST  # boost asks for 100 %
    bt.cur_temp = 18.0
    bt.bt_target_temp = 22.0
    bt.context = None
    bt.ignore_states = False
    bt.task_manager = Mock(create_task=Mock(side_effect=lambda c, **k: c.close()))
    bt.real_trvs = {"climate.trv_1": trv}

    registry = MagicMock(spec=er.EntityRegistry)
    registry.async_get.return_value = None
    set_valve = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.better_thermostat.utils.helpers.er.async_get",
            return_value=registry,
        ),
        patch(
            f"{_CTRL}.convert_outbound_states",
            return_value={"temperature": 20.0, "system_mode": HVACMode.HEAT},
        ),
        patch(f"{_CTRL}.set_valve", set_valve),
        patch(f"{_CTRL}.handle_contact_open", return_value=HVACMode.HEAT),
        patch(f"{_CTRL}.override_set_hvac_mode", new=AsyncMock(return_value=False)),
        patch(f"{_CTRL}.override_set_temperature", new=AsyncMock(return_value=False)),
        patch(f"{_CTRL}.set_hvac_mode", new=AsyncMock()),
        patch(f"{_CTRL}.set_temperature", new=AsyncMock()),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await control_trv(bt, "climate.trv_1")

    set_valve.assert_called_once()
    assert set_valve.call_args[0][2] == 40
