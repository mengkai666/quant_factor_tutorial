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
