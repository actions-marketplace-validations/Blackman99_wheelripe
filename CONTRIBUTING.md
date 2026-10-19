# Contributing

Thanks for helping wheelripe stay small and correct.

1. Read [GOAL.md](./GOAL.md). Off-goal requests get a polite close with a pointer there.
2. Tag logic must match pip. If you change `src/wheelripe/tags.py`, run `python scripts/crosscheck_pip.py 3.15` and `3.15t` (real PyPI) and paste the summary line in the PR. If you change `src/wheelripe/index.py` or how `check.py` picks a release's files, also run `python scripts/crosscheck_index.py 3.15` and `3.15t` (download.pytorch.org).
3. `python -m pytest -q` must pass on 3.9 → 3.15t (CI runs all of them).
4. No new runtime dependencies beyond `packaging` (and `tomli` on old Pythons).
5. The landing page design lives in `site/DESIGN.md`; keep its language, respect `prefers-reduced-motion`.

## Dev

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
python -m pytest -q
wheelripe examples/requirements-blocked.txt
```
