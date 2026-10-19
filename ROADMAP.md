# Roadmap

Every item serves GOAL.md: which dependencies stop you moving to a new Python, and what fixes them.

## v0.1 — shipped (2026-10-13)
- CLI: uv.lock, pylock.toml, poetry.lock, Pipfile.lock, requirements*.txt (with `-r`), pyproject.toml, or specs
- CPython targets incl. free-threaded (`3.15t`) and PEP 803 `abi3` / `abi3t` / `abi3.abi3t`
- Platforms: manylinux (glibc floor), musllinux, macOS arm64/x86_64 (version floor), Windows amd64/arm64/x86
- Statuses: ready · bump (with first fixing version) · blocked · excluded · build · skip · missing
- Text / JSON / Markdown / GitHub annotations, `--summary` for `$GITHUB_STEP_SUMMARY`
- GitHub Action with `ripe` / `blocked` / `bump` outputs
- MCP stdio server: `check_python_upgrade`, `explain_wheel`
- CI cross-checks every verdict against pip's own resolver on the real PyPI, and installs on real CPython 3.15 / 3.15t

## v0.2 — in progress
- ✅ PEP 691 / PEP 503 simple-API indexes — shipped on main, not yet released
  - `-i/--index-url` (replaces PyPI) and repeatable `--extra-index-url`, files pooled across indexes like pip
  - read from requirements files (`-i`, `--extra-index-url`, through `-r`, `${VAR}` expansion), per-package registries in uv.lock / poetry.lock / pylock.toml / Pipfile.lock, and `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `UV_DEFAULT_INDEX` / `UV_INDEX_URL` / `UV_INDEX`
  - JSON + HTML parsing (yanked, requires-python, URL-decoding, hash fragments), versions from filenames, PEP 440 local versions (`2.14.0+cpu`) matched like pip
  - Basic auth from the URL or `~/.netrc`, same-host only, never printed; clear errors for 401/403/404, login pages and non-simple answers
  - Action inputs `index-url` / `extra-index-url`; MCP `index_url` / `extra_index_urls`
  - CI cross-checks the verdicts against pip on the live download.pytorch.org index (3.15, 3.15t)
  - Not yet: `--find-links` / flat directories, `--no-index`, `file://` indexes, keyring credentials, uv's `first-index` strategy (wheelripe pools like pip; for uv.lock it uses each package's locked registry)
- Upstream tracking: link the open "Python 3.15 wheels" issue/PR in each blocked package's repo
- `wheelripe watch`: a scheduled-workflow recipe that comments on your tracking issue when a blocker clears
- On-disk cache with ETag revalidation

## v0.3
- `--fix` that prints the exact `uv lock --upgrade-package` / `pip-compile -P` commands for every BUMP
- Transitive explanation for requirements.txt inputs (which top-level dependency pulls a blocker in)

## v1.0
- Stable JSON schema, documented exit codes, PyPI release with Trusted Publishing
