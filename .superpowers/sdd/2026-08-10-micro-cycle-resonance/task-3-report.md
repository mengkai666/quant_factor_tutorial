# Task 3 Report: Strong Industries, Resonance, And Leaders

## Scope

Modified only the Task 3 calculation module, its tests, and this required report:

- `src/micro_cycle.py`
- `tests/test_micro_cycle.py`
- `.superpowers/sdd/2026-08-10-micro-cycle-resonance/task-3-report.md`

The pre-existing untracked `data/ai_output_cache/` directory was not changed or staged.

## TDD Evidence

### RED

Command:

```powershell
python -m pytest -q tests/test_micro_cycle.py -k "build_sector_return_table or cycle_resonance"
```

Result: 4 failed, 6 deselected.

The failures were the expected missing public interfaces:

- `ImportError: cannot import name 'build_sector_return_table' from 'micro_cycle'`
- `ImportError: cannot import name 'build_cycle_resonance' from 'micro_cycle'`

All Task 3 tests, including the empty sector-cache contract, were added before any Task 3 production code.

### GREEN

After the minimal implementation, the first focused run exposed two fixture-defined requirements:

1. Sorting rounded QFQ returns made `26.763%` and `26.760%` tie. The calculation now retains exact endpoint returns for ranking and rounds only values returned to callers.
2. A valid CLS-attributed singleton was being omitted if it had no sector evidence. It is now retained as `chain follower`, preserving the required source-priority and attribution-coverage result.

Focused command:

```powershell
python -m pytest -q tests/test_micro_cycle.py -k "build_sector_return_table or cycle_resonance"
```

Result: 4 passed, 6 deselected.

Required micro-cycle command:

```powershell
python -m pytest -q tests/test_micro_cycle.py
```

Result: 10 passed in 2.24s.

Required full-suite command:

```powershell
python -m pytest -q
```

Result: 314 passed in 12.04s.

## Implementation Summary

- `build_sector_return_table` calculates period and excess returns, sorts deterministically, and returns an empty DataFrame with the required columns for an empty cache.
- `build_cycle_resonance` separately exposes strong industries and confirmed mainlines, using the supplied mainline-to-industry aliases only as industry evidence.
- Attribution uses the latest valid CLS row for each code before considering Eastmoney. Invalid rows do not displace an earlier valid CLS attribution.
- Leader returns use only the exact signal and latest QFQ matrix endpoints. Coverage is calculated against the whole usable chain, and every numeric leader return is hidden below 80% endpoint coverage.

## Self-Review

- Verified strong industries cannot become mainlines merely by having high sector returns.
- Verified the source precedence fixture keeps the earlier valid CLS mapping despite a later invalid CLS row and a conflicting Eastmoney row.
- Verified missing matrix endpoints lower leader coverage and hide all leader return values rather than calculating from substituted dates.
- Verified empty sector cache behavior directly in a production-facing test.
- Ran `git diff --check`; it reported only existing line-ending warnings and no whitespace errors.

## Concerns

No blocking concerns. Mainline aliases are intentionally static, as specified by the Task 3 brief.

## Fix Round 1 of 5

### Scope and Covering Tests

Updated `tests/test_micro_cycle.py` and `src/micro_cycle.py` to resolve the two confirmed high findings.

- `test_cycle_resonance_omits_unconfirmed_singleton_mainline` proves that an attributed but unsupported one-stock theme is omitted from `mainlines`.
- `test_unknown_index_return_keeps_excess_return_unknown_and_cannot_confirm_industry` proves that `index_return=None` yields an unknown excess return and cannot create strong-industry evidence or promote a singleton mainline.
- `test_cycle_resonance_prefers_latest_valid_cls_and_keeps_unattributed_codes` now provides a linked `半导体` sector with known positive excess return. It verifies the earlier valid CLS `AI算力` mapping wins over a later invalid CLS row and conflicting Eastmoney row, qualifies as `次级共振`, leaves one cohort code unattributed, and reports 0.5 attribution coverage.

### RED

Command:

```powershell
python -m pytest -q tests/test_micro_cycle.py -k "unconfirmed_singleton or unknown_index_return"
```

Result: 2 failed, 10 deselected in 3.07s.

- The singleton test received one `连板跟随` record with `chain_count: 1` instead of an empty `mainlines` list.
- The unknown-index test received `np.float64(15.0)` for `excess_return` instead of `None`.

### GREEN

Focused command:

```powershell
python -m pytest -q tests/test_micro_cycle.py -k "unconfirmed_singleton or unknown_index_return or prefers_latest_valid_cls"
```

Result: 3 passed, 9 deselected in 2.08s.

Required micro-cycle command:

```powershell
python -m pytest -q tests/test_micro_cycle.py
```

Result: 12 passed in 2.16s.

Required full-suite command:

```powershell
python -m pytest -q
```

Result: 316 passed in 12.17s.

### Implementation and Self-Review

- `build_sector_return_table` now sets `excess_return` to `None` when the benchmark return is unknown instead of treating the benchmark as zero.
- The `连板跟随` branch again requires at least two cohort stocks and no industry confirmation. An unconfirmed singleton is skipped.
- The revised CLS-priority test confirms a singleton remains eligible only through linked strong-industry evidence, where it is classified as `次级共振`.
- The original report's singleton-follower conclusion is superseded by this user-confirmed global constraint.
- Verified that the new `None` propagates through numeric coercion to exclude the sector from strong-industry evidence.
- Verified that an attributed singleton still contributes to attribution coverage even when it is omitted from confirmed mainlines.
