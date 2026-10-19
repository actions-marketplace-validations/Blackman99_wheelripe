"""Read dependency lists from lockfiles, requirements files, pyproject.toml or CLI specs."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


@dataclass
class Dep:
    name: str
    version: Optional[str] = None  # exact pin, or None
    specifier: str = ""  # used when not pinned
    marker: Optional[str] = None
    origin: str = ""
    index: Optional[str] = None  # registry the lockfile says this package came from (None = default)

    @property
    def key(self) -> str:
        return canonicalize_name(self.name)


class InputError(Exception):
    pass


@dataclass
class IndexOptions:
    """Index options found inside requirements files (pip applies them to the whole run)."""
    index_url: Optional[str] = None  # last -i / --index-url wins, as in pip
    extra_index_urls: List[str] = field(default_factory=list)


_PYPI_SIMPLE = re.compile(r"^https?://(www\.)?pypi\.(org|python\.org)/simple/?$", re.I)


def _registry(url: Optional[str]) -> Optional[str]:
    """A lockfile's registry URL, or None when it is PyPI (the default index)."""
    if not url or _PYPI_SIMPLE.match(url.strip()):
        return None
    return url.strip()


def _expand(text: str) -> str:
    # pip expands only ${UPPER_CASE_NAMES} in requirements files; unknown ones stay as written
    return re.sub(r"\$\{([A-Z0-9_]+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), text)


_INDEX_FLAGS = {"-i": "index", "--index-url": "index", "--extra-index-url": "extra"}


def _index_option(line: str, opts: IndexOptions) -> bool:
    """Record -i / --index-url / --extra-index-url from a requirements option line."""
    try:
        toks = shlex.split(line)
    except ValueError:
        toks = line.split()
    hit = False
    i = 0
    while i < len(toks):
        t, kind, val = toks[i], None, None
        if t in _INDEX_FLAGS:  # "-i URL", "--index-url URL"
            kind, val = _INDEX_FLAGS[t], toks[i + 1] if i + 1 < len(toks) else None
            i += 1
        elif t.split("=", 1)[0] in ("--index-url", "--extra-index-url") and "=" in t:  # "--index-url=URL"
            flag, val = t.split("=", 1)
            kind = _INDEX_FLAGS[flag]
        elif t.startswith("-i") and not t.startswith("--") and len(t) > 2:  # "-iURL"
            kind, val = "index", t[2:]
        if kind and val:
            hit = True
            if kind == "extra":
                opts.extra_index_urls.append(_expand(val))
            else:
                opts.index_url = _expand(val)
        i += 1
    return hit


AUTODETECT = ("uv.lock", "pylock.toml", "poetry.lock", "Pipfile.lock", "requirements.txt", "pyproject.toml")


def _pin(req: Requirement) -> Optional[str]:
    specs = list(req.specifier)
    if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
        return specs[0].version
    return None


def from_spec(text: str, origin: str = "cli") -> Dep:
    try:
        req = Requirement(text)
    except InvalidRequirement as e:
        raise InputError(f"not a file and not a requirement: {text!r} ({e})") from None
    return Dep(req.name, _pin(req), str(req.specifier), str(req.marker) if req.marker else None, origin)


def parse_requirements(path: Path, _seen: Optional[set] = None, opts: Optional[IndexOptions] = None) -> List[Dep]:
    seen = _seen if _seen is not None else set()
    opts = opts if opts is not None else IndexOptions()
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    deps: List[Dep] = []
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\\\r?\n", " ", text)  # line continuations
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        m = re.match(r"^(-r|--requirement)\s*=?\s*(\S+)", line)
        if m:
            deps += parse_requirements(path.parent / m.group(2), seen, opts)
            continue
        if line.startswith("-"):  # -i / --extra-index-url are recorded; -c, -e, -f ... are skipped
            _index_option(line, opts)
            continue
        if re.match(r"^(\w+\+)?(https?|file|git)\S*://", line) or line.startswith((".", "/")):
            continue
        line = re.split(r"\s+--hash", line)[0].strip()
        line = re.split(r"\s+--\w", line)[0].strip()
        try:
            req = Requirement(line)
        except InvalidRequirement:
            continue
        if req.url:
            continue
        deps.append(Dep(req.name, _pin(req), str(req.specifier),
                        str(req.marker) if req.marker else None, path.name))
    return deps


def _load_toml(path: Path) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def parse_uv_lock(path: Path) -> List[Dep]:
    data = _load_toml(path)
    deps = []
    for pkg in data.get("package", []):
        src = pkg.get("source", {})
        if "registry" not in src:
            continue  # editable / virtual / git / path / url
        deps.append(Dep(pkg["name"], pkg.get("version"), origin=path.name, index=_registry(src["registry"])))
    return deps


def parse_pylock(path: Path) -> List[Dep]:
    data = _load_toml(path)
    deps = []
    for pkg in data.get("packages", []):
        if any(k in pkg for k in ("vcs", "directory", "archive")):
            continue
        if not pkg.get("version"):
            continue
        index = pkg.get("index") if isinstance(pkg.get("index"), str) else None
        deps.append(Dep(pkg["name"], pkg["version"], marker=pkg.get("marker"), origin=path.name,
                        index=_registry(index)))
    return deps


def parse_poetry_lock(path: Path) -> List[Dep]:
    data = _load_toml(path)
    deps = []
    for pkg in data.get("package", []):
        src = pkg.get("source") or {}
        if src.get("type") in ("git", "directory", "file", "url"):
            continue
        index = src.get("url") if src.get("type") == "legacy" else None  # a [[tool.poetry.source]]
        deps.append(Dep(pkg["name"], pkg.get("version"), origin=path.name, index=_registry(index)))
    return deps


def parse_pipfile_lock(path: Path) -> List[Dep]:
    data = json.loads(path.read_text(encoding="utf-8"))
    # [[source]] entries; packages name theirs with "index", the rest use the first source.
    # Pipenv expands ${VAR} in source URLs (that is how credentials get in).
    srcs = [s for s in ((data.get("_meta") or {}).get("sources") or []) if isinstance(s, dict) and s.get("url")]
    by_name = {s.get("name"): os.path.expandvars(s["url"]) for s in srcs}
    first = os.path.expandvars(srcs[0]["url"]) if srcs else None
    deps = []
    for section in ("default", "develop"):
        for name, meta in (data.get(section) or {}).items():
            ver = meta.get("version", "")
            if not ver.startswith("=="):
                continue
            index = by_name.get(meta.get("index"), first) if meta.get("index") else first
            deps.append(Dep(name, ver[2:], marker=meta.get("markers"), origin=f"{path.name}:{section}",
                            index=_registry(index)))
    return deps


def parse_pyproject(path: Path) -> List[Dep]:
    data = _load_toml(path)
    project = data.get("project", {})
    reqs = [(r, "dependencies") for r in project.get("dependencies", [])]
    for extra, items in (project.get("optional-dependencies") or {}).items():
        reqs += [(r, f"extra:{extra}") for r in items]
    for group, items in (data.get("dependency-groups") or {}).items():
        reqs += [(r, f"group:{group}") for r in items if isinstance(r, str)]
    deps = []
    for text, where in reqs:
        try:
            req = Requirement(text)
        except InvalidRequirement:
            continue
        if req.url:
            continue
        deps.append(Dep(req.name, _pin(req), str(req.specifier),
                        str(req.marker) if req.marker else None, f"pyproject.toml:{where}"))
    return deps


def parse_file(path: Path, opts: Optional[IndexOptions] = None) -> List[Dep]:
    name = path.name.lower()
    if name == "uv.lock":
        return parse_uv_lock(path)
    if name == "poetry.lock":
        return parse_poetry_lock(path)
    if name == "pipfile.lock":
        return parse_pipfile_lock(path)
    if name == "pyproject.toml":
        return parse_pyproject(path)
    if name == "pylock.toml" or re.fullmatch(r"pylock\.[^.]+\.toml", name):
        return parse_pylock(path)
    return parse_requirements(path, opts=opts)


def collect(args: List[str], opts: Optional[IndexOptions] = None) -> tuple:
    """Turn CLI positionals into (deps, sources). Directory -> autodetect the best file.

    Index options found in requirements files are recorded into ``opts`` when given."""
    if not args:
        args = ["."]
    deps: List[Dep] = []
    sources: List[str] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            for cand in AUTODETECT:
                if (p / cand).is_file():
                    deps += parse_file(p / cand, opts)
                    sources.append(str(p / cand))
                    break
            else:
                raise InputError(f"no {', '.join(AUTODETECT)} found in {p}")
        elif p.is_file():
            deps += parse_file(p, opts)
            sources.append(str(p))
        elif os.sep in a or a.endswith((".txt", ".lock", ".toml")):
            raise InputError(f"file not found: {a}")
        else:
            deps.append(from_spec(a))
            sources.append("cli")
    # de-duplicate by (name, version); keep first origin
    uniq = {}
    for d in deps:
        uniq.setdefault((d.key, d.version, d.specifier, d.index), d)
    return list(uniq.values()), sources
