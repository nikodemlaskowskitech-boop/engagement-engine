"""
Gathers each Lambda's code into one folder under infra/build/ (git-ignored):
its handler plus the few repo files it runs. Plain Python, no CDK, so the
tests can build exactly what gets deployed.

The engine Lambda also needs DuckDB and the Anthropic library, built for
Lambda's machine (Linux on arm64, Python 3.13), not for the laptop. pip can
fetch those builds from any laptop (--platform), so no Docker is needed. The
versions come from requirements.txt, so the laptop and AWS always match.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

INFRA = Path(__file__).resolve().parent
REPO = INFRA.parent
BUILD = INFRA / "build"

# What each Lambda needs from the repo, and nothing else (never settings.local.toml)
CHECK_FILES = ["check_inputs.py", "settings.py", "settings.toml"]
SLACK_FILES = ["slack_loop.py", "settings.py", "settings.toml"]   # the Slack listener: plain Python, no libraries
# The public search: the search code and the redacted documents (in its handler folder), nothing
# else. No stories, no contacts, no handover.py: it can't redact, so it only ever holds redacted text.
SEARCH_FILES = ["search.py", "documents.py", "settings.py", "settings.toml"]
SEARCH_PACKAGES = ["duckdb"]
ENGINE_FILES = ["settings.py", "settings.toml", "cairnpath_db.py", "ingest.py", "facts.py", "documents.py", "flags.py", "journeys.py",
                "team_report.py", "backtest.py", "onboarding_report.py", "history.py", "retention.py", "export_views.py",
                "to_parquet.py", "views_template.html", "site_search.html", "handover.py", "llm.py", "stories",
                "pull_clicks.py",                       # its combine() merges the Slack clicks
                "roles.py",                             # export_views.py builds the role pages with it
                "slack_loop.py",                        # export_views.py takes the meeting outcome labels from it
                                                        # (since #194; missing until 20 Nov 2026: the AWS export failed)
                "embed.py", "search_export.py",         # search.py's meaning mode imports them (23 Nov 2026); the
                                                        # engine stays on keywords, so they never call Bedrock here
                "research.py", "search.py",             # the account page's research (20 Nov 2026), with the search...
                "site_documents.jsonl"]                 # ...from the redacted documents the public search
                                                        # carries: never the raw corpus (bundled from
                                                        # [search] site_documents_file under this name)
NEVER_BUNDLE = ["answer_key.json", "README.md"]     # stories/: the test's answer key never reaches AWS
LAMBDA_PACKAGES = ["duckdb", "anthropic"]           # third-party libraries the engine needs, pinned in requirements.txt
# The Lambdas' libraries, sub-dependencies included, each pinned to one file by its hash
# (17 Nov 2026, supply chain): a hijacked release of httpx or pydantic can't slip into the
# function that reads the API key. Made on the laptop by infra/lock_lambda.py.
LOCKS = {"engine": INFRA / "locks" / "engine.txt", "search": INFRA / "locks" / "search.txt"}
LAMBDA_PLATFORMS = ["manylinux_2_28_aarch64", "manylinux_2_27_aarch64", "manylinux2014_aarch64"]


def _fresh(out, handler):
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copy(INFRA / "lambdas" / handler / "handler.py", out)
    return out


def bundle_check_inputs(out=None):
    out = _fresh(out or BUILD / "check_inputs", "check_inputs")
    for name in CHECK_FILES:
        shutil.copy(REPO / name, out)
    return out


def bundle_slack(out=None):
    out = _fresh(out or BUILD / "slack", "slack")
    for name in SLACK_FILES:
        shutil.copy(REPO / name, out)
    return out


def site_vectors(out, vectors=None):
    """Search by meaning on the site (23 Nov 2026): the laptop's vectors (embed.py, data/), copied
    only when [aws] search_meaning is on and every redacted document the function carries has a
    vector from [embed] model whose text hash matches: a stale or partial file would rank by old
    text, so then the function stays on keywords. Returns "hybrid" or "keywords"."""
    sys.path.insert(0, str(REPO))
    import embed
    from settings import SETTINGS
    target = out / "vectors.jsonl"
    if target.exists():
        target.unlink()
    if not SETTINGS["aws"].get("search_meaning"):
        return "keywords"
    docs = [json.loads(x) for x in (out / "documents.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    have = embed.load(Path(vectors) if vectors else embed.VECTORS)
    model, dims = SETTINGS["embed"]["model"], int(SETTINGS["embed"]["dimensions"])
    todo, _ = embed.plan(docs, have, model, dims)
    if not docs or todo:
        print("search: no up-to-date vectors for the site (run python embed.py), so keywords only")
        return "keywords"
    rows = [have[(d["account_id"], d["id"])] for d in docs]       # only the documents it carries
    target.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows), encoding="utf-8")
    return "hybrid"


def site_accounts(out, export=None):
    """The public MCP server's account list (24 Nov 2026): the search page's own accounts (name,
    tier, ARR, open flags; roles.search_accounts) with the export's as-of date, so its answers
    say how old they are. Built at deploy from the laptop's views_data.json; none = no file,
    and list_accounts answers an empty list."""
    sys.path.insert(0, str(REPO))
    from settings import OUTPUTS, SETTINGS
    target = out / "accounts.json"
    if export is None:
        src = OUTPUTS / "views_data.json"
        if not src.exists():
            return None
        export = json.loads(src.read_text(encoding="utf-8"))
    import roles
    target.write_text(json.dumps({"as_of": export["as_of"], "currency": SETTINGS["money"]["currency"],
                                  "accounts": roles.search_accounts(export)},
                                 separators=(",", ":")), encoding="utf-8")
    return target


def search_mode(out):
    """What a built search bundle will do: hybrid when it carries vectors."""
    return "hybrid" if (Path(out) / "vectors.jsonl").exists() else "keywords"


def bundle_search(out=None, deps=None, vectors=None):
    """The public search function: handler, SEARCH_FILES, the redacted documents, DuckDB, and
    the documents' vectors when they're up to date (site_vectors). deps=False (or
    CAIRNPATH_SKIP_DEPS) leaves DuckDB out, for tests; vectors = another vectors file (tests)."""
    sys.path.insert(0, str(REPO))
    from settings import SETTINGS          # where search_export.py wrote the redacted file
    out = _fresh(out or BUILD / "search", "search")
    for name in SEARCH_FILES:
        shutil.copy(REPO / name, out)
    shutil.copy(REPO / SETTINGS["search"]["site_documents_file"], out / "documents.jsonl")
    site_vectors(out, vectors)
    shutil.copy(INFRA / "lambdas" / "search" / "mcp_public.py", out)
    site_accounts(out)
    if deps is None:
        deps = not os.environ.get("CAIRNPATH_SKIP_DEPS")
    if deps:
        shutil.copytree(lambda_deps(SEARCH_PACKAGES), out, dirs_exist_ok=True)
    return out


def pinned(package):
    """The version requirements.txt pins for a package (one source of truth for laptop and AWS)."""
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    return re.search(rf"^{re.escape(package)}==([\w.]+)", text, re.M).group(1)


def lock_for(packages):
    """The lock file for a set of packages, after checking it still matches requirements.txt:
    a changed pin with an old lock stops the build (run python infra/lock_lambda.py)."""
    name = "search" if list(packages) == SEARCH_PACKAGES else "engine"
    path = LOCKS[name]
    if not path.exists():
        sys.exit(f"{path} is missing: run python infra/lock_lambda.py (laptop, venv on)")
    locked = dict(re.findall(r"^([\w.-]+)==([\w.]+) --hash=sha256:[0-9a-f]{64}$", path.read_text(encoding="utf-8"), re.M))
    stale = [p for p in packages if locked.get(p.lower()) != pinned(p)]
    if stale:
        sys.exit(f"{path} doesn't match requirements.txt for {', '.join(stale)}: run python infra/lock_lambda.py")
    return path


def lambda_deps(packages=None):
    """The engine's libraries built for Lambda (Linux arm64, Python 3.13), downloaded once per
    lock and reused. pip picks the Linux builds even on a Windows laptop. Exactly the locked
    files: --require-hashes refuses a file whose hash differs, --no-deps anything not locked."""
    packages = packages or LAMBDA_PACKAGES
    lock = lock_for(packages)
    pins = [f"{p}=={pinned(p)}" for p in packages]
    tag = hashlib.sha256(lock.read_bytes()).hexdigest()[:8]
    cache = BUILD / ("deps_" + "_".join(pin.replace("==", "-") for pin in pins) + "_" + tag)
    if not (cache / ".complete").exists():
        shutil.rmtree(cache, ignore_errors=True)
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(cache),
               "--implementation", "cp", "--python-version", "3.13", "--only-binary=:all:",
               *[a for p in LAMBDA_PLATFORMS for a in ("--platform", p)],
               "--require-hashes", "--no-deps", "-r", str(lock)]
        print(f"Fetching {', '.join(pins)} for Lambda (Linux arm64, Python 3.13) ...")
        subprocess.run(cmd, check=True)
        (cache / ".complete").write_text("ok")
    return cache


def bundle_engine(out=None, deps=None):
    """The engine Lambda: handler + ENGINE_FILES (stories/ without its answer key) + libraries.
    deps=False (or the CAIRNPATH_SKIP_DEPS environment variable) leaves the libraries out, for tests."""
    out = _fresh(out or BUILD / "run_engine", "run_engine")
    from settings import SETTINGS
    for name in ENGINE_FILES:
        if name == "site_documents.jsonl":                      # the redacted file, under a flat name
            shutil.copy(REPO / SETTINGS["search"]["site_documents_file"], out / name)
        elif (REPO / name).is_dir():
            shutil.copytree(REPO / name, out / name, ignore=shutil.ignore_patterns(*NEVER_BUNDLE))
        else:
            shutil.copy(REPO / name, out)
    if deps is None:
        deps = not os.environ.get("CAIRNPATH_SKIP_DEPS")
    if deps:
        shutil.copytree(lambda_deps(), out, dirs_exist_ok=True)
    return out
