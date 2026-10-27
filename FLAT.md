# FLAT fork

Better Thermostat for Freddie's flat: upstream releases plus a few small patches, installed through HACS
from this repo (`vogon101/better_thermostat`, branch `flat`). Not sent upstream.

## Patches (search for `FLAT`)
- `model_fixes/TRVZB.py`: `_TRVZB_CLOSE_BUMP_OPEN_DELTA_PCT = 0` turns off the close bump (each close
  opened the valve +10 % first, then went to the target 5 s later: two motor moves). Upstream added it
  because TRVZB motors can lose calibration and not fully close at small openings.
- `utils/calibration/mpc.py`: `min_percent_hold_time_s` 900 (was 300), `big_change_force_close_pct` 25
  (was 10). Fewer re-plans while a room warms up.
- Tests pin the bump back to 10 for upstream's tests and add a bump-off test.
- Per-TRV valve cap from Home Assistant (`utils/flat_valve_cap.py`, tests in
  `tests/unit/test_flat_valve_cap.py`). For TRV `climate.<obj>`, a numeric
  `input_number.<obj>_max_valve` (0-100) caps that TRV's valve; missing or unavailable means no
  cap. It combines with BT's own max-opening number (the lower wins).
  - `calibration.py` (MPC v1 and v2): after the group command is distributed, a capped TRV's
    excess goes to the TRVs below their limit, in proportion to their headroom.
    `applied / commanded` across the group is stored as `flat_effective_ratio` in the MPC state.
    PID gets the combined limit as its `max_opening_pct`.
  - `utils/calibration/mpc.py`: learning (valve integral, gain/loss identification, perf curve)
    uses `last_percent * flat_effective_ratio`, the room's applied input. A TRV with an active cap
    skips the per-TRV dead-zone/profile evaluation. MPC v2 gets the effective input through
    `set_applied_u` when some excess found no room.
  - `utils/controlling.py`: the valve % written in a control cycle never exceeds the cap (covers
    boost, TPI and heating-power modes). Valve maintenance writes through the delegate directly
    and still runs 100 % / 0 %.
  - `climate.py`: listens to the `input_number`s and queues a control cycle on a change; the
    `max_valve` attribute lists each TRV's effective limit.

## Sync
`flat-sync.yaml` checks upstream daily. A new release becomes a PR (`sync/<tag>`) with test results;
merging it publishes `<tag>-flat.1` (`flat-release.yaml`) and HACS offers the update. Install it from
HACS, then restart HA. A conflict opens an issue instead.
`tests/test_release_metadata.py` fails on upstream 1.9.3 itself (pyproject says 1.9.2), so it's skipped.
