#!/usr/bin/env python3
"""Ground truth on a real non-PyPI simple index: wheelripe --index-url must agree with pip.

PyTorch's CPU index (https://download.pytorch.org/whl/cpu) is PEP 503 HTML only, publishes
local versions (torch 2.14.0+cpu for Linux/Windows, plain 2.14.0 for macOS), and answers 403
for projects it does not host. For each spec and platform we ask pip, with
--only-binary=:all: and explicit --python-version/--abi/--platform, whether it can find a wheel
with `--index-url <pytorch> --extra-index-url https://pypi.org/simple`, and require wheelripe
to say READY exactly when pip does. Checks PEP 440 local-version matching (==2.14.0 also
accepts 2.14.0+cpu), pooling across indexes, and HTML parsing (%2B in hrefs, hash fragments).

pip reads core metadata through PEP 658 (data-core-metadata), so no torch wheel is downloaded.
Run: python scripts/crosscheck_index.py 3.15
"""
import json
import subprocess
import sys
import tempfile

sys.path.insert(0, __import__("os").path.dirname(__file__))
from crosscheck_pip import PLATFORMS as ALL_PLATFORMS  # noqa: E402  same pip --platform expansion

INDEX = "https://download.pytorch.org/whl/cpu"
EXTRA = "https://pypi.org/simple"
SPECS = [
    "torch==2.12.0",       # cp314 at most -> no cp315 anywhere
    "torch==2.13.0",       # cp315 only as 2.13.0+cpu on Linux (public pin matches the local build)
    "torch==2.13.0+cpu",   # exact local pin
    "torch==2.14.0",       # +cpu on Linux/Windows, plain 2.14.0 on macOS: ready everywhere
    "torch==2.14.0+cpu",   # exact local pin: no macOS wheel
    "torch==2.14.1",
    "six==1.16.0",         # not on the PyTorch index (403) -> comes from the extra index
    "markupsafe==3.0.3",   # on both indexes -> pooled
    "requests==2.32.0",    # yanked on PyPI: pip still installs an exact pin (PEP 592)
]
PLATFORMS = {k: ALL_PLATFORMS[k] for k in ("linux-x86_64", "linux-aarch64", "macos-arm64", "macos-x86_64",
                                           "windows-amd64")}
PY = sys.argv[1] if len(sys.argv) > 1 else "3.15"


def pip_ok(spec, plats, py):
    nodot = py.replace(".", "").rstrip("t")
    abi = f"cp{nodot}t" if py.endswith("t") else f"cp{nodot}"
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [sys.executable, "-m", "pip", "install", "--dry-run", "--no-deps", "--ignore-installed",
               "--only-binary=:all:", "--target", tmp, "--python-version", py.rstrip("t"),
               "--implementation", "cp", "--abi", abi, "-q", "--disable-pip-version-check",
               "--index-url", INDEX, "--extra-index-url", EXTRA]
        for p in plats:
            cmd += ["--platform", p]
        cmd.append(spec)
        return subprocess.run(cmd, capture_output=True, text=True).returncode == 0


def main():
    out = subprocess.run([sys.executable, "-m", "wheelripe", *SPECS, "--python", PY, "-f", "json",
                          "--fail-on", "never", "--platform", ",".join(PLATFORMS),
                          "--index-url", INDEX, "--extra-index-url", EXTRA],
                         capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    assert data.get("indexes") == ["download.pytorch.org/whl/cpu", "pypi.org"], data.get("indexes")
    rep = {(p["name"].lower(), p["version"]): p for p in data["packages"]}
    bad = rows = 0
    for spec in SPECS:
        name, ver = spec.split("==")
        res = rep[(name.lower(), ver)]
        for key, plats in PLATFORMS.items():
            mine = res["platforms"][key]["status"] == "ready"
            theirs = pip_ok(spec, plats, PY)
            rows += 1
            bad += mine != theirs
            print(f"{'ok ' if mine == theirs else 'MISMATCH'} {spec:22} {key:16} "
                  f"wheelripe={'ready' if mine else res['platforms'][key]['status']:8} "
                  f"pip={'wheel' if theirs else 'none'}")
    print(f"\n{rows - bad}/{rows} agree with pip on {INDEX} + {EXTRA} ({PY})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
