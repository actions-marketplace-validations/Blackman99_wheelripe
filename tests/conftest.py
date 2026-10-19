import pytest


def wheel(name, ver, py, abi, plat, requires_python=None, yanked=False):
    return {"filename": f"{name}-{ver}-{py}-{abi}-{plat}.whl", "requires_python": requires_python,
            "yanked": yanked, "packagetype": "bdist_wheel"}


def sdist(name, ver, requires_python=None):
    return {"filename": f"{name}-{ver}.tar.gz", "requires_python": requires_python, "yanked": False,
            "packagetype": "sdist"}


ALL_PLATS = ["manylinux_2_17_x86_64.manylinux2014_x86_64", "macosx_11_0_arm64", "win_amd64"]


def binary(name, ver, pys, rp=None, plats=ALL_PLATS):
    files = [sdist(name, ver, rp)]
    for py in pys:
        for p in plats:
            files.append(wheel(name, ver, f"cp{py}", f"cp{py}", p, rp))
    return files


def project(name, releases, urls=None):
    latest = sorted(releases, key=lambda v: [int(x) for x in v.split(".")])[-1]
    return {"info": {"name": name, "version": latest, "project_urls": urls or {}}, "releases": releases}


FAKE = {
    # ready: has cp315 wheels everywhere
    "fastpkg": project("fastpkg", {"1.0.0": binary("fastpkg", "1.0.0", ["313", "314", "315"])}),
    # bump: 1.0 only up to cp314, 2.0 adds cp315
    "oldpin": project("oldpin", {"1.0.0": binary("oldpin", "1.0.0", ["313", "314"]),
                                 "2.0.0": binary("oldpin", "2.0.0", ["314", "315"])}),
    # blocked: nothing for cp315 yet
    "slowpoke": project("slowpoke", {"3.1.0": binary("slowpoke", "3.1.0", ["312", "313", "314"])},
                        urls={"Source": "https://github.com/acme/slowpoke"}),
    # pure python
    "purelib": project("purelib", {"0.5": [sdist("purelib", "0.5"),
                                           wheel("purelib", "0.5", "py3", "none", "any")]}),
    # sdist only
    "srconly": project("srconly", {"0.1": [sdist("srconly", "0.1")]}),
    # abi3 wheel from cp39 -> works on 3.15 GIL, not on 3.15t
    "stable": project("stable", {"4.0": [sdist("stable", "4.0")] + [
        wheel("stable", "4.0", "cp39", "abi3", p) for p in ALL_PLATS]}),
    # requires-python excludes 3.15 in 1.x, 2.x allows it with pure wheel
    "capped": project("capped", {"1.0": [sdist("capped", "1.0", "<3.15"),
                                         wheel("capped", "1.0", "py3", "none", "any", "<3.15")],
                                 "2.0": [wheel("capped", "2.0", "py3", "none", "any", ">=3.10")]}),
    # linux only cp315
    "partial": project("partial", {"1.0": binary("partial", "1.0", ["314"]) + [
        wheel("partial", "1.0", "cp315", "cp315", "manylinux_2_28_x86_64")]}),
    # abi3.abi3t from cp315 -> works on both 3.15 and 3.15t
    "dualabi": project("dualabi", {"1.0": [wheel("dualabi", "1.0", "cp315", "abi3.abi3t", p) for p in ALL_PLATS]}),
    # cp315 only for a new glibc
    "newglibc": project("newglibc", {"1.0": binary("newglibc", "1.0", ["315"], plats=["manylinux_2_39_x86_64", "win_amd64"])}),
    # 1.0 is yanked (an exact pin still installs it, as in pip); 1.1 is the newest usable release
    "yankpin": project("yankpin", {"1.0": [dict(f, yanked=True) for f in binary("yankpin", "1.0", ["315"])],
                                   "1.1": binary("yankpin", "1.1", ["314"])}),
    # windows-only package
    "winonly": project("winonly", {"300": binary("winonly", "300", ["314", "315"], plats=["win_amd64"])}),
}


def fake_fetch(name):
    return FAKE.get(name)


INDEX_ENV = ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "UV_DEFAULT_INDEX", "UV_INDEX_URL", "UV_INDEX",
             "UV_EXTRA_INDEX_URL", "WHEELRIPE_INDEX", "NETRC")


@pytest.fixture(autouse=True)
def _no_index_env(monkeypatch, tmp_path):
    """Tests never inherit the developer's index configuration or ~/.netrc."""
    for var in INDEX_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NETRC", str(tmp_path / "no-netrc"))


@pytest.fixture
def fetch():
    return fake_fetch
