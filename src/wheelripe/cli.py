"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__, report
from .check import FAILING, run
from .index import make_fetcher, scrub
from .inputs import IndexOptions, InputError, collect
from .tags import DEFAULT_PLATFORMS, PLATFORM_KEYS, Python, explain_one, make_platform, parse_wheel_filename

DEFAULT_PYTHON = "3.15"


def _platforms(ns) -> list:
    keys: List[str] = []
    if ns.all_platforms:
        keys = list(PLATFORM_KEYS)
    for item in ns.platform or []:
        keys += [k for k in item.replace(" ", ",").split(",") if k]
    if not keys:
        keys = list(DEFAULT_PLATFORMS)
    seen, out = set(), []
    for k in keys:
        p = make_platform(k, glibc=ns.glibc, musl=ns.musl, macos=ns.macos)
        if p.key not in seen:
            seen.add(p.key)
            out.append(p)
    return out


def _common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("-p", "--python", default=os.environ.get("WHEELRIPE_PYTHON", DEFAULT_PYTHON),
                    help="target CPython, e.g. 3.15 or 3.15t for free-threaded (default %(default)s)")
    ap.add_argument("--platform", action="append", metavar="KEY",
                    help=f"target platform(s), repeat or comma-separate. Default: {','.join(DEFAULT_PLATFORMS)}. "
                         f"Known: {', '.join(PLATFORM_KEYS)}")
    ap.add_argument("--all-platforms", action="store_true", help="check every known platform")
    ap.add_argument("--glibc", help="oldest glibc you deploy on, for linux-* (default 2.34)")
    ap.add_argument("--musl", help="musl version for musllinux-* (default 1.2)")
    ap.add_argument("--macos", help="oldest macOS you support, for macos-* (default 14)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="wheelripe",
        description="Find the dependencies that have no wheel yet for the Python you want to move to, "
                    "and the version bump that fixes each one.",
        epilog="Subcommands: `wheelripe wheel FILE.whl` explains one wheel filename; "
               "`wheelripe mcp` runs the MCP stdio server.")
    ap.add_argument("inputs", nargs="*", metavar="PATH|SPEC",
                    help="uv.lock, pylock.toml, poetry.lock, Pipfile.lock, requirements*.txt, pyproject.toml, "
                         "a directory (auto-detect), or requirement specs like numpy==1.26.4. Default: .")
    _common(ap)
    ap.add_argument("-f", "--format", choices=["text", "json", "markdown", "github"], default="text")
    ap.add_argument("--fail-on", choices=sorted(FAILING), default="bump",
                    help="exit 1 when anything is at least this bad (default: bump)")
    ap.add_argument("--show-ready", action="store_true", help="list ready packages too (text format)")
    ap.add_argument("--pre", action="store_true", help="consider pre-releases for bumps")
    ap.add_argument("-i", "--index-url", metavar="URL",
                    help="simple-API index (PEP 691/503) to use instead of PyPI, e.g. "
                         "https://download.pytorch.org/whl/cpu. Credentials in the URL "
                         "(https://USER:TOKEN@host/simple) go to that host only and are never printed. "
                         "Overrides -i in requirements files, lockfile registries and "
                         "PIP_INDEX_URL / UV_DEFAULT_INDEX / UV_INDEX_URL")
    ap.add_argument("--extra-index-url", metavar="URL", action="append", default=[],
                    help="one more simple-API index (repeatable); files from all indexes are pooled, as pip "
                         "does. Also read from requirements files, PIP_EXTRA_INDEX_URL and UV_INDEX")
    ap.add_argument("--index", metavar="URL",
                    help="PyPI JSON API base for a mirror of pypi.org/pypi (default https://pypi.org/pypi, "
                         "env WHEELRIPE_INDEX)")
    ap.add_argument("--workers", type=int, default=8, help="parallel index requests (default 8)")
    ap.add_argument("-o", "--output", help="write the report to a file instead of stdout")
    ap.add_argument("--summary", help="also append a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--json-out", metavar="FILE", help="also write the JSON report to FILE")
    ap.add_argument("--annotations", action="store_true",
                    help="also print GitHub Actions ::error/::warning annotations")
    ap.add_argument("-V", "--version", action="version", version=f"wheelripe {__version__}")
    return ap


def cmd_wheel(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(prog="wheelripe wheel",
                                 description="Explain whether wheel filename(s) install on a target Python/platform.")
    ap.add_argument("files", nargs="+", metavar="FILE.whl")
    _common(ap)
    ns = ap.parse_args(argv)
    target = Python.parse(ns.python)
    plats = _platforms(ns)
    ok_all = True
    for f in ns.files:
        tags = parse_wheel_filename(f)
        if not tags:
            print(f"{f}: not a wheel filename", file=sys.stderr)
            ok_all = False
            continue
        print(f"{os.path.basename(f)} on CPython {target}")
        for p in plats:
            ok, why = explain_one(tags, target, p)
            ok_all &= ok
            print(f"  {'yes' if ok else 'no ':3}  {p.key:<18} {why}")
    return 0 if ok_all else 1


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "mcp":
        if any(a in ("-h", "--help") for a in argv[1:]):
            print("usage: wheelripe mcp\n\nRun the read-only MCP server on stdio (newline-delimited JSON-RPC).\n"
                  "Tools: check_python_upgrade, explain_wheel.")
            return 0
        from .mcp import serve
        return serve()
    if argv and argv[0] == "wheel":
        return cmd_wheel(argv[1:])
    if argv and argv[0] == "check":
        argv = argv[1:]
    ap = build_parser()
    ns = ap.parse_args(argv)
    if ns.index and ns.index_url:
        print("wheelripe: use --index-url (a simple-API index) or --index (a PyPI JSON API mirror), not both",
              file=sys.stderr)
        return 2
    opts = IndexOptions()
    try:
        target = Python.parse(ns.python)
        plats = _platforms(ns)
        deps, sources = collect(ns.inputs, opts)
        fetch = make_fetcher(ns.index_url, ns.extra_index_url, opts, ns.index)
    except (ValueError, InputError, OSError) as e:
        print(f"wheelripe: {scrub(e)}", file=sys.stderr)
        return 2
    if not deps:
        print("wheelripe: no dependencies found", file=sys.stderr)
        return 2
    try:
        rep = run(deps, fetch, target, plats, sources, pre=ns.pre, workers=ns.workers)
    except RuntimeError as e:
        print(f"wheelripe: {scrub(e)}", file=sys.stderr)
        return 2
    for w in rep.warnings:
        print(f"wheelripe: warning: {w}", file=sys.stderr)
    if ns.annotations:
        sys.stdout.write(report.github(rep))
    if ns.json_out:
        with open(ns.json_out, "w", encoding="utf-8") as fh:
            fh.write(report.to_json(rep))
    out_stream = open(ns.output, "w", encoding="utf-8") if ns.output else sys.stdout
    try:
        if ns.format == "json":
            out_stream.write(report.to_json(rep))
        elif ns.format == "markdown":
            out_stream.write(report.markdown(rep))
        elif ns.format == "github":
            out_stream.write(report.github(rep))
        else:
            out_stream.write(report.text(rep, stream=out_stream, show_ready=ns.show_ready))
    finally:
        if ns.output:
            out_stream.close()
    if ns.summary:
        with open(ns.summary, "a", encoding="utf-8") as fh:
            fh.write(report.markdown(rep))
    return 1 if rep.failing(ns.fail_on) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
