# Retired scripts - 2026-09-19 cleanup

Not part of the daily pipeline and not referenced by any live entry
point. Kept as evidence for figures quoted in DATA_AUDIT.md,
STRIDE_SYSTEM.md, ODDS_API_FINDING.md and README.md.

## Groups

* `research_backtests/` - analyses behind the documented ROI / stride
  numbers (cross-referenced from the .md files above).
* `oneoff_probes/` - single-purpose exploratory probes (schema checks,
  duplicate hunting, key matching) run once during development.

## Running them again

They import live modules from `scripts\`, so put that on the import
path first (PowerShell):

```
$env:PYTHONPATH = 'E:\Test\racing-form-system\scripts'
python archive\2026-09-19_cleanup\research_backtests\stride_combined.py
```

Sibling imports keep working because each group stays in one folder.

## Deleting permanently

Once you are satisfied nothing is needed:

```
Remove-Item -Recurse -Force 'E:\Test\racing-form-system\archive\2026-09-19_cleanup'
```
