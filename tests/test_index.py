"""Simple-API indexes (PEP 691 JSON / PEP 503 HTML), offline.

Parsers run on fixtures; the HTTP layer runs against a throwaway server on 127.0.0.1, so
content negotiation, Basic auth, redirects and error statuses go through real urllib code.
The pip agreement on a live index is scripts/crosscheck_index.py (download.pytorch.org).
"""
import base64
import io
import json
import pathlib
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from wheelripe import cli, index, mcp
from wheelripe.check import BUMP, BLOCKED, MISSING, READY, classify, run
from wheelripe.index import (ACCEPT, IndexFailure, Indexes, IndexConfig, dist_version, env_indexes,
                             parse_html, parse_json, redact, scrub, to_project)
from wheelripe.inputs import Dep, IndexOptions, collect, parse_file
from wheelripe.tags import Python, make_platform

from .conftest import fake_fetch

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TORCH_HTML = (FIXTURES / "pytorch-cpu-torch.html").read_text()
PLATS = [make_platform(k) for k in ("linux-x86_64", "linux-aarch64", "macos-arm64", "windows-amd64")]


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(index, "BACKOFF", 0)
    monkeypatch.setattr(index, "PyPI", lambda index=None: fake_fetch)  # "PyPI" is the offline fake


# ---------------------------------------------------------------------------
# a tiny index server


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        srv = self.server
        srv.seen.append((self.path, self.headers.get("Authorization"), self.headers.get("Accept")))
        route = srv.routes.get(self.path)
        if callable(route):
            route = route(self)
        if route is None:
            route = (404, "text/html", "<h1>Not Found</h1>")
        status, ctype, body = route[:3]
        headers = route[3] if len(route) > 3 else {}
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.routes, srv.seen = {}, []
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


HTML = "text/html; charset=utf-8"
JSON = "application/vnd.pypi.simple.v1+json"


def html_page(*filenames, extra=""):
    links = "\n".join(f'<a href="/files/{f.replace("+", "%2B")}#sha256=00">{f}</a><br/>' for f in filenames)
    return f"<!DOCTYPE html><html><body><h1>Links</h1>\n{links}\n{extra}</body></html>"


def json_page(name, files):
    return json.dumps({"meta": {"api-version": "1.1"}, "name": name, "files": files, "versions": []})


def wheels(name, ver, py, plats=("manylinux_2_28_x86_64", "macosx_14_0_arm64", "win_amd64"), abi=None):
    return [f"{name}-{ver}-{py}-{abi or py}-{p}.whl" for p in plats]


def cli_json(capsys, *argv):
    code = cli.main([*argv, "-f", "json", "--fail-on", "never"])
    cap = capsys.readouterr()
    assert code in (0, 2), cap.err
    return (json.loads(cap.out) if code == 0 else None), cap.err


# ---------------------------------------------------------------------------
# parsing


def test_dist_version_follows_pip():
    assert str(dist_version("torch-2.14.0+cpu-cp315-cp315-win_amd64.whl", "torch")) == "2.14.0+cpu"
    assert str(dist_version("Foo_Bar-1.0-py3-none-any.whl", "foo.bar")) == "1.0"
    assert str(dist_version("foo_bar-1.0.tar.gz", "Foo-Bar")) == "1.0"
    assert str(dist_version("foo-bar-2.0.tar.bz2", "foo-bar")) == "2.0"  # legacy sdist formats count
    assert str(dist_version("foo-1.0-2.zip", "foo")) == "1.0.post2"     # pip splits after the name
    assert dist_version("other-1.0.tar.gz", "foo") is None              # someone else's file
    assert dist_version("foo-1.0-py3.9.egg", "foo") is None             # eggs are ignored by pip
    assert dist_version("foo-not.a.version-py3-none-any.whl", "foo") is None
    assert dist_version("foo-1.0.tar.gz.metadata", "foo") is None


def test_parse_pytorch_html_fixture():
    proj = to_project("torch", parse_html(TORCH_HTML, "https://download.pytorch.org/whl/cpu/torch/"))
    rel = proj["releases"]
    assert {"2.13.0+cpu", "2.13.0", "2.14.0+cpu", "2.14.0", "2.14.1+cpu", "2.14.1"} <= set(rel)
    names = {f["filename"] for f in rel["2.14.0+cpu"]}
    assert "torch-2.14.0+cpu-cp315-cp315-win_amd64.whl" in names  # %2B decoded
    assert "torch-2.14.0-cp315-cp315-macosx_14_0_arm64.whl" in {f["filename"] for f in rel["2.14.0"]}
    f = rel["2.14.0+cpu"][0]
    assert len(f["hashes"]["sha256"]) == 64 and f["yanked"] is False


def test_parse_pep503_details():
    page = textwrap.dedent("""\
        <html><head><base href="https://files.example/pkgs/sub/"></head><body>
        <a href="../foo_bar-1.0.tar.gz#sha256=aa">foo_bar-1.0.tar.gz</a>
        <a href="Foo.Bar-2.0-py3-none-any.whl#sha256=bb" data-requires-python="&gt;=3.10">x</a>
        <a href="foo_bar-2.1-py3-none-any.whl" data-yanked>yanked, no reason</a>
        <a href="foo_bar-2.2-py3-none-any.whl" data-yanked="broke the build">yanked</a>
        <a href="https://elsewhere.example/other-9.0.tar.gz">another project</a>
        <a href="foo_bar-0.1-py2.7.egg">egg</a>
        </body></html>""")
    files = parse_html(page, "https://idx.example/simple/foo-bar/")
    by = {f["filename"]: f for f in files}
    assert by["Foo.Bar-2.0-py3-none-any.whl"]["requires_python"] == ">=3.10"  # entities unescaped
    assert by["foo_bar-1.0.tar.gz"]["hashes"] == {"sha256": "aa"}
    assert by["foo_bar-2.1-py3-none-any.whl"]["yanked"] is True
    assert by["foo_bar-2.2-py3-none-any.whl"]["yanked"] is True
    assert by["Foo.Bar-2.0-py3-none-any.whl"]["yanked"] is False
    proj = to_project("foo-bar", files)
    assert sorted(proj["releases"]) == ["1.0", "2.0", "2.1", "2.2"]  # not other-9.0, not the egg


def test_parse_pep691_json():
    data = {"meta": {"api-version": "1.0"}, "name": "fastjson", "files": [
        {"filename": "fastjson-1.0-py3-none-any.whl", "url": "x", "hashes": {"sha256": "aa"}},
        {"filename": "fastjson-1.1-py3-none-any.whl", "url": "x", "hashes": {}, "yanked": True},
        {"filename": "fastjson-1.2-py3-none-any.whl", "url": "x", "hashes": {}, "yanked": "oops"},
        {"filename": "fastjson-2.0.tar.gz", "url": "x", "hashes": {}, "requires-python": ">=3.11"},
    ]}
    by = {f["filename"]: f for f in parse_json(data, "https://x/simple/fastjson/")}
    assert [by[k]["yanked"] for k in sorted(by)] == [False, True, True, False]
    assert by["fastjson-2.0.tar.gz"]["requires_python"] == ">=3.11"
    with pytest.raises(IndexFailure, match="simple API version 2.0"):
        parse_json({"meta": {"api-version": "2.0"}, "files": []}, "https://x/simple/fastjson/")


def test_non_simple_html_is_detected():
    from wheelripe.index import NotSimple
    with pytest.raises(NotSimple):
        parse_html('<html><body><form action="/login"><input name="user"></form></body></html>', "https://x/")
    assert parse_html("<html><body><h1>Links for empty</h1></body></html>", "https://x/") == []


# ---------------------------------------------------------------------------
# verdicts on a PyTorch-style index (expectations verified against pip on the live index)


def torch_fetch(name, source=None):
    if name != "torch":
        return None
    return to_project("torch", parse_html(TORCH_HTML, "https://download.pytorch.org/whl/cpu/torch/"))


def verdicts(*specs, py="3.15"):
    deps = [Dep("torch", s or None, "" if s else ">=0") for s in specs]
    rep = run(deps, torch_fetch, Python.parse(py), PLATS)
    return {r.version if r.pinned else "latest": r for r in rep.results}


def test_local_versions_match_like_pip():
    r = verdicts("2.14.0", "2.13.0", "2.14.0+cpu", "2.12.0", "2.13.0+cpu", None)
    # ==2.14.0 also accepts 2.14.0+cpu (PEP 440): +cpu on Linux/Windows, plain on macOS
    assert r["2.14.0"].status == READY
    assert r["2.14.0"].platforms["windows-amd64"].wheel == "torch-2.14.0+cpu-cp315-cp315-win_amd64.whl"
    assert r["2.14.0"].platforms["macos-arm64"].wheel == "torch-2.14.0-cp315-cp315-macosx_14_0_arm64.whl"
    # 2.13.0 shipped cp315 only for Linux
    t = r["2.13.0"]
    assert t.status == BUMP and t.fix == "2.14.0"
    assert {k: p.status for k, p in t.platforms.items()} == {
        "linux-x86_64": READY, "linux-aarch64": READY, "macos-arm64": BUMP, "windows-amd64": BUMP}
    assert r["2.13.0+cpu"].platforms["linux-aarch64"].status == READY
    # an exact local pin does not match the plain macOS build of the same release
    t = r["2.14.0+cpu"]
    assert t.platforms["macos-arm64"].status == BUMP and t.platforms["macos-arm64"].fix == "2.14.1"
    assert t.platforms["windows-amd64"].status == READY
    assert r["2.12.0"].status == BUMP and r["2.12.0"].fix == "2.14.0"
    assert r["latest"].version == "2.14.1" and r["latest"].status == READY


def test_local_versions_free_threaded():
    r = verdicts("2.13.0", "2.14.1", py="3.15t")
    t = r["2.13.0"]
    assert t.platforms["linux-x86_64"].status == READY and t.platforms["linux-x86_64"].kind == "cp315t"
    assert t.platforms["windows-amd64"].status == BUMP
    assert r["2.14.1"].status == READY


def test_unknown_local_version_is_missing_with_a_hint():
    r = verdicts("2.14.0+cu121")
    assert r["2.14.0+cu121"].status == MISSING
    res = classify(Dep("torch", "2.14.0+cpu"), {"info": {}, "releases": {"2.14.0": []}}, Python.parse("3.15"), PLATS)
    assert res.status == MISSING and "--index-url" in res.note and "+cpu" in res.note


# ---------------------------------------------------------------------------
# over HTTP


def test_json_negotiation_and_bump(server, capsys):
    server.routes["/simple/fastjson/"] = (200, JSON, json_page("fastjson", [
        {"filename": f, "url": f"/f/{f}", "hashes": {}} for f in
        wheels("fastjson", "1.0", "cp314") + wheels("fastjson", "2.0", "cp315")]))
    data, _ = cli_json(capsys, "fastjson==1.0", "--index-url", f"{server.base}/simple/")
    assert server.seen[0][2] == ACCEPT
    pkg = data["packages"][0]
    assert pkg["status"] == "bump" and pkg["fix"] == "2.0"
    assert data["indexes"] == [f"127.0.0.1:{server.server_address[1]}/simple"]


def test_html_index_and_name_normalization(server, capsys):
    server.routes["/simple/fast-json/"] = (200, HTML, html_page(*wheels("Fast_JSON", "3.0", "cp315")))
    data, _ = cli_json(capsys, "Fast_JSON==3.0", "-i", f"{server.base}/simple")
    assert [p for p, _, _ in server.seen] == ["/simple/fast-json/"]
    assert data["packages"][0]["status"] == "ready"


def test_extra_indexes_are_pooled(server, capsys):
    server.routes["/a/pkg/"] = (200, HTML, html_page(*wheels("pkg", "1.0", "cp314")))
    server.routes["/b/pkg/"] = (200, JSON, json_page("pkg", [{"filename": f, "url": f, "hashes": {}}
                                                           for f in wheels("pkg", "2.0", "cp315")]))
    server.routes["/b/only-b/"] = (200, HTML, html_page(*wheels("only_b", "1.0", "py3", plats=("any",),
                                                                 abi="none")))
    data, err = cli_json(capsys, "pkg==1.0", "only-b", "--index-url", f"{server.base}/a",
                         "--extra-index-url", f"{server.base}/b")
    by = {p["name"]: p for p in data["packages"]}
    assert by["pkg"]["status"] == "bump" and by["pkg"]["fix"] == "2.0"  # 2.0 exists only on /b
    assert by["only-b"]["status"] == "ready"  # /a answered 404, /b has it
    assert err == ""  # /a served pkg, so its 404 for only-b is not worth a warning


def test_index_url_replaces_pypi(server, capsys):
    calls = []
    index_pypi = lambda index=None: (lambda name: calls.append(name))  # noqa: E731
    server.routes["/simple/six/"] = (200, HTML, html_page("six-1.16.0-py2.py3-none-any.whl"))
    import wheelripe.index as idx
    old = idx.PyPI
    idx.PyPI = index_pypi
    try:
        data, _ = cli_json(capsys, "six==1.16.0", "--index-url", f"{server.base}/simple")
    finally:
        idx.PyPI = old
    assert data["packages"][0]["status"] == "ready"
    assert calls == []  # private package names never leak to pypi.org


def test_missing_and_status_warnings(server, capsys):
    server.routes["/s/locked/"] = (401, HTML, "no")
    server.routes["/s/hidden/"] = (403, "application/xml", "<Error/>")
    data, err = cli_json(capsys, "nowhere==1", "locked==1", "hidden==1", "--index-url", f"{server.base}/s")
    by = {p["name"]: p for p in data["packages"]}
    host = f"127.0.0.1:{server.server_address[1]}"
    assert by["nowhere"]["status"] == "missing"
    assert by["nowhere"]["note"] == (f"not found on {host}/s (404 Not Found); PyPI was not searched "
                                     "(--index-url replaces it: add --extra-index-url https://pypi.org/simple)")
    assert "401 Unauthorized" in by["locked"]["note"] and "403 Forbidden" in by["hidden"]["note"]
    assert f"it needs credentials: put them in the URL (http://USER:TOKEN@{host}/...) or in ~/.netrc" in err
    assert "has none of the" not in err  # not every answer was a 404
    assert data["warnings"] and all(w in err for w in data["warnings"])


def test_all_404_hints_at_the_simple_root(server, capsys):
    data, err = cli_json(capsys, "six==1.16.0", "attrs", "--index-url", f"{server.base}/repo")
    assert f"{server.base}/repo has none of the 2 packages asked (attrs, six). If that is unexpected" in err
    assert "ends in /simple" in err
    _, err = cli_json(capsys, "six==1.16.0", "--index-url", server.base)
    assert f"{server.base} does not have six. That is a site root" in err
    assert f"(e.g. {server.base}/simple)" in err


def test_403_everywhere_warns(server, capsys):
    server.routes["/s/a/"] = server.routes["/s/b/"] = (403, "application/xml", "<Error/>")
    _, err = cli_json(capsys, "a==1", "b==1", "--index-url", f"{server.base}/s")
    assert "answered 403 Forbidden for every package asked (a, b)" in err


@pytest.mark.parametrize("route, needle", [
    ((200, HTML, '<html><script src="app.js"></script><form><input name="password"></form></html>'),
     "a web page with no file links (a web UI or login page?)"),
    ((200, "application/xml", "<ListBucketResult/>"), "returned application/xml, not a simple index page"),
    ((200, "application/json", '{"info": {}, "releases": {}}'), "looks like the PyPI JSON API"),
    ((400, HTML, "bad"), "answered HTTP 400 Bad Request"),
])
def test_unusable_answers_are_errors(server, capsys, route, needle):
    server.routes["/simple/six/"] = route
    code = cli.main(["six==1.16.0", "--index-url", f"{server.base}/simple"])
    err = capsys.readouterr().err
    assert code == 2 and needle in err, err
    if route[0] == 200 and "PyPI JSON" not in needle:
        assert f"Check that 127.0.0.1:{server.server_address[1]}/simple is the simple API root you would give pip -i" in err


def test_pypi_website_is_not_an_index():
    # what `--index-url https://pypi.org/pypi` ends up at after PyPI's redirect
    src = index.SimpleSource("https://pypi.org/pypi", index.Auth())
    with pytest.raises(IndexFailure, match="is PyPI's website, not its simple index. Use https://pypi.org/simple"):
        src._parse("six", "text/html", "utf-8", b"<a href='six-1.17.0.tar.gz'>x</a>", "https://pypi.org/project/six/")


def test_server_errors_retry_then_fail(server, capsys):
    server.routes["/simple/six/"] = (503, HTML, "busy")
    assert cli.main(["six==1.16.0", "--index-url", f"{server.base}/simple"]) == 2
    err = capsys.readouterr().err
    assert "after 3 attempts: HTTP 503 Service Unavailable" in err
    assert len(server.seen) == 3


def test_unreachable_index(capsys):
    assert cli.main(["six==1.16.0", "--index-url", "http://alice:hunter2@127.0.0.1:9/simple"]) == 2
    err = capsys.readouterr().err
    assert "could not fetch http://127.0.0.1:9/simple/six/ after 3 attempts" in err
    assert "hunter2" not in err and "alice" not in err


# ---------------------------------------------------------------------------
# credentials


def basic(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def test_credentials_are_sent_and_never_printed(server, capsys, tmp_path):
    good = basic("alice", "s3cr@t")

    def guarded(page):
        def route(h):
            return page if h.headers.get("Authorization") == good else (401, HTML, "who are you")
        return route

    server.routes["/simple/torch/"] = guarded((200, HTML, TORCH_HTML))
    server.routes["/simple/gone/"] = guarded((404, HTML, "nope"))
    url = f"http://alice:s3cr%40t@127.0.0.1:{server.server_address[1]}/simple"
    outs = []
    for fmt in ("text", "json", "markdown", "github"):
        code = cli.main(["torch==2.13.0", "gone==1", "-i", url, "-f", fmt, "--annotations",
                         "--summary", str(tmp_path / "s.md")])
        cap = capsys.readouterr()
        assert code == 1
        outs += [cap.out, cap.err]
    assert all(auth == good for _, auth, _ in server.seen)
    blob = "\n".join(outs) + (tmp_path / "s.md").read_text()
    assert "torch 2.13.0 → 2.14.0" in blob
    assert "s3cr" not in blob and "alice" not in blob
    # wrong password: a clear warning, still no secret
    bad = url.replace("s3cr%40t", "wrong")
    _, err = cli_json(capsys, "torch==2.13.0", "-i", bad)
    assert f"the credentials for 127.0.0.1:{server.server_address[1]} were rejected" in err
    assert "wrong" not in err and "alice" not in err


def test_credentials_follow_same_host_redirects_only(server, capsys):
    port = server.server_address[1]
    good = basic("bob", "pw")
    server.routes["/simple/same/"] = (302, None, "", {"Location": "/real/same/"})
    server.routes["/real/same/"] = lambda h: ((200, HTML, html_page(*wheels("same", "1.0", "cp315")))
                                              if h.headers.get("Authorization") == good else (401, HTML, "x"))
    server.routes["/simple/cdn/"] = (302, None, "", {"Location": f"http://localhost:{port}/cdn/cdn/"})
    server.routes["/cdn/cdn/"] = (200, HTML, html_page(*wheels("cdn", "1.0", "cp315")))
    data, _ = cli_json(capsys, "same==1.0", "cdn==1.0", "-i", f"http://bob:pw@127.0.0.1:{port}/simple")
    assert {p["status"] for p in data["packages"]} == {"ready"}
    auth = {path: a for path, a, _ in server.seen}
    assert auth["/real/same/"] == good
    assert auth["/cdn/cdn/"] is None  # another host never sees the credentials


def test_credentials_by_host_and_netrc(server, monkeypatch, tmp_path):
    port = server.server_address[1]
    server.routes["/locked/pkg/"] = lambda h: ((200, HTML, html_page(*wheels("pkg", "1.0", "cp315")))
                                               if h.headers.get("Authorization") == basic("n", "rc")
                                               else (401, HTML, "x"))
    rc = tmp_path / "netrc"
    rc.write_text("machine 127.0.0.1 login n password rc\n")
    rc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(rc))
    # a lockfile registry without credentials (uv strips them) still authenticates via ~/.netrc
    rep = run([Dep("pkg", "1.0", index=f"http://127.0.0.1:{port}/locked")], Indexes(),
              Python.parse("3.15"), [make_platform("linux-x86_64")])
    assert rep.results[0].status == READY and rep.warnings == []


def test_redact_and_scrub():
    assert redact("https://u:p@h.example/simple") == "https://***@h.example/simple"
    assert redact("https://h.example/simple") == "https://h.example/simple"
    assert scrub("boom at https://tok@h/x and http://a:b@c/") == "boom at https://***@h/x and http://***@c/"


# ---------------------------------------------------------------------------
# configuration: requirements files, lockfiles, environment


def test_requirements_index_options(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_TOKEN", "t0k")
    (tmp_path / "base.txt").write_text("--index-url https://download.pytorch.org/whl/cpu\nsix==1.16.0\n")
    (tmp_path / "requirements.txt").write_text(textwrap.dedent("""\
        -r base.txt
        --extra-index-url=https://${PRIVATE_TOKEN}@corp.example/simple  # private
        --extra-index-url https://${UNSET_VAR}@other.example/simple
        torch==2.14.0
        """))
    opts = IndexOptions()
    deps, _ = collect([str(tmp_path / "requirements.txt")], opts)
    assert [d.name for d in deps] == ["six", "torch"]
    assert opts.index_url == "https://download.pytorch.org/whl/cpu"
    assert opts.extra_index_urls == ["https://t0k@corp.example/simple", "https://${UNSET_VAR}@other.example/simple"]
    o2 = IndexOptions()
    (tmp_path / "r2.txt").write_text("-ihttps://a.example/simple\n-i https://b.example/simple\nsix\n")
    parse_file(tmp_path / "r2.txt", o2)
    assert o2.index_url == "https://b.example/simple"  # the last one wins, as in pip


def test_requirements_file_index_is_used(server, tmp_path, capsys):
    server.routes["/simple/torch/"] = (200, HTML, TORCH_HTML)
    req = tmp_path / "requirements.txt"
    req.write_text(f"--index-url {server.base}/simple\ntorch==2.14.0\n")
    data, _ = cli_json(capsys, str(req))
    assert data["packages"][0]["status"] == "ready" and data["indexes"]
    # an explicit --index-url beats the file
    server.routes["/other/torch/"] = (200, HTML, html_page(*wheels("torch", "2.14.0", "cp314")))
    data, _ = cli_json(capsys, str(req), "--index-url", f"{server.base}/other")
    assert data["packages"][0]["status"] == "blocked"


def test_env_fallbacks():
    assert env_indexes({}) == (None, [])
    env = {"PIP_INDEX_URL": "https://a.example/simple", "UV_INDEX_URL": "https://b.example/simple",
           "PIP_EXTRA_INDEX_URL": "https://c.example/simple https://d.example/simple",
           "UV_INDEX": "corp=https://e.example/simple https://f.example/simple"}
    assert env_indexes(env) == ("https://a.example/simple", ["https://c.example/simple", "https://d.example/simple",
                                                            "https://e.example/simple", "https://f.example/simple"])
    assert env_indexes({"UV_INDEX_URL": "https://b.example/simple"})[0] == "https://b.example/simple"
    # precedence: CLI > requirements file > environment
    cfg = Indexes(IndexConfig(file_index_url="https://file.example/simple"), env={"PIP_INDEX_URL": "https://env.example/simple"})
    assert [s.label for s in cfg.default] == ["file.example/simple"]
    cfg = Indexes(IndexConfig(index_url="https://cli.example/simple", file_index_url="https://file.example/simple"))
    assert [s.label for s in cfg.default] == ["cli.example/simple"]
    cfg = Indexes(env={"PIP_INDEX_URL": "https://u:p@env.example/simple"})
    assert [s.label for s in cfg.default] == ["env.example/simple"]  # credentials never shown
    cfg = Indexes(IndexConfig(extra_index_urls=["https://pypi.org/simple/"]))
    assert [s.label for s in cfg.default] == ["pypi.org"]  # pypi.org/simple is the JSON API source


def test_bad_index_urls_are_usage_errors(capsys):
    assert cli.main(["six", "--index-url", "download.pytorch.org/whl/cpu"]) == 2
    assert "is not an http(s) URL" in capsys.readouterr().err
    assert cli.main(["six", "--index-url", "https://x.example/simple", "--index", "https://m.example/pypi"]) == 2
    assert "not both" in capsys.readouterr().err


def test_lockfile_registries(tmp_path):
    (tmp_path / "uv.lock").write_text(textwrap.dedent("""\
        version = 1
        [[package]]
        name = "torch"
        version = "2.14.0+cpu"
        source = { registry = "https://download.pytorch.org/whl/cpu" }
        [[package]]
        name = "six"
        version = "1.16.0"
        source = { registry = "https://pypi.org/simple" }
        """))
    assert [(d.name, d.index) for d in parse_file(tmp_path / "uv.lock")] == [
        ("torch", "https://download.pytorch.org/whl/cpu"), ("six", None)]
    (tmp_path / "poetry.lock").write_text(textwrap.dedent("""\
        [[package]]
        name = "torch"
        version = "2.14.0+cpu"
        [package.source]
        type = "legacy"
        url = "https://download.pytorch.org/whl/cpu"
        reference = "pytorch"
        [[package]]
        name = "six"
        version = "1.16.0"
        """))
    assert [(d.name, d.index) for d in parse_file(tmp_path / "poetry.lock")] == [
        ("torch", "https://download.pytorch.org/whl/cpu"), ("six", None)]
    (tmp_path / "pylock.toml").write_text(textwrap.dedent("""\
        lock-version = "1.0"
        [[packages]]
        name = "torch"
        version = "2.14.0"
        index = "https://download.pytorch.org/whl/cpu"
        [[packages]]
        name = "six"
        version = "1.16.0"
        index = "https://pypi.org/simple"
        """))
    assert [(d.name, d.index) for d in parse_file(tmp_path / "pylock.toml")] == [
        ("torch", "https://download.pytorch.org/whl/cpu"), ("six", None)]
    (tmp_path / "Pipfile.lock").write_text(json.dumps({
        "_meta": {"sources": [{"name": "pypi", "url": "https://pypi.org/simple"},
                              {"name": "pytorch", "url": "https://download.pytorch.org/whl/cpu"}]},
        "default": {"torch": {"version": "==2.14.0", "index": "pytorch"}, "six": {"version": "==1.16.0"}}}))
    assert sorted((d.name, d.index) for d in parse_file(tmp_path / "Pipfile.lock")) == [
        ("six", None), ("torch", "https://download.pytorch.org/whl/cpu")]


def test_uv_lock_registry_is_fetched_per_package(server, tmp_path, capsys):
    server.routes["/whl/cpu/torch/"] = (200, HTML, TORCH_HTML)
    (tmp_path / "uv.lock").write_text(textwrap.dedent(f"""\
        version = 1
        [[package]]
        name = "torch"
        version = "2.13.0+cpu"
        source = {{ registry = "{server.base}/whl/cpu" }}
        [[package]]
        name = "fastpkg"
        version = "1.0.0"
        source = {{ registry = "https://pypi.org/simple" }}
        """))
    data, _ = cli_json(capsys, str(tmp_path / "uv.lock"))
    by = {p["name"]: p for p in data["packages"]}
    assert by["torch"]["status"] == "bump" and by["torch"]["fix"] == "2.14.0"
    assert by["fastpkg"]["status"] == "ready"  # from (fake) PyPI
    assert sorted(data["indexes"]) == sorted([f"127.0.0.1:{server.server_address[1]}/whl/cpu", "pypi.org"])


# ---------------------------------------------------------------------------
# defaults and MCP


def test_default_is_still_the_pypi_json_api(capsys):
    fetch = Indexes()
    assert fetch("oldpin") is fake_fetch("oldpin")  # same object: no re-shaping on the default path
    assert fetch("nosuch") is None
    data, err = cli_json(capsys, "oldpin==1.0.0", "nosuch==1")
    assert "indexes" not in data and "warnings" not in data and err == ""
    assert {p["name"]: p.get("note") for p in data["packages"]}["nosuch"] == "not found on the package index"
    assert cli.main(["oldpin==1.0.0"]) == 1
    head = capsys.readouterr().out.splitlines()[1]
    assert head == "cli · 1 package"  # no index line unless you configured one


def test_mcp_index_arguments(server):
    tools = {t["name"]: t for t in mcp.TOOLS}
    props = tools["check_python_upgrade"]["inputSchema"]["properties"]
    assert props["index_url"]["type"] == "string" and props["extra_index_urls"]["type"] == "array"
    server.routes["/simple/torch/"] = (200, HTML, TORCH_HTML)
    out = mcp.call_tool("check_python_upgrade", {
        "requirements": ["torch==2.13.0"], "index_url": f"http://u:secret@127.0.0.1:{server.server_address[1]}/simple"})
    sc = out["structuredContent"]
    assert sc["packages"][0]["fix"] == "2.14.0"
    assert "secret" not in json.dumps(out)
    assert sc["indexes"] == [f"127.0.0.1:{server.server_address[1]}/simple"]
    resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "check_python_upgrade", "arguments": {"requirements": ["six"],
                                                      "index_url": "http://u:secret@127.0.0.1:9/simple"}}})
    text = resp["result"]["content"][0]["text"]
    assert resp["result"]["isError"] and "could not fetch" in text and "secret" not in text
