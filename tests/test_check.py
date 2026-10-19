from wheelripe.check import BLOCKED, BUILD, BUMP, MISSING, READY, SKIP, run
from wheelripe.inputs import Dep
from wheelripe.tags import Python, make_platform

PLATS = [make_platform(k) for k in ("linux-x86_64", "macos-arm64", "windows-amd64")]


def go(fetch, *deps, py="3.15", plats=PLATS):
    rep = run(list(deps), fetch, Python.parse(py), plats)
    return rep, {r.name: r for r in rep.results}


def test_statuses(fetch):
    rep, r = go(fetch, Dep("fastpkg", "1.0.0"), Dep("oldpin", "1.0.0"), Dep("slowpoke", "3.1.0"),
                Dep("purelib", "0.5"), Dep("srconly", "0.1"), Dep("stable", "4.0"), Dep("nosuch", "1"))
    assert r["fastpkg"].status == READY
    assert r["oldpin"].status == BUMP and r["oldpin"].fix == "2.0.0"
    assert r["slowpoke"].status == BLOCKED and r["slowpoke"].upstream == "https://github.com/acme/slowpoke"
    assert r["slowpoke"].ships == ["cp312", "cp313", "cp314"]
    assert r["purelib"].status == READY and r["purelib"].platforms["linux-x86_64"].kind == "pure"
    assert r["srconly"].status == BUILD
    assert r["stable"].status == READY and r["stable"].platforms["windows-amd64"].kind == "abi3"
    assert r["nosuch"].status == MISSING
    assert [x.name for x in rep.failing("bump")] == ["nosuch", "slowpoke", "oldpin"]
    assert [x.name for x in rep.failing("blocked")] == ["nosuch", "slowpoke"]
    assert rep.to_dict()["ripe"] is False


def test_freethreaded(fetch):
    _, r = go(fetch, Dep("stable", "4.0"), Dep("dualabi", "1.0"), Dep("fastpkg", "1.0.0"), py="3.15t")
    assert r["stable"].status == BLOCKED  # abi3 never loads on free-threaded builds
    assert r["dualabi"].status == READY and r["dualabi"].platforms["linux-x86_64"].kind == "abi3t"
    assert r["fastpkg"].status == BLOCKED  # only GIL cp315 wheels


def test_requires_python_excluded_then_bump(fetch):
    _, r = go(fetch, Dep("capped", "1.0"))
    assert r["capped"].status == BUMP and r["capped"].fix == "2.0"
    assert "requires-python" in r["capped"].platforms["linux-x86_64"].note


def test_partial_platforms(fetch):
    _, r = go(fetch, Dep("partial", "1.0"))
    assert r["partial"].status == BLOCKED
    assert r["partial"].platforms["linux-x86_64"].status == READY
    assert r["partial"].platforms["windows-amd64"].status == BLOCKED


def test_markers_skip(fetch):
    _, r = go(fetch, Dep("winonly", "300", marker="sys_platform == 'win32'"),
              Dep("oldpin", "1.0.0", marker="python_version < '3.15'"))
    assert r["winonly"].status == READY
    assert r["winonly"].platforms["linux-x86_64"].status == SKIP
    assert r["oldpin"].status == SKIP


def test_unpinned_picks_latest(fetch):
    _, r = go(fetch, Dep("oldpin", None, ">=1"), Dep("oldpin", None, "<2"))
    assert r["oldpin"].version in ("2.0.0", "1.0.0")
    rep, _ = go(fetch, Dep("oldpin", None, "<2"))
    res = rep.results[0]
    assert res.version == "1.0.0" and res.status == BUMP and res.fix == "2.0.0"


def test_ripe_report(fetch):
    rep, _ = go(fetch, Dep("fastpkg", "1.0.0"), Dep("purelib", "0.5"))
    assert rep.to_dict()["ripe"] is True and not rep.failing("bump")


def test_glibc_floor_hint(fetch):
    _, r = go(fetch, Dep("newglibc", "1.0"), plats=[make_platform("linux-x86_64")])
    assert r["newglibc"].status == BLOCKED
    assert "glibc >= 2.39" in r["newglibc"].platforms["linux-x86_64"].note
    _, r = go(fetch, Dep("newglibc", "1.0"), plats=[make_platform("linux-x86_64", glibc="2.39")])
    assert r["newglibc"].status == READY


def test_yanked_pin_is_used_like_pip(fetch):
    # pip: yanked files are skipped, unless an exact pin selects that release (PEP 592)
    _, r = go(fetch, Dep("yankpin", "1.0"))
    assert r["yankpin"].status == READY and r["yankpin"].yanked is True
    _, r = go(fetch, Dep("yankpin", None, ">=1"))
    assert r["yankpin"].version == "1.1" and r["yankpin"].status == BLOCKED  # 1.1 has no cp315
    from wheelripe.report import explain
    _, r = go(fetch, Dep("yankpin", "1.0"))
    assert "1.0 is yanked" in explain(r["yankpin"], [p.key for p in PLATS], "3.15")
