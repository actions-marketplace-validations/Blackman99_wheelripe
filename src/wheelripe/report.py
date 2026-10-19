"""Render a Report as text, JSON, Markdown or GitHub workflow commands."""

from __future__ import annotations

import json
import os
import sys
from typing import List

from .check import BLOCKED, BUILD, BUMP, EXCLUDED, MISSING, READY, SKIP, Report, Result

LABEL = {READY: "READY", SKIP: "SKIP", BUILD: "BUILD", BUMP: "BUMP", BLOCKED: "BLOCKED",
         EXCLUDED: "EXCLUDED", MISSING: "MISSING"}
COLOR = {READY: "32", SKIP: "90", BUILD: "33", BUMP: "36", BLOCKED: "31;1", EXCLUDED: "31;1", MISSING: "35"}


def _use_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


def _ships(r: Result, python: str = "") -> str:
    if not r.ships:
        return ""
    nodot = python[:-1].replace(".", "")
    if python.endswith("t") and f"cp{nodot}" in r.ships and f"cp{nodot}t" not in r.ships:
        return f"has a GIL-only cp{nodot} wheel, no free-threaded one"

    def span(tags):
        return tags[0] if tags[0] == tags[-1] else f"{tags[0]}–{tags[-1]}"

    gil = [s for s in r.ships if not s.endswith("t")]
    ft = [s for s in r.ships if s.endswith("t")]
    if python.endswith("t") and ft:  # free-threaded target: show both builds, e.g. cp311–cp315 + cp314t–cp315t
        return f"ships {span(gil)} + {span(ft)}" if gil else f"ships {span(ft)}"
    return f"ships {span(gil or r.ships)}"


def explain(r: Result, platforms: List[str], python: str = "") -> str:
    tag = "cp" + python.replace(".", "") if python else "a"
    bad = {k: p for k, p in r.platforms.items() if p.status not in (READY, SKIP)}
    good = [k for k, p in r.platforms.items() if p.status == READY]
    if r.status == MISSING:
        return r.note
    if r.status == READY:
        kinds = sorted({p.kind for p in r.platforms.values() if p.kind})
        return ("wheel: " + ", ".join(kinds) if kinds else "wheel available") + _yanked(r)
    if r.status == SKIP:
        return "marker excludes every target"
    where = "any target platform" if len(bad) == len(platforms) else ", ".join(bad)
    if r.status == BUMP:
        extra = _ships(r, python)
        scope = "" if len(bad) == len(platforms) else f" for {where}"
        msg = (f"no {tag} wheel in {r.version}{scope}" + (f" ({extra})" if extra else "")
               + f"; {r.fix} is the first release with one")
    elif r.status == BUILD:
        msg = f"sdist only on {where}: pip will build it from source (fine if pure Python)"
    elif r.status == EXCLUDED:
        msg = f"requires-python {r.requires_python} excludes it"
        if r.fix:
            msg += f"; {r.fix} allows it"
    else:
        hard = {k: p for k, p in bad.items() if p.status in (BLOCKED, EXCLUDED, MISSING)}
        soft = {k: p for k, p in bad.items() if p.status == BUMP}
        hw = "any target platform" if len(hard) == len(platforms) else ", ".join(hard)
        extra = _ships(r, python)
        msg = f"no {tag} wheel for {hw}" + (f" ({extra})" if extra else "")
        hints = sorted({p.note for p in hard.values() if p.note[:1].isdigit()})
        if r.pinned and r.latest and r.latest != r.version and not hints:
            msg += f"; latest {r.latest} has none either"
        if hints:
            msg += " · " + "; ".join(hints)
        if soft:
            fixes = sorted({p.fix for p in soft.values() if p.fix})
            msg += f" · {' / '.join(fixes)} fixes {', '.join(soft)}"
    if good and bad and len(good) < len(platforms):
        msg += f" · ready on {', '.join(good)}"
    return msg + _yanked(r)


def _yanked(r: Result) -> str:
    return f" · {r.version} is yanked (pip installs it only because it is pinned, with a warning)" if r.yanked else ""


def text(rep: Report, stream=None, show_ready: bool = False) -> str:
    stream = stream or sys.stdout
    color = _use_color(stream)

    def c(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if color else s

    lines = [c("1", f"wheelripe · CPython {rep.python} · {' '.join(rep.platforms)}")]
    src = ", ".join(dict.fromkeys(rep.sources)) if rep.sources else "-"
    via = f" · index {' + '.join(rep.indexes)}" if rep.indexes else ""
    lines.append(c("90", f"{src} · {len(rep.results)} package{'s' if len(rep.results) != 1 else ''}{via}"))
    lines.append("")
    def ver(r: Result) -> str:
        return f"{r.version} → {r.fix}" if r.status == BUMP and r.fix else (r.version or "")
    shown = [r for r in rep.results if show_ready or r.status not in (READY, SKIP)]
    width = max([len(f"{r.name} {ver(r)}") for r in shown] + [10])
    ready = [r for r in rep.results if r.status in (READY, SKIP)]
    for r in rep.results:
        if r.status in (READY, SKIP) and not show_ready:
            continue
        head = f"{r.name} {ver(r)}".ljust(width + 2)
        lines.append(f"{c(COLOR[r.status], LABEL[r.status].ljust(8))} {head} {explain(r, rep.platforms, rep.python)}")
        if r.status in (BLOCKED, EXCLUDED) and r.upstream:
            lines.append(c("90", f"{'':8} {'':{width + 2}} upstream: {r.upstream}"))
    if ready and not show_ready:
        kinds = {}
        for r in ready:
            for p in r.platforms.values():
                if p.kind:
                    kinds[r.name] = p.kind
        pure = sum(1 for k in kinds.values() if k == "pure")
        lines.append(f"{c(COLOR[READY], 'READY'.ljust(8))} {len(ready)} package{'s' if len(ready) != 1 else ''} "
                     f"({len(ready) - pure} with binary wheels · {pure} pure Python)")
    lines.append("")
    n = rep.counts()
    if not rep.failing("bump"):
        verdict = c("32;1", f"RIPE — every dependency installs from a wheel on CPython {rep.python}")
        if n[BUILD]:
            verdict += f" ({n[BUILD]} build from sdist)"
    else:
        parts = []
        if n[BLOCKED] + n[EXCLUDED]:
            parts.append(f"{n[BLOCKED] + n[EXCLUDED]} blocked upstream")
        if n[BUMP]:
            parts.append(f"{n[BUMP]} need{'s' if n[BUMP] == 1 else ''} a version bump")
        if n[BUILD]:
            parts.append(f"{n[BUILD]} build{'s' if n[BUILD] == 1 else ''} from sdist")
        if n[MISSING]:
            parts.append(f"{n[MISSING]} not found")
        verdict = c("31;1", f"NOT RIPE for CPython {rep.python}: " + " · ".join(parts))
    lines.append(verdict)
    return "\n".join(lines) + "\n"


def to_json(rep: Report) -> str:
    return json.dumps(rep.to_dict(), indent=2) + "\n"


def markdown(rep: Report) -> str:
    n = rep.counts()
    ripe = not rep.failing("bump")
    out = [f"### wheelripe · CPython {rep.python}", ""]
    out.append(("**Ripe.** Every dependency installs from a wheel" if ripe else
                f"**Not ripe yet.** {n[BLOCKED] + n[EXCLUDED]} blocked upstream · {n[BUMP]} need a bump")
               + f" on {', '.join('`%s`' % p for p in rep.platforms)}.")
    if rep.indexes:
        out.append(f"Index: {' + '.join('`%s`' % i for i in rep.indexes)}.")
    out.append("")
    for w in rep.warnings:
        out += [f"> **Warning:** {w}", ""]
    rows = [r for r in rep.results if r.status not in (READY, SKIP)]
    if rows:
        out += ["| status | package | locked | fix | why |", "|---|---|---|---|---|"]
        for r in rows:
            name = f"[{r.name}]({r.upstream})" if r.upstream else r.name
            out.append(f"| {LABEL[r.status]} | {name} | {r.version or ''} | {r.fix or ''} | "
                       f"{explain(r, rep.platforms, rep.python)} |")
        out.append("")
    out.append(f"{n[READY]} ready · {n[BUILD]} sdist-only · {n[SKIP]} skipped by markers.")
    return "\n".join(out) + "\n"


def _gh(text: str) -> str:
    """Escape a workflow-command message (GitHub drops what follows a raw newline or %)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def github(rep: Report) -> str:
    out = []
    for r in rep.results:
        if r.status in (BLOCKED, EXCLUDED, MISSING):
            out.append(f"::error title=wheelripe {LABEL[r.status]} {r.name}::{_gh(explain(r, rep.platforms, rep.python))}")
        elif r.status in (BUMP, BUILD):
            out.append(f"::warning title=wheelripe {LABEL[r.status]} {r.name}::{_gh(explain(r, rep.platforms, rep.python))}")
    for w in rep.warnings:
        out.append(f"::warning title=wheelripe index::{_gh(w)}")
    return "\n".join(out) + ("\n" if out else "")
