<p align="center"><img src="site/logo.svg" width="96" alt="wheelripe logo: a cheese wedge with one open hole"></p>

# wheelripe

**Before you move to a new Python, find every dependency that has no wheel for it yet — and the version bump that fixes each one.**

Python 3.15 shipped on 2026-10-09. Moving a project means finding out, package by package, whether each locked dependency has a `cp315` (or `abi3`, or free-threaded `cp315t`/`abi3t`) wheel for *your* platforms. Today that means upgrading, watching `pip`/`uv` fall back to an sdist and fail on a missing compiler or header, fixing one, and repeating — or hand-checking the PyPI JSON API like [harlequin#1119](https://github.com/tconbeer/harlequin/issues/1119) and [dynamic-des#40](https://github.com/jaehyeon-kim/dynamic-des/issues/40) do. `pip install --only-binary=:all: --python-version 3.15` stops at the first failure and never tells you which newer release would work.

wheelripe reads your lockfile, asks the index for every release's file list, and applies the same tag rules pip does (PEP 425/600/656/803 — CI cross-checks every verdict against pip on the real PyPI) for every target platform at once.

![wheelripe checking a requirements file for CPython 3.15](site/demo.gif)

## Install

```bash
uvx --from git+https://github.com/Blackman99/wheelripe wheelripe          # one-off
pipx install git+https://github.com/Blackman99/wheelripe                   # or keep it
```

Not on PyPI yet; the [release](https://github.com/Blackman99/wheelripe/releases/latest) has a wheel you can `pip install` directly.

## Use

```bash
wheelripe                               # auto-detects uv.lock / pylock.toml / poetry.lock / requirements.txt / pyproject.toml
wheelripe uv.lock --python 3.15t        # free-threaded
wheelripe requirements.txt --platform linux-aarch64,musllinux-x86_64 --glibc 2.28
```

```
BLOCKED  confluent-kafka 2.16.0          no cp315 wheel for any target platform (ships cp38–cp314)
BUMP     numpy 1.26.4 → 2.5.2            no cp315 wheel in 1.26.4 (ships cp39–cp312); 2.5.2 is the first release with one
BUMP     pydantic_core 2.41.5 → 2.48.0   no cp315 wheel in 2.41.5 (ships cp39–cp314); 2.48.0 is the first release with one
READY    1 package (0 with binary wheels · 1 pure Python)

NOT RIPE for CPython 3.15: 1 blocked upstream · 2 need a version bump
```

### PyTorch, private mirrors and other indexes

wheelripe reads PEP 691 (JSON) and PEP 503 (HTML) simple indexes — PyTorch's download.pytorch.org, or a private one on Artifactory, Nexus, devpi or GitLab — with the options pip takes:

```bash
wheelripe torch==2.13.0 numpy==2.5.4 --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
```

```
cli · 2 packages · index download.pytorch.org/whl/cpu + pypi.org

BUMP     torch 2.13.0 → 2.14.0   no cp315 wheel in 2.13.0 for macos-arm64, windows-amd64 (ships cp310–cp315); 2.14.0 is the first release with one · ready on linux-x86_64
READY    1 package (1 with binary wheels · 0 pure Python)
```

- `-i/--index-url` replaces PyPI (which is then never asked about your package names); `--extra-index-url` adds more, and files from all of them are pooled, as pip does.
- `-i` / `--extra-index-url` lines in requirements files (including `-r` includes, with `${VAR}` expansion), the per-package registry in `uv.lock`, `poetry.lock`, `pylock.toml` and `Pipfile.lock`, and `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `UV_DEFAULT_INDEX` / `UV_INDEX` are honoured too. An explicit `--index-url` beats all of them.
- Local versions work like pip: `torch==2.14.0` matches `2.14.0+cpu` on Linux and plain `2.14.0` on macOS; `torch==2.14.0+cpu` matches only the `+cpu` builds. CI checks this against pip on the live PyTorch index.
- Credentials go in the URL (`https://USER:TOKEN@host/simple`) or `~/.netrc`. They are sent only to that host and never printed: every URL in output, warnings and errors is redacted.

Exit code is 1 when anything needs a bump or is blocked (`--fail-on blocked|build|never` to change). Also: `-f json|markdown|github`, a [GitHub Action](https://blackman99.github.io/wheelripe/docs/usage.html#github-action) (`uses: Blackman99/wheelripe@v0.1.0`), and an [MCP server](https://blackman99.github.io/wheelripe/docs/usage.html#mcp) (`wheelripe mcp`) so coding agents check before they bump.

Docs: https://blackman99.github.io/wheelripe/docs/ · [GOAL.md](GOAL.md) · [ROADMAP.md](ROADMAP.md) · MIT
