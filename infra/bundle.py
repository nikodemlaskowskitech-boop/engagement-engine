"""
Gathers each Lambda's code into one folder under infra/build/ (git-ignored):
its handler plus the few repo files it runs. Plain Python, no CDK, so the
tests can build exactly what gets deployed.

The engine Lambda also needs DuckDB and the Anthropic library, built for
Lambda's machine (Linux on arm64, Python 3.13), not for the laptop. pip can
fetch those builds from any laptop (--platform), so no Docker is needed. The
versions come from requirements.txt, so the laptop and AWS always match.
"""

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
                "to_parquet.py", "views_template.html", "handover.py", "llm.py", "stories",
                "pull_clicks.py",                       # its combine() merges the Slack clicks
                "roles.py"]                             # export_views.py builds the role pages with it
NEVER_BUNDLE = ["answer_key.json", "README.md"]     # stories/: the test's answer key never reaches AWS
LAMBDA_PACKAGES = ["duckdb", "anthropic"]           # third-party libraries the engine needs, pinned in requirements.txt
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


def bundle_search(out=None, deps=None):
    """The public search function: handler, SEARCH_FILES, the redacted documents, DuckDB.
    deps=False (or CAIRNPATH_SKIP_DEPS) leaves DuckDB out, for tests."""
    sys.path.insert(0, str(REPO))
    from settings import SETTINGS          # where search_export.py wrote the redacted file
    out = _fresh(out or BUILD / "search", "search")
    for name in SEARCH_FILES:
        shutil.copy(REPO / name, out)
    shutil.copy(REPO / SETTINGS["search"]["site_documents_file"], out / "documents.jsonl")
    if deps is None:
        deps = not os.environ.get("CAIRNPATH_SKIP_DEPS")
    if deps:
        shutil.copytree(lambda_deps(SEARCH_PACKAGES), out, dirs_exist_ok=True)
    return out


def pinned(package):
    """The version requirements.txt pins for a package (one source of truth for laptop and AWS)."""
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    return re.search(rf"^{re.escape(package)}==([\w.]+)", text, re.M).group(1)


def lambda_deps(packages=None):
    """The engine's libraries built for Lambda (Linux arm64, Python 3.13), downloaded once per
    set of versions and reused. pip picks the Linux builds even on a Windows laptop."""
    pins = [f"{p}=={pinned(p)}" for p in (packages or LAMBDA_PACKAGES)]
    cache = BUILD / ("deps_" + "_".join(pin.replace("==", "-") for pin in pins))
    if not (cache / ".complete").exists():
        shutil.rmtree(cache, ignore_errors=True)
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(cache),
               "--implementation", "cp", "--python-version", "3.13", "--only-binary=:all:",
               *[a for p in LAMBDA_PLATFORMS for a in ("--platform", p)], *pins]
        print(f"Fetching {', '.join(pins)} for Lambda (Linux arm64, Python 3.13) ...")
        subprocess.run(cmd, check=True)
        (cache / ".complete").write_text("ok")
    return cache


def bundle_engine(out=None, deps=None):
    """The engine Lambda: handler + ENGINE_FILES (stories/ without its answer key) + libraries.
    deps=False (or the CAIRNPATH_SKIP_DEPS environment variable) leaves the libraries out, for tests."""
    out = _fresh(out or BUILD / "run_engine", "run_engine")
    for name in ENGINE_FILES:
        if (REPO / name).is_dir():
            shutil.copytree(REPO / name, out / name, ignore=shutil.ignore_patterns(*NEVER_BUNDLE))
        else:
            shutil.copy(REPO / name, out)
    if deps is None:
        deps = not os.environ.get("CAIRNPATH_SKIP_DEPS")
    if deps:
        shutil.copytree(lambda_deps(), out, dirs_exist_ok=True)
    return out
