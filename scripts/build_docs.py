"""Generate site/docs/*.html and site/blog/*.html from the content below (stdlib only)."""
from __future__ import annotations

import html
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent / "site"
BASE = "https://blackman99.github.io/wheelripe/"


def code(text: str, lang: str = "") -> str:
    out = []
    for line in text.strip("\n").split("\n"):
        esc = html.escape(line)
        if re.match(r"\s*#", line):
            esc = f'<span class="c">{esc}</span>'
        out.append(esc)
    return '<div class="rind"><pre><code>' + "\n".join(out) + "</code></pre></div>"


DOCS = [
    ("index", "Overview", "What wheelripe answers, in one page."),
    ("install", "Install", "uvx, pipx, pip, the release wheel, or nothing at all in CI."),
    ("usage", "Usage", "The CLI, the GitHub Action and the MCP server."),
    ("configuration", "Configuration", "Targets, floors, fail levels, indexes and environment variables."),
    ("reference", "Reference", "Every flag, status, exit code, JSON field and tag rule."),
    ("faq", "FAQ", "Why not just run pip? What about abi3? Is it ever wrong?"),
]

P = {}

P["index"] = f"""
<h1>The newer the Python, the more holes in the cheese.</h1>
<p class="lede">wheelripe reads your lockfile and, for the Python you want to move to and every platform you ship, tells you which dependencies would fall back to a source build — and the first release of each that fixes it.</p>
<p>When a new CPython comes out (3.15 landed in October 2026, with a supported free-threaded 3.15t build), most upgrades stall on the same question: <em>which of our dependencies have wheels for it yet?</em> Today people answer it by trying an install, reading the error, opening PyPI for each package, scrolling the file list, and repeating per platform. wheelripe does that for every package at once, without installing anything, using the same tag rules pip uses.</p>
<h2>What you get</h2>
{code('''$ wheelripe uv.lock --python 3.15
BLOCKED  confluent-kafka 2.16.0          no cp315 wheel for any target platform (ships cp38–cp314)
BUMP     numpy 1.26.4 → 2.5.2            no cp315 wheel in 1.26.4 (ships cp39–cp312); 2.5.2 is the first release with one
BUMP     pydantic_core 2.41.5 → 2.48.0   no cp315 wheel in 2.41.5 (ships cp39–cp314); 2.48.0 is the first release with one
READY    1 package (0 with binary wheels · 1 pure Python)

NOT RIPE for CPython 3.15: 1 blocked upstream · 2 need a version bump''')}
<ul class="holes">
<li><b>BUMP</b> your pinned version has no wheel, a newer release does; wheelripe names the first one.</li>
<li><b>BLOCKED</b> no release has a wheel yet; you are waiting on upstream (the report links the project).</li>
<li><b>EXCLUDED</b> the release's <code>requires-python</code> rules your target out.</li>
<li><b>BUILD</b> only an sdist exists for any Python; usually fine for pure-Python packages.</li>
<li><b>READY</b> a matching wheel exists: version-specific, stable ABI (abi3/abi3t) or pure Python.</li>
</ul>
<h2>Three ways to run it</h2>
<p><a href="usage.html#cli">CLI</a> on your laptop, <a href="usage.html#github-action">GitHub Action</a> on a schedule so CI tells you the week you become ripe, and an <a href="usage.html#mcp">MCP server</a> so your coding agent can check before it bumps <code>requires-python</code>.</p>
<p class="next"><a href="install.html">Install →</a></p>
"""

P["install"] = f"""
<h1>Install</h1>
<p class="lede">wheelripe is a small pure-Python package (one dependency: <code>packaging</code>). It runs on Python 3.9+ — the interpreter you run it with does not need to be the one you are targeting.</p>
<h2 id="uvx">Run once with uvx</h2>
{code('''uvx --from git+https://github.com/Blackman99/wheelripe wheelripe
# pin a release
uvx --from git+https://github.com/Blackman99/wheelripe@v0.1.0 wheelripe uv.lock -p 3.15t''')}
<h2 id="pipx">Install with pipx</h2>
{code('''pipx install git+https://github.com/Blackman99/wheelripe
wheelripe --version''')}
<h2 id="wheel">From the release wheel</h2>
<p>Every <a href="https://github.com/Blackman99/wheelripe/releases">GitHub Release</a> carries the wheel and sdist built in CI:</p>
{code('''pip install https://github.com/Blackman99/wheelripe/releases/download/v0.1.0/wheelripe-0.1.0-py3-none-any.whl''')}
<p class="note">wheelripe is not on PyPI yet; until it is, install from GitHub as above. The commands stay the same once it is.</p>
<h2 id="ci">In CI</h2>
<p>Nothing to install — use the <a href="usage.html#github-action">GitHub Action</a>. It creates its own virtualenv, so it never touches the Python your job uses.</p>
<h2 id="network">Network</h2>
<p>wheelripe makes one HTTPS request per package to the PyPI JSON API (<code>https://pypi.org/pypi/&lt;name&gt;/json</code>), eight in parallel by default. With <code>--index-url</code> / <code>--extra-index-url</code> it asks those simple-API indexes instead (one request per package per index), and PyPI is not contacted unless you list it. Behind a proxy, the usual <code>HTTPS_PROXY</code> variable is honoured; for a private CA, set <code>SSL_CERT_FILE</code>. See <a href="configuration.html#index">Indexes</a>.</p>
<p class="next"><a href="usage.html">Usage →</a></p>
"""

P["usage"] = f"""
<h1>Usage</h1>
<h2 id="cli">CLI</h2>
<p>Point it at a project directory, a lockfile, or a list of specs. With no argument it looks in the current directory and picks the most precise file it finds: <code>uv.lock</code> &gt; <code>pylock.toml</code> &gt; <code>poetry.lock</code> &gt; <code>Pipfile.lock</code> &gt; <code>requirements*.txt</code> &gt; <code>pyproject.toml</code>.</p>
{code('''# the current project, CPython 3.15, default platforms
wheelripe

# free-threaded 3.15t, every platform you ship
wheelripe uv.lock -p 3.15t --platform linux-x86_64,linux-aarch64,macos-arm64,windows-amd64

# no lockfile handy? pass specs
wheelripe numpy==1.26.4 pydantic-core==2.41.5 orjson

# older glibc in production
wheelripe requirements.txt --glibc 2.28''')}
<p>Unpinned specs (<code>orjson</code>, <code>numpy&gt;=1.26</code>) are checked at the newest release matching the specifier. Pinned ones (<code>==</code>, or anything from a lockfile) are checked at exactly that version, and if it has no wheel wheelripe looks forward for the first newer release that does.</p>
<h3 id="indexes">PyTorch and private indexes</h3>
<p>Point wheelripe at any PEP 691 (JSON) or PEP 503 (HTML) simple index with the options pip takes. Files from every index are pooled, like pip, and versions come from the file names, so local builds such as <code>2.14.0+cpu</code> match the way pip matches them.</p>
{code('''$ wheelripe torch==2.13.0 numpy==2.5.4 --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
cli · 2 packages · index download.pytorch.org/whl/cpu + pypi.org

BUMP     torch 2.13.0 → 2.14.0   no cp315 wheel in 2.13.0 for macos-arm64, windows-amd64 (ships cp310–cp315); 2.14.0 is the first release with one · ready on linux-x86_64
READY    1 package (1 with binary wheels · 0 pure Python)

# a requirements file that already says where to look needs no flags
wheelripe requirements.txt        # -i / --extra-index-url lines, also inside -r includes

# uv.lock: each package is checked on the registry uv locked it from
wheelripe uv.lock -p 3.15t

# a private index: credentials go to that host only and are never printed
wheelripe -i "https://$INDEX_USER:$INDEX_TOKEN@pypi.corp.example/simple" requirements.txt''')}
<p>Precedence, credentials and what happens when an index refuses are on the <a href="configuration.html#index">configuration</a> page.</p>
<h3 id="wheel">Explain a single wheel</h3>
{code('''$ wheelripe wheel orjson-3.13.0-cp315-cp315-manylinux_2_39_x86_64.whl -p 3.15t
orjson-3.13.0-cp315-cp315-manylinux_2_39_x86_64.whl on CPython 3.15t
  no   linux-x86_64       built for the GIL build cp315, not free-threaded cp315t; needs glibc 2.39, your floor is 2.34
  no   macos-arm64        built for the GIL build cp315, not free-threaded cp315t; built for manylinux_2_39_x86_64, not macos-arm64
  no   windows-amd64      ...''')}
<h3 id="formats">Output formats</h3>
<p><code>-f text</code> (default, coloured unless <code>NO_COLOR</code> is set), <code>-f json</code> (<a href="reference.html#json">schema</a>), <code>-f markdown</code> (a table for PR comments or issues), <code>-f github</code> (workflow annotations). <code>--summary FILE</code> and <code>--json-out FILE</code> write extra copies alongside whatever goes to stdout.</p>

<h2 id="github-action">GitHub Action</h2>
<p>A weekly job that tells you when your last blocker clears:</p>
{code('''# .github/workflows/py315.yml
name: ripe for 3.15?
on:
  schedule: [{cron: "0 6 * * 1"}]
  workflow_dispatch:
jobs:
  wheels:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: Blackman99/wheelripe@v0.1.0
        id: ripe
        with:
          python-version: "3.15t"
          platforms: linux-x86_64,macos-arm64,windows-amd64
          fail-on: blocked
      - if: steps.ripe.outputs.ripe == 'true'
        run: echo "Every dependency has a 3.15t wheel. Time to upgrade."''')}
<table>
<thead><tr><th>Input</th><th>Default</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td><code>path</code></td><td><code>.</code></td><td>Lockfile, requirements, pyproject or directory. Space-separated for several.</td></tr>
<tr><td><code>python-version</code></td><td><code>3.15</code></td><td>Target CPython; append <code>t</code> for free-threaded.</td></tr>
<tr><td><code>platforms</code></td><td><code>linux-x86_64,macos-arm64,windows-amd64</code></td><td>Comma-separated <a href="reference.html#platforms">platform keys</a>.</td></tr>
<tr><td><code>fail-on</code></td><td><code>bump</code></td><td><code>bump</code>, <code>blocked</code>, <code>build</code> or <code>never</code>.</td></tr>
<tr><td><code>glibc</code></td><td>2.34</td><td>Oldest glibc you deploy on.</td></tr>
<tr><td><code>macos</code></td><td>14</td><td>Oldest macOS you support.</td></tr>
<tr><td><code>index-url</code></td><td></td><td>Simple-API index to use instead of PyPI, e.g. <code>https://download.pytorch.org/whl/cpu</code>.</td></tr>
<tr><td><code>extra-index-url</code></td><td></td><td>More simple-API indexes, separated by spaces or newlines; pooled with the first.</td></tr>
</tbody></table>
<p>For a private index, build the URL from repository secrets. GitHub masks them in the log, and wheelripe never prints credentials anyway:</p>
{code('''- uses: Blackman99/wheelripe@v0.1.0
  with:
    path: requirements.txt
    index-url: https://${{ secrets.INDEX_USER }}:${{ secrets.INDEX_TOKEN }}@pypi.corp.example/simple
    extra-index-url: https://pypi.org/simple''')}
<table>
<thead><tr><th>Output</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td><code>ripe</code></td><td><code>true</code> when everything installs from a wheel.</td></tr>
<tr><td><code>blocked</code></td><td>Count blocked upstream (including requires-python exclusions).</td></tr>
<tr><td><code>bump</code></td><td>Count fixed by a version bump.</td></tr>
<tr><td><code>report</code></td><td>Path to the JSON report.</td></tr>
</tbody></table>
<p>The step also writes a Markdown table to the job summary and an annotation per problem package. It runs on Linux, macOS and Windows runners (CI tests all three).</p>

<h2 id="mcp">MCP server</h2>
<p><code>wheelripe mcp</code> is a read-only stdio MCP server, so an agent can check before it bumps <code>requires-python</code> or a CI matrix.</p>
{code('''{
  "mcpServers": {
    "wheelripe": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/Blackman99/wheelripe", "wheelripe", "mcp"]
    }
  }
}''')}
<table>
<thead><tr><th>Tool</th><th>Arguments</th><th>Returns</th></tr></thead>
<tbody>
<tr><td><code>check_python_upgrade</code></td><td><code>path</code> or <code>requirements[]</code>, <code>python</code>, <code>platforms[]</code>, <code>glibc</code>, <code>macos</code>, <code>index_url</code>, <code>extra_index_urls[]</code></td><td>Markdown table + the full <a href="reference.html#json">JSON report</a> as structured content.</td></tr>
<tr><td><code>explain_wheel</code></td><td><code>filename</code>, <code>python</code>, <code>platforms[]</code></td><td>Installs yes/no per platform, with the reason.</td></tr>
</tbody></table>
<p>Both tools are annotated <code>readOnlyHint</code>; they only read local files and the package index.</p>
<p class="next"><a href="configuration.html">Configuration →</a></p>
"""

P["configuration"] = f"""
<h1>Configuration</h1>
<p class="lede">There is no config file. Everything is a flag, an Action input or an environment variable, and the defaults describe a typical 2026 deployment.</p>
<h2 id="python">Target Python</h2>
<p><code>-p / --python</code>, env <code>WHEELRIPE_PYTHON</code>, default <code>3.15</code>. Accepts <code>3.15</code>, <code>3.15t</code>, <code>cp315</code>, <code>315t</code>. The <code>t</code> suffix means the free-threaded build, which has its own ABI: a <code>cp315</code> or <code>abi3</code> wheel does not load there, a <code>cp315t</code> or <code>abi3t</code> one does.</p>
<h2 id="platforms">Platforms</h2>
<p><code>--platform</code> (repeat or comma-separate), or <code>--all-platforms</code>. Default <code>linux-x86_64, macos-arm64, windows-amd64</code>. Keys are listed in the <a href="reference.html#platforms">reference</a>.</p>
<h2 id="floors">Floors: glibc, musl, macOS</h2>
<p>A wheel tagged <code>manylinux_2_39_x86_64</code> needs glibc 2.39; on a 2.34 host pip skips it. Set the oldest system you deploy on and wheelripe judges with that floor, and tells you when the only wheels are for newer systems.</p>
<table><thead><tr><th>Flag</th><th>Default</th><th>Applies to</th></tr></thead><tbody>
<tr><td><code>--glibc</code></td><td>2.34</td><td><code>linux-*</code> (manylinux tags, including manylinux1/2010/2014)</td></tr>
<tr><td><code>--musl</code></td><td>1.2</td><td><code>musllinux-*</code></td></tr>
<tr><td><code>--macos</code></td><td>14</td><td><code>macos-*</code> (arm64 also accepts universal2)</td></tr>
</tbody></table>
<h2 id="fail-on">Failing</h2>
<p><code>--fail-on</code> sets the exit-1 threshold: <code>bump</code> (default: anything you have to change), <code>blocked</code> (only upstream blockers — useful for a "tell me when" job), <code>build</code> (also sdist-only packages), <code>never</code> (report only).</p>
<h2 id="pre">Pre-releases</h2>
<p><code>--pre</code> lets the suggested bump be a pre-release. Early in a CPython cycle, many projects ship wheels in an rc first.</p>
<h2 id="index">Indexes</h2>
<p>By default wheelripe asks the PyPI JSON API. For anything else — PyTorch's <code>download.pytorch.org</code>, Artifactory, Nexus, devpi, GitLab — give it the simple-API URL you would give pip. It asks for PEP 691 JSON (<code>Accept: application/vnd.pypi.simple.v1+json</code>) and reads PEP 503 HTML when that is what comes back.</p>
<table><thead><tr><th>Where</th><th>Effect</th></tr></thead><tbody>
<tr><td><code>-i / --index-url URL</code></td><td>Replaces PyPI for every package, including ones a lockfile pinned to another registry. PyPI is not asked about your package names unless you list it.</td></tr>
<tr><td><code>--extra-index-url URL</code></td><td>Repeatable. Files from every index are pooled, as pip does, so a newer release on any of them counts.</td></tr>
<tr><td>requirements files</td><td><code>-i</code>, <code>--index-url</code> and <code>--extra-index-url</code> lines, also inside <code>-r</code> includes, with pip's <code>${{VAR}}</code> expansion. The last <code>-i</code> wins, as in pip.</td></tr>
<tr><td>lockfiles</td><td>Each package is checked on the registry it was locked from: <code>source = {{ registry = … }}</code> in <code>uv.lock</code>, a <code>legacy</code> source in <code>poetry.lock</code>, <code>index</code> in <code>pylock.toml</code>, <code>_meta.sources</code> in <code>Pipfile.lock</code>.</td></tr>
<tr><td>environment</td><td><code>PIP_INDEX_URL</code>, <code>UV_DEFAULT_INDEX</code> or <code>UV_INDEX_URL</code> for the main index; <code>PIP_EXTRA_INDEX_URL</code>, <code>UV_INDEX</code> and <code>UV_EXTRA_INDEX_URL</code> (space-separated) for extras.</td></tr>
<tr><td><code>--index URL</code></td><td>A mirror of the PyPI <em>JSON</em> API (<code>&lt;index&gt;/&lt;name&gt;/json</code>), env <code>WHEELRIPE_INDEX</code>.</td></tr>
</tbody></table>
<p>Order: <code>--index-url</code> &gt; the lockfile's registry for that package &gt; <code>-i</code> in requirements files &gt; environment &gt; PyPI. Extra indexes from all of them are added together. <code>https://pypi.org/simple</code> anywhere in the list means PyPI's JSON API, which also gives wheelripe the upstream links for BLOCKED packages.</p>
<h3 id="local">Local versions</h3>
<p>Versions come from file names (<code>packaging.utils</code>), so <code>torch-2.14.0+cpu-cp315-cp315-win_amd64.whl</code> is version <code>2.14.0+cpu</code>. Matching follows PEP 440, like pip: <code>torch==2.14.0</code> accepts <code>2.14.0+cpu</code> on Linux and Windows and plain <code>2.14.0</code> on macOS; <code>torch==2.14.0+cpu</code> accepts only the <code>+cpu</code> builds, so on macOS it reports the first release that has one. CI compares these verdicts with pip on the live PyTorch index for 3.15 and 3.15t.</p>
<h3 id="credentials">Credentials</h3>
<p>Put them in the URL (<code>https://USER:TOKEN@host/simple</code>; percent-encode an <code>@</code> or <code>:</code> inside the token) or in <code>~/.netrc</code> (or the file named by <code>NETRC</code>). They are sent as HTTP Basic auth to that host only — including lockfile registries on the same host, which uv records without credentials — and are dropped on a redirect to another host. wheelripe never prints them: every URL in the report, warnings, annotations, MCP results and error messages is redacted.</p>
<h3 id="index-errors">When an index says no</h3>
<table><thead><tr><th>Answer</th><th>What wheelripe does</th></tr></thead><tbody>
<tr><td>404 / 410</td><td>The package is not on that index. If no index has it, it is <code>MISSING</code> and the note says what each index answered. An index that has none of your packages gets a warning: is the URL the <code>/simple</code> root?</td></tr>
<tr><td>401</td><td>As in pip, that index adds no files for the package; a warning says whether credentials were missing or rejected.</td></tr>
<tr><td>403</td><td>The same, but a warning appears only when the index refused <em>every</em> package (download.pytorch.org answers 403 for projects it does not host).</td></tr>
<tr><td>a web page, XML, the PyPI JSON API or website</td><td>Exit code 2, with the URL and a hint. A login page or web UI is not a simple index, and a verdict built from it would be wrong.</td></tr>
<tr><td>5xx, timeouts</td><td>Three attempts, then exit code 2.</td></tr>
</tbody></table>
<p>Warnings go to stderr, into the JSON report (<code>warnings</code>), the Markdown summary and, with <code>--annotations</code>, a <code>::warning</code> annotation.</p>
<h2 id="env">Environment variables</h2>
<table><thead><tr><th>Variable</th><th>Effect</th></tr></thead><tbody>
<tr><td><code>WHEELRIPE_PYTHON</code></td><td>Default for <code>--python</code>.</td></tr>
<tr><td><code>WHEELRIPE_INDEX</code></td><td>Default for <code>--index</code>.</td></tr>
<tr><td><code>PIP_INDEX_URL</code>, <code>UV_DEFAULT_INDEX</code>, <code>UV_INDEX_URL</code></td><td>Main simple index when neither <code>--index-url</code> nor a requirements-file <code>-i</code> is given.</td></tr>
<tr><td><code>PIP_EXTRA_INDEX_URL</code>, <code>UV_INDEX</code>, <code>UV_EXTRA_INDEX_URL</code></td><td>Extra simple indexes (space-separated; <code>UV_INDEX</code> entries may be <code>name=url</code>).</td></tr>
<tr><td><code>NETRC</code></td><td>netrc file with index credentials (default <code>~/.netrc</code>).</td></tr>
<tr><td><code>NO_COLOR</code></td><td>Plain text output.</td></tr>
<tr><td><code>HTTPS_PROXY</code></td><td>Standard proxy support.</td></tr>
<tr><td><code>SSL_CERT_FILE</code></td><td>CA bundle for an index behind a private certificate authority.</td></tr>
</tbody></table>
<h2 id="inputs">What gets read</h2>
<table><thead><tr><th>File</th><th>Notes</th></tr></thead><tbody>
<tr><td><code>uv.lock</code></td><td>Registry packages only, each checked on its own registry; git, path and URL sources are skipped.</td></tr>
<tr><td><code>pylock.toml</code></td><td>PEP 751 lockfiles.</td></tr>
<tr><td><code>poetry.lock</code>, <code>Pipfile.lock</code></td><td>All locked packages.</td></tr>
<tr><td><code>requirements*.txt</code></td><td>Follows <code>-r</code>, handles continuations and hashes, honours <code>-i</code> / <code>--extra-index-url</code>; skips other options, URLs and <code>-e</code>.</td></tr>
<tr><td><code>pyproject.toml</code></td><td><code>[project] dependencies</code>, optional dependencies and dependency groups.</td></tr>
</tbody></table>
<p>Environment markers are evaluated per target, so <code>pywin32; sys_platform == "win32"</code> is only checked for Windows and shows as skipped elsewhere.</p>
<p class="next"><a href="reference.html">Reference →</a></p>
"""

P["reference"] = f"""
<h1>Reference</h1>
<h2 id="flags">Flags</h2>
{code('''wheelripe [PATH|SPEC ...] [options]
wheelripe wheel FILE.whl ... [-p PY] [--platform KEY]
wheelripe mcp''')}
<table><thead><tr><th>Flag</th><th>Default</th><th>Meaning</th></tr></thead><tbody>
<tr><td><code>-p, --python</code></td><td>3.15</td><td>Target CPython; <code>t</code> = free-threaded.</td></tr>
<tr><td><code>--platform KEY</code></td><td>linux-x86_64, macos-arm64, windows-amd64</td><td>Repeat or comma-separate.</td></tr>
<tr><td><code>--all-platforms</code></td><td></td><td>Every known key.</td></tr>
<tr><td><code>--glibc / --musl / --macos</code></td><td>2.34 / 1.2 / 14</td><td>Oldest system you deploy on.</td></tr>
<tr><td><code>-f, --format</code></td><td>text</td><td><code>text</code>, <code>json</code>, <code>markdown</code>, <code>github</code>.</td></tr>
<tr><td><code>--fail-on</code></td><td>bump</td><td><code>bump</code>, <code>blocked</code>, <code>build</code>, <code>never</code>.</td></tr>
<tr><td><code>--show-ready</code></td><td></td><td>List ready packages one by one.</td></tr>
<tr><td><code>--pre</code></td><td></td><td>Allow pre-releases as the suggested bump.</td></tr>
<tr><td><code>-i, --index-url URL</code></td><td>PyPI</td><td>Simple-API index (PEP 691/503) to use instead of PyPI. Credentials in the URL are never printed.</td></tr>
<tr><td><code>--extra-index-url URL</code></td><td></td><td>One more simple-API index; repeatable; files are pooled.</td></tr>
<tr><td><code>--index URL</code></td><td>https://pypi.org/pypi</td><td>PyPI JSON API base, for a mirror of it.</td></tr>
<tr><td><code>--workers N</code></td><td>8</td><td>Parallel index requests.</td></tr>
<tr><td><code>-o, --output FILE</code></td><td>stdout</td><td>Write the report to a file.</td></tr>
<tr><td><code>--summary FILE</code></td><td></td><td>Append a Markdown summary (e.g. <code>$GITHUB_STEP_SUMMARY</code>).</td></tr>
<tr><td><code>--json-out FILE</code></td><td></td><td>Also write the JSON report.</td></tr>
<tr><td><code>--annotations</code></td><td></td><td>Also print <code>::error</code>/<code>::warning</code> lines.</td></tr>
<tr><td><code>-V, --version</code></td><td></td><td>Print the version.</td></tr>
</tbody></table>
<h2 id="exit">Exit codes</h2>
<table><thead><tr><th>Code</th><th>Meaning</th></tr></thead><tbody>
<tr><td>0</td><td>Nothing at or above the <code>--fail-on</code> level.</td></tr>
<tr><td>1</td><td>Something is; the report says what.</td></tr>
<tr><td>2</td><td>Usage error, unreadable input, an index that could not be reached, or one that answered with something that is not a simple index page.</td></tr>
</tbody></table>
<h2 id="statuses">Statuses</h2>
<table><thead><tr><th>Status</th><th>Meaning</th></tr></thead><tbody>
<tr><td><code>ready</code></td><td>A wheel for this version installs on the target.</td></tr>
<tr><td><code>skip</code></td><td>An environment marker excludes the package on this target.</td></tr>
<tr><td><code>build</code></td><td>The release has only an sdist (for every Python).</td></tr>
<tr><td><code>bump</code></td><td>No wheel at this version; <code>fix</code> names the first newer release with one.</td></tr>
<tr><td><code>blocked</code></td><td>No release has a wheel for the target yet.</td></tr>
<tr><td><code>excluded</code></td><td>The release's <code>requires-python</code> excludes the target.</td></tr>
<tr><td><code>missing</code></td><td>The package or version is not on the index.</td></tr>
</tbody></table>
<p>A package's overall status is its worst status across the platforms you asked for.</p>
<p>Yanked files (PEP 592) are skipped, as pip skips them — except when an exact pin selects a yanked release: pip installs it then, with a warning, so wheelripe judges that release and marks the package <code>"yanked": true</code>.</p>
<h2 id="platforms">Platform keys</h2>
<table><thead><tr><th>Key</th><th>Wheel tags accepted</th></tr></thead><tbody>
<tr><td><code>linux-x86_64</code>, <code>linux-aarch64</code></td><td><code>manylinux_X_Y_ARCH</code> with X.Y ≤ glibc floor, legacy <code>manylinux1/2010/2014</code>, <code>linux_ARCH</code></td></tr>
<tr><td><code>musllinux-x86_64</code>, <code>musllinux-aarch64</code></td><td><code>musllinux_X_Y_ARCH</code> with X.Y ≤ musl floor</td></tr>
<tr><td><code>macos-arm64</code></td><td><code>macosx_X_Y_arm64</code> / <code>universal2</code>, X ≤ macOS floor</td></tr>
<tr><td><code>macos-x86_64</code></td><td><code>x86_64</code>, <code>intel</code>, <code>universal</code>, <code>universal2</code> …</td></tr>
<tr><td><code>windows-amd64</code>, <code>windows-arm64</code>, <code>windows-x86</code></td><td><code>win_amd64</code>, <code>win_arm64</code>, <code>win32</code></td></tr>
</tbody></table>
<h2 id="abi">Interpreter and ABI rules</h2>
<table><thead><tr><th>Wheel</th><th>3.15 (GIL)</th><th>3.15t (free-threaded)</th></tr></thead><tbody>
<tr><td><code>cp315-cp315</code></td><td>yes</td><td>no</td></tr>
<tr><td><code>cp315-cp315t</code></td><td>no</td><td>yes</td></tr>
<tr><td><code>cp3X-abi3</code>, X ≤ 15</td><td>yes</td><td>no</td></tr>
<tr><td><code>cp3X-abi3t</code>, 14 ≤ X ≤ 15 (PEP 803)</td><td>no</td><td>yes</td></tr>
<tr><td><code>cp3X-abi3.abi3t</code></td><td>yes</td><td>yes</td></tr>
<tr><td><code>py3-none-any</code></td><td>yes</td><td>yes</td></tr>
</tbody></table>
<h2 id="json">JSON report</h2>
{code('''{
  "tool": "wheelripe",
  "python": "3.15",
  "platforms": ["linux-x86_64", "macos-arm64", "windows-amd64"],
  "sources": ["uv.lock"],
  "ripe": false,
  "summary": {"ready": 1, "skip": 0, "build": 0, "bump": 2,
              "blocked": 1, "excluded": 0, "missing": 0},
  "packages": [
    {
      "name": "numpy", "version": "1.26.4", "pinned": true,
      "status": "bump", "fix": "2.5.2",
      "platforms": {"linux-x86_64": {"status": "bump", "note": "..."}},
      "latest": "2.5.4", "ships": ["cp39", "cp310", "cp311", "cp312"],
      "requires_python": ">=3.9",
      "upstream": "https://github.com/numpy/numpy",
      "origin": "uv.lock"
    }
  ]
}''')}
<p>Packages are sorted worst first. Fields that do not apply are omitted. Two top-level fields appear only when they have something to say: <code>indexes</code> (the indexes consulted, redacted, when anything besides PyPI was used, e.g. <code>["download.pytorch.org/whl/cpu", "pypi.org"]</code>) and <code>warnings</code> (index problems such as a 401, also printed to stderr).</p>
<p class="next"><a href="faq.html">FAQ →</a></p>
"""

P["faq"] = f"""
<h1>FAQ</h1>
<h2 id="pip">Why not just run <code>pip install</code> on the new Python?</h2>
<p>You can, and you should eventually. But pip stops at the first package that needs a compiler, tells you nothing about the others, needs the target interpreter and each OS, and never tells you which newer version would have worked. wheelripe answers for every package and every platform at once, from any machine, in a couple of seconds.</p>
<h2 id="right">How do I know it agrees with pip?</h2>
<p>CI checks it against pip itself on every push: for 26 real packages on 7 platforms, for both 3.15 and 3.15t, it asks <code>pip install --dry-run --only-binary=:all: --python-version … --abi … --platform …</code> and fails on any disagreement. Then it installs on real CPython 3.15 and 3.15t interpreters and compares again.</p>
<h2 id="abi3">My package ships abi3 wheels. Is it ready for 3.15t?</h2>
<p>No. <code>abi3</code> is the stable ABI of GIL-enabled builds; free-threaded CPython cannot load it. PEP 803 adds <code>abi3t</code> for that, starting at 3.14. A wheel tagged <code>abi3.abi3t</code> works on both. wheelripe applies the PEP 803 table exactly.</p>
<h2 id="glibc">It says BLOCKED, but I can see a cp315 wheel on PyPI.</h2>
<p>Check the platform tag. If it is <code>manylinux_2_39</code> and your floor is 2.34 (the default), pip on your servers will not use it. The report says “wheels exist for a newer glibc”; raise <code>--glibc</code> if your hosts really are newer.</p>
<h2 id="build">Is BUILD bad?</h2>
<p>Not necessarily. Many pure-Python packages publish only an sdist; they build in seconds with no compiler. It counts as a failure only with <code>--fail-on build</code>.</p>
<h2 id="pypi">Does it need PyPI?</h2>
<p>No. PyPI's JSON API is the default, but any PEP 691 / PEP 503 simple index works with <code>--index-url</code> and <code>--extra-index-url</code>: PyTorch's <code>download.pytorch.org/whl/cpu</code> (or <code>/cu126</code> …), Artifactory, Nexus, devpi, GitLab. Index lines in your requirements file and the registries recorded in <code>uv.lock</code> are picked up automatically. See <a href="configuration.html#index">Indexes</a>.</p>
<h2 id="lock">Does it change my lockfile?</h2>
<p>No. It only reads. The suggested bump is printed; you apply it with your own tool (<code>uv lock --upgrade-package numpy</code>, etc.).</p>
<h2 id="other">Other interpreters?</h2>
<p>CPython only for now — that is where the per-version wheel problem lives. PyPy and GraalPy are possible later.</p>
<h2 id="name">Why cheese?</h2>
<p>Python wheels are named after cheese wheels, and PyPI was once called the Cheese Shop. A ripe Emmental has holes; so does a lockfile on a brand-new Python.</p>
<p class="next"><a href="https://github.com/Blackman99/wheelripe/issues">Ask something else →</a></p>
"""

BLOG = [
    ("which-dependencies-block-python-3-15", "en",
     "Which of your dependencies have no wheel for Python 3.15 yet?",
     "Python 3.15 is out and free-threading is supported. Here is why upgrades stall on wheels, and a tool that tells you exactly which ones."),
    ("python-3-15-wheel-check-zh", "zh-CN",
     "升级 Python 3.15 前，先查清哪些依赖还没有 wheel",
     "3.15 发布、3.15t 自由线程转正之后，升级卡在哪？wheelripe 一次查完整个 lockfile。"),
]

B = {}
B["which-dependencies-block-python-3-15"] = f"""
<p class="lede">CPython 3.15 shipped in October 2026, and the free-threaded build (3.15t) is now supported. Within days the same issue appears on dozens of trackers: “add Python 3.15 support”, followed by a hand-made list of which dependencies do and do not have wheels yet.</p>
<h2>The question nobody's tooling answers</h2>
<p>Look at <a href="https://github.com/tconbeer/harlequin/issues/1119">harlequin#1119</a> or <a href="https://github.com/jaehyeon-kim/dynamic-des/issues/40">dynamic-des#40</a>: maintainers open PyPI for each dependency, scroll the file list and look for <code>cp315</code>. Then they do it again for 3.15t, where <code>abi3</code> wheels do not count. Then again for macOS and Windows. Then again next week.</p>
<p>pip does not help much: it stops at the first package that needs a compiler, needs the target interpreter on each OS, and never says which newer release would have worked. Dependabot and Renovate bump versions but do not know about wheel tags.</p>
<h2>What wheelripe does</h2>
<p>It reads your lockfile (uv, PEP 751, Poetry, Pipenv, requirements, pyproject) and fetches each package's file list from the PyPI JSON API — one request per package. For each target platform it applies the same tag rules pip uses: interpreter, ABI (including PEP 803 <code>abi3t</code>), and platform with your glibc/macOS floor.</p>
{code('''$ uvx --from git+https://github.com/Blackman99/wheelripe wheelripe uv.lock -p 3.15
BLOCKED  confluent-kafka 2.16.0          no cp315 wheel for any target platform (ships cp38–cp314)
BUMP     numpy 1.26.4 → 2.5.2            no cp315 wheel in 1.26.4 (ships cp39–cp312); 2.5.2 is the first release with one
NOT RIPE for CPython 3.15: 1 blocked upstream · 1 needs a version bump''')}
<p>The useful distinction is <b>bump</b> versus <b>blocked</b>. A bump you can do today. A blocker you wait on — the report links the upstream project so you can watch it.</p>
<h2>Trusting the answer</h2>
<p>Tag matching has corners: manylinux floors, legacy aliases, universal2, abi3 on free-threaded builds, compressed tag sets. So wheelripe's CI asks pip itself (<code>--dry-run --only-binary=:all:</code> with explicit Python/ABI/platform) about 26 real packages on 7 platforms for both 3.15 and 3.15t, and installs on real 3.15/3.15t interpreters. Any disagreement turns CI red.</p>
<h2>Make CI tell you</h2>
{code('''- uses: Blackman99/wheelripe@v0.1.0
  with:
    python-version: "3.15t"
    fail-on: blocked''')}
<p>Run it weekly. The job goes green the week your last blocker ships a wheel. There is also an MCP server so a coding agent can check before it touches <code>requires-python</code>.</p>
<p><a href="../docs/">Docs</a> · <a href="https://github.com/Blackman99/wheelripe">GitHub</a></p>
"""
B["python-3-15-wheel-check-zh"] = f"""
<p class="lede">2026 年 10 月 CPython 3.15 正式发布，自由线程版本 3.15t 也进入“受支持”阶段。随后各个项目的 issue 区开始出现同一类帖子：“支持 Python 3.15”，下面是一张手工整理的依赖清单——谁有 wheel、谁还没有。</p>
<h2>升级卡在哪</h2>
<p>看看 <a href="https://github.com/tconbeer/harlequin/issues/1119">harlequin#1119</a> 和 <a href="https://github.com/jaehyeon-kim/dynamic-des/issues/40">dynamic-des#40</a>：维护者逐个打开 PyPI 页面，翻文件列表找 <code>cp315</code>；再为 3.15t 查一遍（<code>abi3</code> wheel 在自由线程版上不能用）；再为 macOS、Windows 各查一遍；下周再来一遍。</p>
<p>pip 帮不上太多：遇到第一个需要编译的包就停下，需要在每个系统上装好目标解释器，也不会告诉你哪个新版本已经有 wheel。Dependabot / Renovate 会升版本，但不懂 wheel 标签。</p>
<h2>wheelripe 做什么</h2>
<p>读取 lockfile（uv、PEP 751、Poetry、Pipenv、requirements、pyproject），每个包向 PyPI JSON API 发一次请求，按 pip 的规则逐平台匹配解释器、ABI（含 PEP 803 的 <code>abi3t</code>）和平台标签（含 glibc / macOS 下限）。</p>
{code('''$ uvx --from git+https://github.com/Blackman99/wheelripe wheelripe uv.lock -p 3.15t''')}
<p>关键是区分 <b>BUMP</b>（升到某个版本就有 wheel，今天就能改）和 <b>BLOCKED</b>（任何版本都还没有，只能等上游；报告里附上游链接）。</p>
<h2>结果可信吗</h2>
<p>CI 每次提交都会让 pip 本身（<code>--dry-run --only-binary=:all:</code>，显式指定 Python/ABI/平台）对 26 个真实包、7 个平台、3.15 与 3.15t 逐一给出答案，与 wheelripe 不一致就失败；还会在真实的 3.15 / 3.15t 解释器上安装验证。</p>
<h2>让 CI 每周告诉你</h2>
{code('''- uses: Blackman99/wheelripe@v0.1.0
  with:
    python-version: "3.15t"
    fail-on: blocked''')}
<p>最后一个阻塞依赖发布 wheel 的那一周，任务会变绿。另有 MCP server，编码 agent 改 <code>requires-python</code> 前可以先问一下。</p>
<p><a href="../docs/">文档</a> · <a href="https://github.com/Blackman99/wheelripe">GitHub</a></p>
"""


def head(title, desc, url, lang="en", depth="../"):
    t, d = html.escape(title), html.escape(desc)
    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}</title>
<meta name="description" content="{d}">
<link rel="canonical" href="{url}">
<link rel="icon" href="{depth}favicon.svg" type="image/svg+xml">
<link rel="icon" href="{depth}logo-512.png" type="image/png" sizes="512x512">
<link rel="apple-touch-icon" href="{depth}logo-512.png">
<meta name="theme-color" content="#e8742a">
<meta property="og:type" content="{'article' if 'blog' in url else 'website'}">
<meta property="og:site_name" content="wheelripe">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{BASE}og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="A cheese wedge whose holes are Python packages with no cp315 wheel yet.">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{t}">
<meta name="twitter:description" content="{d}">
<meta name="twitter:image" content="{BASE}og.png">
<link rel="preload" href="{depth}fonts/bricolage-grotesque.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{depth}docs.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="band">
  <a class="brand" href="{depth}"><img src="{depth}favicon.svg" width="30" height="30" alt=""> wheelripe</a>
  <nav aria-label="Site"><a href="{depth}docs/">Docs</a><a href="{depth}blog/">Notes</a><a href="https://github.com/Blackman99/wheelripe">GitHub</a></nav>
</header>
"""


FOOT = """<footer class="foot"><p>wheelripe · MIT · <a href="https://github.com/Blackman99/wheelripe">GitHub</a> · <a href="https://github.com/Blackman99/wheelripe/issues">Issues</a></p></footer>
</body>
</html>
"""


def side(current):
    items = []
    for slug, label, _ in DOCS:
        href = "./" if slug == "index" else f"{slug}.html"
        cur = ' aria-current="page"' if slug == current else ""
        items.append(f'<li><a href="{href}"{cur}><i aria-hidden="true"></i>{label}</a></li>')
    return '<nav class="side" aria-label="Docs"><ol>' + "".join(items) + "</ol></nav>"


def main():
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "blog").mkdir(exist_ok=True)
    for slug, label, desc in DOCS:
        fn = "index.html" if slug == "index" else f"{slug}.html"
        url = BASE + "docs/" + ("" if slug == "index" else fn)
        title = f"{label} · wheelripe docs" if slug != "index" else "wheelripe docs"
        body = head(title, desc, url) + f'<div class="docs">{side(slug)}<main id="main" class="page">{P[slug]}</main></div>' + FOOT
        (ROOT / "docs" / fn).write_text(body, encoding="utf-8")
    cards = []
    for slug, lang, title, desc in BLOG:
        url = BASE + f"blog/{slug}.html"
        art = (head(title, desc, url, lang) + f'<main id="main" class="page article"><p class="kicker">Notes · 2026-10-13</p><h1>{html.escape(title)}</h1>'
               + B[slug] + "</main>" + FOOT)
        (ROOT / "blog" / f"{slug}.html").write_text(art, encoding="utf-8")
        cards.append(f'<li lang="{lang}"><a href="{slug}.html"><b>{html.escape(title)}</b></a><span>{html.escape(desc)}</span></li>')
    idx = (head("Notes · wheelripe", "Writing about Python upgrades, wheels and wheelripe.", BASE + "blog/")
           + '<main id="main" class="page"><h1>Notes</h1><ol class="posts">' + "".join(cards) + "</ol></main>" + FOOT)
    (ROOT / "blog" / "index.html").write_text(idx, encoding="utf-8")
    print("docs + blog written")


if __name__ == "__main__":
    main()
