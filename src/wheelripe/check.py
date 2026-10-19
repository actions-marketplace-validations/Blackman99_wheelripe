"""Classify every dependency for a target Python on each target platform."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence

from packaging.markers import InvalidMarker, Marker
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .inputs import Dep
from dataclasses import replace

from .tags import Platform, Python, binary_pythons, compatible, kind, parse_wheel_filename

# Status values, from best to worst.
READY = "ready"      # a compatible wheel exists for the locked/selected version
SKIP = "skip"        # environment marker excludes this platform/Python
BUILD = "build"      # release ships no wheels at all (sdist only) - builds from source
BUMP = "bump"        # locked version has no wheel; a newer release does
BLOCKED = "blocked"  # no release has a compatible wheel yet: waiting on upstream
EXCLUDED = "excluded"  # requires-python rules out the target and no release fixes it
MISSING = "missing"  # not found on the index / version not found

ORDER = [READY, SKIP, BUILD, BUMP, BLOCKED, EXCLUDED, MISSING]
FAILING = {"bump": {BUMP, BLOCKED, EXCLUDED, MISSING}, "blocked": {BLOCKED, EXCLUDED, MISSING},
           "build": {BUILD, BUMP, BLOCKED, EXCLUDED, MISSING}, "never": set()}


@dataclass
class PlatformResult:
    status: str
    wheel: Optional[str] = None  # matched wheel filename
    kind: Optional[str] = None  # cp315 / abi3 / abi3t / pure
    fix: Optional[str] = None  # first newer version that has a wheel here
    note: str = ""


@dataclass
class Result:
    name: str
    version: Optional[str]
    pinned: bool
    status: str
    platforms: Dict[str, PlatformResult] = field(default_factory=dict)
    fix: Optional[str] = None  # version that fixes every failing platform
    latest: Optional[str] = None
    ships: List[str] = field(default_factory=list)  # cp tags this release has wheels for
    requires_python: Optional[str] = None
    upstream: Optional[str] = None
    origin: str = ""
    note: str = ""
    yanked: Optional[bool] = None  # True when an exact pin selects a yanked release

    def to_dict(self) -> dict:
        d = asdict(self)
        d["platforms"] = {k: {kk: vv for kk, vv in asdict(v).items() if vv not in (None, "")}
                          for k, v in self.platforms.items()}
        return {k: v for k, v in d.items() if v not in (None, "", [])}


def worst(statuses: Sequence[str]) -> str:
    return max(statuses, key=ORDER.index) if statuses else READY


def _files(release: list) -> list:
    return [f for f in release if not f.get("yanked")]


def _requires_python(files: list) -> Optional[str]:
    for f in files:
        rp = f.get("requires_python")
        if rp:
            return rp
    return None


def _rp_ok(rp: Optional[str], target: Python) -> bool:
    if not rp:
        return True
    try:
        return SpecifierSet(rp).contains(target.full, prereleases=True)
    except InvalidSpecifier:
        return True


def _wheel_for(files: list, target: Python, plat: Platform) -> Optional[tuple]:
    best = None
    for f in files:
        tags = parse_wheel_filename(f.get("filename", ""))
        if tags and compatible(tags, target, plat):
            k = kind(tags, target)
            # prefer version-specific over abi3 over pure, purely for display
            rank = {"pure": 3, "py-none": 2, "abi3": 1, "abi3t": 1}.get(k, 0)
            if best is None or rank < best[2]:
                best = (f["filename"], k, rank)
    return best


def _floor_hint(rel: "_Releases", versions: list, target: Python, plat: Platform) -> str:
    """If wheels exist but only for a newer glibc/musl/macOS than the floor, say which."""
    if plat.os not in ("linux", "musllinux", "macos"):
        return ""
    relaxed = replace(plat, libc=(99, 99))
    need = None
    for v in reversed(versions[-5:]):
        for f in rel.files(v):
            tags = parse_wheel_filename(f.get("filename", ""))
            if not tags or not compatible(tags, target, relaxed):
                continue
            for pt in tags.platforms:
                m = re.fullmatch(r"(?:manylinux|musllinux|macosx)_(\d+)_(\d+)_\w+", pt)
                if m:
                    cand = (int(m.group(1)), int(m.group(2)))
                    need = cand if need is None or cand < need else need
        if need:
            what = {"linux": "glibc", "musllinux": "musl", "macos": "macOS"}[plat.os]
            fl = plat.libc
            fmt = (lambda t: f"{t[0]}") if plat.os == "macos" else (lambda t: f"{t[0]}.{t[1]}")
            flag = {"linux": "--glibc", "musllinux": "--musl", "macos": "--macos"}[plat.os]
            return (f"{v} has wheels, but only for {what} >= {fmt(need)} (your floor is {fmt(fl)}; "
                    f"raise {flag} if you deploy on newer)")
    return ""


def _upstream(info: dict) -> Optional[str]:
    urls = dict(info.get("project_urls") or {})
    if info.get("home_page"):
        urls.setdefault("Homepage", info["home_page"])
    prefer = ("source", "repository", "code", "github", "source code", "homepage", "home")
    items = sorted(urls.items(), key=lambda kv: next((i for i, p in enumerate(prefer)
                                                       if kv[0].lower() == p), 99))
    for _, url in items:
        m = re.match(r"https?://(github\.com|gitlab\.com|codeberg\.org)/([^/#?]+)/([^/#?]+)", url or "")
        if m:
            return f"https://{m.group(1)}/{m.group(2)}/{m.group(3).removesuffix('.git')}"
    return None


class _Releases:
    """Release files keyed by version, with PEP 440 ``==`` semantics for local versions.

    PyTorch-style indexes publish ``2.14.0+cpu`` for Linux/Windows and plain ``2.14.0`` for
    macOS. ``torch==2.14.0`` matches both (a public version ignores local labels), while
    ``torch==2.14.0+cpu`` matches only the ``+cpu`` files, so that is what pip installs.
    PyPI never has local versions, so there every group is exactly one release.
    """

    def __init__(self, releases: dict):
        self.exact: Dict[Version, list] = {}
        self.public: Dict[Version, list] = {}
        for k, files in releases.items():
            try:
                v = Version(k)
            except InvalidVersion:
                continue
            self.exact.setdefault(v, []).extend(files)
            self.public.setdefault(Version(v.public), []).extend(files)

    def has(self, v: Version) -> bool:
        return v in self.exact if v.local else Version(v.public) in self.public

    def files(self, v: Version, yanked: bool = False) -> list:
        """Files pip could pick for ``==v``; yanked ones only when asked for."""
        files = self.exact.get(v, []) if v.local else self.public.get(Version(v.public), [])
        return list(files) if yanked else _files(files)

    def versions(self, pre: bool) -> List[Version]:
        """Public versions with at least one non-yanked file, oldest first."""
        out = [v for v, files in self.public.items()
               if (pre or not (v.is_prerelease or v.is_devrelease)) and _files(files)]
        return sorted(out)


def classify(dep: Dep, project: Optional[dict], target: Python, plats: Sequence[Platform],
             pre: bool = False) -> Result:
    if project is None or project.get("missing"):
        note = (project or {}).get("missing") or "not found on the package index"
        return Result(dep.name, dep.version, dep.version is not None, MISSING, note=note, origin=dep.origin)
    info = project.get("info", {})
    rel = _Releases(project.get("releases", {}) or {})
    name = info.get("name") or dep.name
    pinned = dep.version is not None
    pin: Optional[Version] = None
    if pinned:
        try:
            pin = Version(dep.version)
        except InvalidVersion:
            return Result(name, dep.version, True, MISSING, origin=dep.origin,
                          note=f"{dep.version!r} is not a valid version (PEP 440)")
    versions = rel.versions(pre or bool(pin and pin.is_prerelease))
    latest = str(versions[-1]) if versions else info.get("version")

    if pinned:
        version = dep.version
        sel = pin
        if not rel.has(pin):
            note = f"version {version} not found on the index"
            if pin.local:
                note += (f" (+{pin.local} builds come from a non-PyPI index such as "
                         "download.pytorch.org; pass it with --index-url or --extra-index-url)")
            return Result(name, version, True, MISSING, latest=latest, note=note, origin=dep.origin)
    else:
        try:
            spec = SpecifierSet(dep.specifier or "")
        except InvalidSpecifier:
            spec = SpecifierSet("")
        ok = [v for v in versions if spec.contains(v, prereleases=pre)]
        if not ok:
            return Result(name, None, False, MISSING, latest=latest,
                          note=f"no release matches {dep.specifier!r}", origin=dep.origin)
        # pip would pick the newest release whose requires-python allows the target
        allowed = [v for v in ok if _rp_ok(_requires_python(rel.files(v)), target)]
        sel = (allowed or ok)[-1]
        version = str(sel)

    files = rel.files(sel)
    yanked = None
    if pinned and not files and rel.files(sel, yanked=True):
        # PEP 592: pip skips yanked files unless an exact pin leaves nothing else (it then warns)
        files, yanked = rel.files(sel, yanked=True), True
    rp = _requires_python(files)
    all_tags = [t for t in (parse_wheel_filename(f["filename"]) for f in files) if t]
    ships = binary_pythons(all_tags)
    has_wheels = bool(all_tags)
    res = Result(name, version, pinned, READY, latest=latest, ships=ships, requires_python=rp,
                 upstream=_upstream(info), origin=dep.origin, yanked=yanked)

    marker = None
    if dep.marker:
        try:
            marker = Marker(dep.marker)
        except InvalidMarker:
            marker = None

    # later public versions (a +cpu build and its plain macOS twin count as one release)
    newer = [v for v in versions if v > sel]
    for plat in plats:
        if marker is not None:
            try:
                if not marker.evaluate(plat.marker_env(target)):
                    res.platforms[plat.key] = PlatformResult(SKIP, note="environment marker excludes it")
                    continue
            except Exception:  # undefined marker vars -> evaluate as included
                pass
        if not _rp_ok(rp, target):
            pr = PlatformResult(EXCLUDED, note=f"requires-python {rp}")
        else:
            hit = _wheel_for(files, target, plat)
            if hit:
                res.platforms[plat.key] = PlatformResult(READY, wheel=hit[0], kind=hit[1])
                continue
            if not has_wheels:
                pr = PlatformResult(BUILD, note="sdist only: pip builds it from source")
            else:
                pr = PlatformResult(BLOCKED, note="no wheel for this Python/platform")
        # look for the first newer release that installs here from a wheel
        for v in newer:
            vf = rel.files(v)
            if not _rp_ok(_requires_python(vf), target):
                continue
            if _wheel_for(vf, target, plat):
                pr.fix = str(v)
                break
        if not pr.fix and pr.status == BLOCKED:
            hint = _floor_hint(rel, versions, target, plat)
            if hint:
                pr.note = hint
        if pr.fix and pinned:
            pr.status = BUMP
        elif pr.fix and not pinned:
            # an unpinned spec chose an older release (spec upper bound) - widening fixes it
            pr.status = BUMP
        res.platforms[plat.key] = pr

    statuses = [p.status for p in res.platforms.values()]
    res.status = worst([s for s in statuses if s != SKIP]) if any(s != SKIP for s in statuses) else SKIP
    failing = [p for p in res.platforms.values() if p.status in (BUMP, BLOCKED, EXCLUDED, BUILD)]
    if res.status == BUMP:
        fixes = [Version(p.fix) for p in failing if p.fix]
        res.fix = str(max(fixes)) if fixes else None
    if res.status in (BLOCKED, EXCLUDED, BUILD):
        fixes = [p.fix for p in failing if p.fix]
        if fixes:
            res.fix = str(max(Version(f) for f in fixes))
    return res


@dataclass
class Report:
    python: str
    platforms: List[str]
    sources: List[str]
    results: List[Result]
    indexes: List[str] = field(default_factory=list)  # redacted; empty = PyPI only
    warnings: List[str] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        c = {s: 0 for s in ORDER}
        for r in self.results:
            c[r.status] += 1
        return c

    def failing(self, fail_on: str = "bump") -> List[Result]:
        bad = FAILING[fail_on]
        return [r for r in self.results if r.status in bad]

    def to_dict(self) -> dict:
        c = self.counts()
        return {
            "tool": "wheelripe",
            "python": self.python,
            "platforms": self.platforms,
            "sources": self.sources,
            **({"indexes": self.indexes} if self.indexes else {}),
            "ripe": not self.failing("bump"),
            "summary": {k: v for k, v in c.items()},
            "packages": [r.to_dict() for r in self.results],
            **({"warnings": self.warnings} if self.warnings else {}),
        }


def run(deps: Sequence[Dep], fetch, target: Python, plats: Sequence[Platform], sources=(),
        pre: bool = False, workers: int = 8) -> Report:
    """``fetch(name)`` returns a project dict or None; deps that carry the registry their lockfile
    names (``Dep.index``) are fetched as ``fetch(name, index)``."""
    keys = sorted({(canonicalize_name(d.name), d.index or "") for d in deps})

    def get(key):
        name, index = key
        return fetch(name, index) if index else fetch(name)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        projects = dict(zip(keys, ex.map(get, keys)))
    results = [classify(d, projects.get((canonicalize_name(d.name), d.index or "")), target, plats, pre)
               for d in deps]
    results.sort(key=lambda r: (-ORDER.index(r.status), r.name.lower()))
    rep = Report(str(target), [p.key for p in plats], list(sources), results)
    if hasattr(fetch, "describe"):
        rep.indexes = list(fetch.describe())
        rep.warnings = list(fetch.warnings())
    return rep
