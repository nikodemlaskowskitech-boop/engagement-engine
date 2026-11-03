"""
Gathers each Lambda's code into one folder under infra/build/ (git-ignored):
its handler plus the few repo files it runs. Plain Python, no CDK, so the
tests can build exactly what gets deployed.

The engine Lambda also needs DuckDB, built for Lambda's machine (Linux on
arm64, Python 3.13), not for the laptop. pip can fetch that build from any
laptop (--platform), so no Docker is needed. The version comes from
requirements.txt, so the laptop and AWS always run the same DuckDB.
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
ENGINE_FILES = ["settings.py", "settings.toml", "cairnpath_db.py", "flags.py", "journeys.py",
                "team_report.py", "backtest.py", "onboarding_report.py", "export_views.py",
                "views_template.html"]
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


def duckdb_version():
    """The pinned DuckDB version, from requirements.txt (one source of truth)."""
    text = (REPO / "requirements.txt").read_text(encoding="utf-8")
    return re.search(r"^duckdb==([\w.]+)", text, re.M).group(1)


def lambda_deps():
    """DuckDB for Lambda (Linux arm64, Python 3.13), downloaded once per version and reused."""
    version = duckdb_version()
    cache = BUILD / f"deps_duckdb_{version}"
    if not (cache / "duckdb").is_dir():
        shutil.rmtree(cache, ignore_errors=True)
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(cache),
               "--implementation", "cp", "--python-version", "3.13", "--only-binary=:all:",
               *[a for p in LAMBDA_PLATFORMS for a in ("--platform", p)], f"duckdb=={version}"]
        print(f"Fetching DuckDB {version} for Lambda (Linux arm64, Python 3.13) ...")
        subprocess.run(cmd, check=True)
    return cache


def bundle_engine(out=None, deps=None):
    """The engine Lambda: handler + ENGINE_FILES + DuckDB. deps=False (or the
    CAIRNPATH_SKIP_DEPS environment variable) leaves DuckDB out, for tests."""
    out = _fresh(out or BUILD / "run_engine", "run_engine")
    for name in ENGINE_FILES:
        shutil.copy(REPO / name, out)
    if deps is None:
        deps = not os.environ.get("CAIRNPATH_SKIP_DEPS")
    if deps:
        shutil.copytree(lambda_deps(), out, dirs_exist_ok=True)
    return out
