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

## Sync
`flat-sync.yaml` checks upstream daily. A new release becomes a PR (`sync/<tag>`) with test results;
merging it publishes `<tag>-flat.1` (`flat-release.yaml`) and HACS offers the update. Install it from
HACS, then restart HA. A conflict opens an issue instead.
`tests/test_release_metadata.py` fails on upstream 1.9.3 itself (pyproject says 1.9.2), so it's skipped.
