import io
import json

import pytest

from wheelripe import cli, index, mcp, report
from wheelripe.check import run
from wheelripe.inputs import Dep
from wheelripe.tags import Python, make_platform

from .conftest import fake_fetch


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(index, "PyPI", lambda index=None: fake_fetch)


def test_cli_exit_codes(capsys):
    assert cli.main(["fastpkg==1.0.0", "purelib==0.5"]) == 0
    out = capsys.readouterr().out
    assert "RIPE" in out and "NOT RIPE" not in out
    assert cli.main(["oldpin==1.0.0"]) == 1
    assert "oldpin 1.0.0 → 2.0.0" in capsys.readouterr().out
    assert cli.main(["oldpin==1.0.0", "--fail-on", "blocked"]) == 0
    assert cli.main(["slowpoke==3.1.0", "--fail-on", "never"]) == 0
    assert cli.main(["--python", "nope", "six"]) == 2
    assert cli.main(["--platform", "amiga", "six"]) == 2


def test_cli_json_and_summary(tmp_path, capsys):
    summary = tmp_path / "summary.md"
    assert cli.main(["slowpoke==3.1.0", "-f", "json", "--summary", str(summary)]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ripe"] is False and data["packages"][0]["status"] == "blocked"
    assert "| BLOCKED | [slowpoke](https://github.com/acme/slowpoke)" in summary.read_text()


def test_cli_github_format(capsys):
    cli.main(["slowpoke==3.1.0", "oldpin==1.0.0", "-f", "github"])
    out = capsys.readouterr().out
    assert "::error title=wheelripe BLOCKED slowpoke::" in out
    assert "::warning title=wheelripe BUMP oldpin::" in out


def test_cli_wheel_subcommand(capsys):
    assert cli.main(["wheel", "x-1-cp315-abi3.abi3t-manylinux_2_28_x86_64.whl", "--python", "3.15t",
                     "--platform", "linux-x86_64"]) == 0
    assert cli.main(["wheel", "x-1-cp39-abi3-manylinux_2_28_x86_64.whl", "--python", "3.15t",
                     "--platform", "linux-x86_64"]) == 1


def test_text_report_platform_detail():
    plats = [make_platform(k) for k in ("linux-x86_64", "windows-amd64")]
    rep = run([Dep("partial", "1.0")], fake_fetch, Python.parse("3.15"), plats)
    out = report.text(rep, stream=io.StringIO())
    assert "no cp315 wheel for windows-amd64" in out and "ready on linux-x86_64" in out


def rpc(lines):
    out = io.StringIO()
    mcp.serve(io.StringIO("\n".join(json.dumps(m) for m in lines) + "\n"), out, fetch=fake_fetch)
    return [json.loads(x) for x in out.getvalue().splitlines()]


def test_mcp_roundtrip():
    resp = rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "check_python_upgrade", "arguments": {"requirements": ["oldpin==1.0.0", "slowpoke==3.1.0"]}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "explain_wheel", "arguments": {"filename": "x-1-cp39-abi3-win_amd64.whl",
                                                            "python": "3.15t", "platforms": ["windows-amd64"]}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "bogus"},
    ])
    assert [r["id"] for r in resp] == [1, 2, 3, 4, 5, 6]
    assert resp[0]["result"]["serverInfo"]["name"] == "wheelripe"
    assert {t["name"] for t in resp[1]["result"]["tools"]} == {"check_python_upgrade", "explain_wheel"}
    sc = resp[2]["result"]["structuredContent"]
    assert sc["ripe"] is False and {p["status"] for p in sc["packages"]} == {"bump", "blocked"}
    assert resp[3]["result"]["structuredContent"]["installs"] == {"windows-amd64": False}
    assert resp[4]["error"]["code"] == -32602
    assert resp[5]["error"]["code"] == -32601
