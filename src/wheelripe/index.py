"""Package indexes: the PyPI JSON API (default) and PEP 691 / PEP 503 simple-API indexes.

A simple index (PyTorch's download.pytorch.org, Artifactory, Nexus, devpi, GitLab, a
private mirror) only lists file names. Versions are read from those file names with
``packaging.utils`` exactly as pip does, and turned into the same ``{"info", "releases"}``
shape the PyPI JSON API returns, so the verdict logic in ``check.py`` is unchanged.

Like pip, files from every configured index are pooled (``--index-url`` plus each
``--extra-index-url``). Credentials embedded in a URL (``https://user:token@host/simple``)
are sent as HTTP Basic auth to that host only, and are never printed: every URL shown to
the user goes through :func:`redact`.
"""

from __future__ import annotations

import base64
import json
import netrc
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from .pypi import UA, PyPI

PYPI_SIMPLE = "https://pypi.org/simple"
ACCEPT = ("application/vnd.pypi.simple.v1+json, application/vnd.pypi.simple.v1+html;q=0.2, "
          "text/html;q=0.1")
# pip's list of archive formats it will consider as an sdist (pip._internal.utils.filetypes)
SDIST_EXTENSIONS = (".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tar.xz", ".txz", ".tar.lz", ".tlz",
                    ".tar.lzma", ".tar", ".zip")
ATTEMPTS = 3
BACKOFF = 0.6  # seconds, doubled per retry (tests set it to 0)
REASONS = {401: "401 Unauthorized", 403: "403 Forbidden", 404: "404 Not Found", 410: "410 Gone"}


class IndexFailure(RuntimeError):
    """The index answered in a way that makes any verdict unreliable (exit 2)."""


# ---------------------------------------------------------------------------
# URLs and credentials


def redact(url: str) -> str:
    """``https://user:token@host/x`` -> ``https://***@host/x``. Safe to print."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return scrub(url)
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc="***@" + parts.netloc.rpartition("@")[2]))


_USERINFO = re.compile(r"(?<=://)[^/\s@]+@")


def scrub(text: str) -> str:
    """Remove ``user:pass@`` from any URL inside free text (last line of defence)."""
    return _USERINFO.sub("***@", str(text))


def label(url: str) -> str:
    """Short, redacted form for reports: ``download.pytorch.org/whl/cpu``."""
    r = redact(url)
    return re.sub(r"^https?://", "", r).rstrip("/")


def _host(url: str) -> str:
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    return f"{host}:{p.port}" if p.port else host


def split_auth(url: str) -> Tuple[str, Optional[Tuple[str, str]]]:
    """Return (url without userinfo, (user, password) or None)."""
    p = urlsplit(url.strip())
    if "@" not in p.netloc:
        return urlunsplit(p), None
    userinfo, _, hostport = p.netloc.rpartition("@")
    user, _, pw = userinfo.partition(":")
    return urlunsplit(p._replace(netloc=hostport)), (unquote(user), unquote(pw))


def is_pypi(url: str) -> bool:
    clean, _ = split_auth(url)
    p = urlsplit(clean)
    return (p.hostname or "").lower() in ("pypi.org", "www.pypi.org", "pypi.python.org") and \
        p.path.rstrip("/") == "/simple"


class Auth:
    """Credentials per host, collected from every configured URL, then ~/.netrc (like pip)."""

    def __init__(self) -> None:
        self._by_host: Dict[str, Tuple[str, str]] = {}
        self._netrc_loaded = False
        self._netrc = None

    def add(self, url: str) -> str:
        clean, creds = split_auth(url)
        if creds:
            self._by_host.setdefault(_host(clean), creds)
        return clean

    def has(self, url: str) -> bool:
        return self.lookup(url) is not None

    def lookup(self, url: str) -> Optional[Tuple[str, str]]:
        host = _host(url)
        if host in self._by_host:
            return self._by_host[host]
        if not self._netrc_loaded:
            self._netrc_loaded = True
            try:
                self._netrc = netrc.netrc(os.environ.get("NETRC") or None)
            except Exception:  # missing, unreadable or malformed ~/.netrc
                self._netrc = None
        if self._netrc is not None:
            hit = self._netrc.authenticators(urlsplit(url).hostname or "")
            if hit:
                return (hit[0] or "", hit[2] or "")
        return None

    def header(self, url: str) -> Optional[str]:
        creds = self.lookup(url)
        if not creds:
            return None
        token = base64.b64encode(f"{creds[0]}:{creds[1]}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, re-sending Authorization only to the same scheme+host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        auth = req.unredirected_hdrs.get("Authorization")
        if new is not None and auth:
            a, b = urlsplit(req.full_url), urlsplit(newurl)
            if (a.scheme, _host(req.full_url)) == (b.scheme, _host(newurl)):
                new.add_unredirected_header("Authorization", auth)
        return new


_OPENER = urllib.request.build_opener(_SameHostRedirect())


# ---------------------------------------------------------------------------
# parsing simple pages


def dist_version(filename: str, project: str) -> Optional[Version]:
    """Version of a wheel or sdist of ``project``; None if it is neither (pip's rules)."""
    want = canonicalize_name(project)
    if filename.endswith(".whl"):
        try:
            name, version, _build, _tags = parse_wheel_filename(filename)
        except ValueError:  # InvalidWheelFilename / InvalidVersion: pip skips these too
            return None
        return version if canonicalize_name(name) == want else None
    low = filename.lower()
    ext = next((e for e in SDIST_EXTENSIONS if low.endswith(e)), None)
    if not ext:
        return None  # .egg, .exe, .msi, .rpm, .metadata ...: pip ignores them
    stem = filename[: -len(ext)]
    # pip's _extract_version_from_fragment: the version starts after the dash where the
    # prefix canonicalizes to the project name ("foo_bar-1.0" and "Foo.Bar-1.0" both work)
    for i, ch in enumerate(stem):
        if ch == "-" and canonicalize_name(stem[:i]) == want:
            try:
                return Version(stem[i + 1:])
            except InvalidVersion:
                return None
    return None


def _hashes(fragment: str) -> Dict[str, str]:
    out = {}
    for part in fragment.split("&"):
        k, _, v = part.partition("=")
        if k in ("sha256", "sha384", "sha512", "sha1", "md5") and v:
            out[k] = v
    return out


def _file(filename: str, yanked: bool, requires_python: Optional[str], hashes) -> dict:
    return {"filename": filename, "yanked": yanked, "requires_python": requires_python or None,
            "hashes": dict(hashes or {})}


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base: Optional[str] = None
        self.anchors: List[Dict[str, Optional[str]]] = []
        self.app_markup = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "base" and self.base is None and d.get("href"):
            self.base = d["href"]
        elif tag == "a" and d.get("href"):
            self.anchors.append(d)
        elif tag in ("script", "form", "input"):
            self.app_markup = True


class NotSimple(Exception):
    pass


def parse_html(text: str, page_url: str) -> List[dict]:
    """PEP 503 page -> files. Raises NotSimple if this is clearly some other HTML page."""
    p = _Anchors()
    p.feed(text)
    base = urljoin(page_url, p.base) if p.base else page_url
    files = []
    for a in p.anchors:
        url = urljoin(base, a["href"])
        parts = urlsplit(url)
        name = unquote(parts.path.rsplit("/", 1)[-1])
        if not name:
            continue
        # PEP 592: data-yanked present at all (even empty) means yanked; its value is the reason.
        # data-requires-python arrives already HTML-unescaped ("&gt;=3.9" -> ">=3.9").
        files.append(_file(name, "data-yanked" in a, a.get("data-requires-python"), _hashes(parts.fragment)))
    looks_like_dists = any(f["filename"].endswith(".whl") or f["filename"].lower().endswith(SDIST_EXTENSIONS)
                           for f in files)
    if not looks_like_dists and p.app_markup:
        raise NotSimple("html-app")
    return files


def parse_json(data, page_url: str) -> List[dict]:
    """PEP 691 page -> files."""
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise NotSimple("pypi-json" if isinstance(data, dict) and "releases" in data else "json")
    ver = str((data.get("meta") or {}).get("api-version", "1.0"))
    if ver.split(".")[0] != "1":
        raise IndexFailure(f"{redact(page_url)} speaks simple API version {ver}; wheelripe understands 1.x. "
                           "Please open an issue at https://github.com/Blackman99/wheelripe/issues")
    files = []
    for f in data["files"]:
        if not isinstance(f, dict) or not f.get("filename"):
            continue
        y = f.get("yanked", False)  # PEP 691: true, or a string with the reason
        files.append(_file(f["filename"], y is True or isinstance(y, str), f.get("requires-python"),
                           f.get("hashes")))
    return files


def to_project(project: str, files: Iterable[dict], info: Optional[dict] = None) -> dict:
    """Group files by the version in their filename, in the PyPI JSON API shape."""
    releases: Dict[str, List[dict]] = {}
    seen = set()
    for f in files:
        v = dist_version(f["filename"], project)
        if v is None or f["filename"] in seen:
            continue
        seen.add(f["filename"])
        releases.setdefault(str(v), []).append(f)
    return {"info": dict(info or {}), "releases": releases}


# ---------------------------------------------------------------------------
# sources


@dataclass
class Outcome:
    code: int  # 200, or 401 / 403 / 404 / 410
    project: Optional[dict] = None


class SimpleSource:
    def __init__(self, url: str, auth: Auth, timeout: float = 30.0):
        self.url = url.rstrip("/")
        self.auth = auth
        self.timeout = timeout
        self.label = label(self.url)
        self._memo: Dict[str, Outcome] = {}
        self._lock = threading.Lock()

    def has_credentials(self) -> bool:
        return self.auth.has(self.url)

    def get(self, name: str) -> Outcome:
        key = canonicalize_name(name)
        with self._lock:
            if key in self._memo:
                return self._memo[key]
        out = self._fetch(key)
        with self._lock:
            self._memo[key] = out
        return out

    def _fetch(self, project: str) -> Outcome:
        page = f"{self.url}/{project}/"
        shown = redact(page)
        req = urllib.request.Request(page, headers={"User-Agent": UA, "Accept": ACCEPT})
        hdr = self.auth.header(page)
        if hdr:
            req.add_unredirected_header("Authorization", hdr)
        last = ""
        for attempt in range(ATTEMPTS):
            if attempt:
                time.sleep(BACKOFF * 2 ** (attempt - 1))
            try:
                with _OPENER.open(req, timeout=self.timeout) as resp:
                    ctype = resp.headers.get_content_type()
                    charset = resp.headers.get_content_charset() or "utf-8"
                    body = resp.read()
                    final = resp.geturl()
                break
            except urllib.error.HTTPError as e:
                e.close()
                if e.code in REASONS:
                    return Outcome(e.code)
                last = f"HTTP {e.code} {e.reason}"
                if e.code < 500 and e.code != 429:
                    raise IndexFailure(f"{shown} answered {last}. Is {self.label} a simple index "
                                       "(the URL you would give pip -i)?") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = getattr(e, "reason", e)
                last = scrub(str(reason))
                if "CERTIFICATE_VERIFY_FAILED" in last:
                    raise IndexFailure(f"could not verify the TLS certificate of {self.label} ({last}). "
                                       "For a private CA, point SSL_CERT_FILE at its bundle.") from None
        else:
            raise IndexFailure(f"could not fetch {shown} after {ATTEMPTS} attempts: {last}")
        return Outcome(200, self._parse(project, ctype, charset, body, final))

    def _parse(self, project: str, ctype: str, charset: str, body: bytes, final: str) -> dict:
        shown = redact(final)
        fp = urlsplit(final)
        if (fp.hostname or "").endswith("pypi.org") and fp.path.startswith("/project/"):
            raise IndexFailure(f"{shown} is PyPI's website, not its simple index. Use "
                               f"https://{fp.hostname}/simple, or leave --index-url out: PyPI is the default.")
        hint = (f"Check that {self.label} is the simple API root you would give pip -i "
                "(it usually ends in /simple)")
        try:
            if ctype.endswith("+json") or ctype == "application/json":
                try:
                    data = json.loads(body.decode(charset, "replace"))
                except ValueError:
                    raise IndexFailure(f"{shown} sent invalid JSON. {hint}.") from None
                return to_project(project, parse_json(data, final))
            if ctype in ("text/html", "application/xhtml+xml") or ctype.endswith("+html"):
                return to_project(project, parse_html(body.decode(charset, "replace"), final))
        except NotSimple as e:
            if str(e) == "pypi-json":
                raise IndexFailure(f"{shown} looks like the PyPI JSON API, not a simple index: give a JSON API "
                                   "mirror with --index, or use its /simple URL with --index-url.") from None
            what = "a web page with no file links (a web UI or login page?)" if str(e) == "html-app" \
                else "JSON that is not a PEP 691 project page"
            raise IndexFailure(f"{shown} returned {what}. {hint}.") from None
        raise IndexFailure(f"{shown} returned {ctype}, not a simple index page (PEP 691 JSON or PEP 503 HTML). "
                           f"{hint}.")


class PyPIJsonSource:
    """The PyPI JSON API (``https://pypi.org/pypi/<name>/json``, or ``--index``)."""

    def __init__(self, client):
        self.client = client
        base = getattr(client, "index", "https://pypi.org/pypi")
        self.label = "pypi.org" if base.rstrip("/") == "https://pypi.org/pypi" else label(base)
        self.url = base

    def has_credentials(self) -> bool:
        return False

    def get(self, name: str) -> Outcome:
        data = self.client(name)
        return Outcome(200, data) if data is not None else Outcome(404)


def _norm_key(k: str) -> str:
    try:
        return str(Version(k))
    except InvalidVersion:
        return k


def merge(projects: Sequence[dict]) -> dict:
    """Pool files from several indexes (pip's behaviour): union per version, first info wins."""
    if len(projects) == 1:
        return projects[0]
    infos = [p.get("info") or {} for p in projects]
    info = next((i for i in infos if i.get("project_urls") or i.get("home_page")), None) or \
        next((i for i in infos if i), {})
    releases: Dict[str, List[dict]] = {}
    seen: Dict[str, set] = {}
    for p in projects:
        for k, files in (p.get("releases") or {}).items():
            nk = _norm_key(k)
            bucket = releases.setdefault(nk, [])
            names = seen.setdefault(nk, set())
            for f in files:
                if f.get("filename") not in names:
                    names.add(f.get("filename"))
                    bucket.append(f)
    return {"info": info, "releases": releases}


# ---------------------------------------------------------------------------
# configuration


@dataclass
class IndexConfig:
    """Where index URLs came from. CLI beats files beats environment beats PyPI."""
    index_url: Optional[str] = None          # --index-url
    extra_index_urls: List[str] = field(default_factory=list)  # --extra-index-url
    file_index_url: Optional[str] = None     # -i in requirements files (last one wins, like pip)
    file_extra_index_urls: List[str] = field(default_factory=list)
    json_index: Optional[str] = None         # --index (PyPI JSON API mirror)


def env_indexes(env=None) -> Tuple[Optional[str], List[str]]:
    env = os.environ if env is None else env
    primary = None
    for var in ("PIP_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX_URL"):
        if env.get(var, "").strip():
            primary = env[var].strip()
            break
    extras: List[str] = []
    for var in ("PIP_EXTRA_INDEX_URL", "UV_INDEX", "UV_EXTRA_INDEX_URL"):
        for item in env.get(var, "").split():
            # UV_INDEX entries may be "name=https://..."
            if "=" in item and "://" not in item.split("=", 1)[0]:
                item = item.split("=", 1)[1]
            extras.append(item)
    return primary, extras


def _check_url(url: str, where: str) -> str:
    clean, _ = split_auth(url)
    p = urlsplit(clean)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError(f"{where}: {redact(url)!r} is not an http(s) URL "
                         "(expected something like https://download.pytorch.org/whl/cpu)")
    return url


class Indexes:
    """The fetcher handed to ``check.run``: ``fetch(name)`` or ``fetch(name, source_url)``."""

    def __init__(self, config: Optional[IndexConfig] = None, env=None, timeout: float = 30.0, pypi_factory=None):
        cfg = config or IndexConfig()
        self.timeout = timeout
        self.auth = Auth()
        self._pypi_factory = pypi_factory or PyPI
        self._sources: Dict[str, object] = {}
        self._lock = threading.Lock()
        self._stats: Dict[str, Dict[int, set]] = {}
        self._used: Dict[str, object] = {}
        env_primary, env_extras = env_indexes(env)
        # --index-url overrides everything, including the registry a lockfile recorded per package
        self.explicit = bool(cfg.index_url)
        if cfg.index_url:
            primary = cfg.index_url
        elif cfg.json_index:
            primary = None  # --index (a PyPI JSON mirror) given on the command line beats files and env
        else:
            primary = cfg.file_index_url or env_primary
        extras = list(cfg.extra_index_urls) + list(cfg.file_extra_index_urls) + env_extras
        for where, u in [("--index-url", primary)] + [("--extra-index-url", e) for e in extras]:
            if u:
                _check_url(u, where)
                self.auth.add(u)
        self.json_index = cfg.json_index
        self.default: List[object] = []
        for u in [primary, *extras]:
            src = self._source(u)
            if src not in self.default:
                self.default.append(src)

    # -- sources -----------------------------------------------------------
    def _source(self, url: Optional[str]):
        if url is None or (is_pypi(url) and not self.json_index):
            key = "pypi-json"
            with self._lock:
                if key not in self._sources:
                    self._sources[key] = PyPIJsonSource(self._pypi_factory(self.json_index))
                return self._sources[key]
        clean = self.auth.add(url).rstrip("/")
        with self._lock:
            if clean not in self._sources:
                self._sources[clean] = SimpleSource(clean, self.auth, self.timeout)
            return self._sources[clean]

    def _for(self, source: Optional[str]) -> List[object]:
        if source is None or self.explicit:
            return self.default
        return [self._source(source)]

    # -- fetch ---------------------------------------------------------------
    def __call__(self, name: str, source: Optional[str] = None) -> Optional[dict]:
        srcs = self._for(source)
        found, misses = [], []
        for s in srcs:
            out = s.get(name)
            with self._lock:
                self._used.setdefault(s.label, s)
                self._stats.setdefault(s.label, {}).setdefault(out.code, set()).add(canonicalize_name(name))
            if out.code == 200 and out.project is not None:
                found.append(out.project)
            else:
                misses.append((s, out.code))
        if found:
            return merge(found)
        if len(srcs) == 1 and isinstance(srcs[0], PyPIJsonSource):
            return None  # classic message: "not found on the package index"
        where = " · ".join(f"{s.label} ({REASONS.get(c, c)})" for s, c in misses)
        note = f"not found on {where}"
        if srcs is self.default and not any(isinstance(s, PyPIJsonSource) or _host(s.url).endswith("pypi.org")
                                            for s in srcs):
            note += "; PyPI was not searched (--index-url replaces it: add --extra-index-url https://pypi.org/simple)"
        return {"missing": note, "info": {}, "releases": {}}

    # -- reporting -----------------------------------------------------------
    def describe(self) -> List[str]:
        """Indexes that were consulted, redacted; empty when only the default PyPI was used."""
        used = list(self._used.values())
        if all(isinstance(s, PyPIJsonSource) for s in used):
            return []
        return [s.label for s in used]

    def warnings(self) -> List[str]:
        out = []
        for lbl, codes in self._stats.items():
            src = self._used[lbl]
            total = sum(len(v) for v in codes.values())
            ok = len(codes.get(200, ()))

            def names(*cs):
                ns = sorted(set().union(*(codes.get(c, set()) for c in cs)))
                return ", ".join(ns[:4]) + (f" and {len(ns) - 4} more" if len(ns) > 4 else "")

            n401 = len(codes.get(401, ()))
            n403 = len(codes.get(403, ()))
            n404 = len(codes.get(404, ())) + len(codes.get(410, ()))
            host = _host(getattr(src, "url", "")) or lbl
            if n401:
                fix = (f"the credentials for {host} were rejected" if src.has_credentials() else
                       f"it needs credentials: put them in the URL "
                       f"({urlsplit(getattr(src, 'url', '')).scheme or 'https'}://USER:TOKEN@{host}/...) "
                       "or in ~/.netrc")
                out.append(f"{lbl} answered 401 Unauthorized for {names(401)}, so its files were not checked; {fix}.")
            if n403 and not ok and n403 == total - n401:
                out.append(f"{lbl} answered 403 Forbidden for every package asked ({names(403)}), so it added "
                           "no files. If it should serve them, check the credentials and the URL "
                           "(some indexes, like download.pytorch.org, answer 403 for projects they do not host).")
            if n404 and not ok and n404 == total and not isinstance(src, PyPIJsonSource):
                url = redact(src.url)
                what = f"does not have {names(404, 410)}" if n404 == 1 else \
                    f"has none of the {n404} packages asked ({names(404, 410)})"
                if urlsplit(src.url).path in ("", "/"):
                    fix = (f"that is a site root; the simple index lives under a path, usually /simple "
                           f"(e.g. {url.rstrip('/')}/simple)")
                else:
                    fix = ("if that is unexpected, check it is the simple index root you would give pip -i "
                           "(it usually ends in /simple)")
                out.append(f"{url} {what}. {fix[0].upper()}{fix[1:]}.")
        return out


def make_fetcher(index_url: Optional[str] = None, extra_index_urls: Sequence[str] = (), file_opts=None,
                 json_index: Optional[str] = None, env=None) -> Indexes:
    """Shared by the CLI and MCP. ``--index-url`` applies to every package. Otherwise a package keeps
    the registry its lockfile recorded, and the rest use ``-i`` from requirements files, then
    PIP_INDEX_URL / UV_DEFAULT_INDEX / UV_INDEX_URL, then PyPI. Extra indexes from all of these pool."""
    return Indexes(IndexConfig(index_url=index_url or None,
                               extra_index_urls=[u for u in extra_index_urls if u],
                               file_index_url=getattr(file_opts, "index_url", None),
                               file_extra_index_urls=list(getattr(file_opts, "extra_index_urls", None) or []),
                               json_index=json_index), env=env)
