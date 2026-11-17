"""
Locks the Lambdas' libraries: every package the engine and search functions install,
sub-dependencies included, pinned to the exact file for Lambda (Linux arm64, Python 3.13)
by its sha256 hash (17 Nov 2026, supply chain). bundle.py then installs exactly these files
(--require-hashes --no-deps) and stops if requirements.txt has moved on without a new lock.

    python infra/lock_lambda.py        (laptop, venv on; needs PyPI)

Run it after changing a pin in requirements.txt that a Lambda uses (duckdb, anthropic), then
commit infra/locks/. Writes nothing else and installs nothing (pip --dry-run).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle import LAMBDA_PACKAGES, LAMBDA_PLATFORMS, LOCKS, SEARCH_PACKAGES, pinned   # noqa: E402


def lines(report):
    """pip's --report (JSON) as lock lines: name==version --hash=sha256:..., sorted."""
    out = []
    for item in report["install"]:
        name, version = item["metadata"]["name"].lower(), item["metadata"]["version"]
        info = item["download_info"].get("archive_info", {})
        digest = (info.get("hashes") or {}).get("sha256") or (info.get("hash", "").removeprefix("sha256="))
        if not digest:
            sys.exit(f"pip gave no sha256 for {name}=={version}: not locked")
        out.append(f"{name}=={version} --hash=sha256:{digest}")
    return sorted(out)


def resolve(packages):
    pins = [f"{p}=={pinned(p)}" for p in packages]
    with tempfile.TemporaryDirectory() as d:
        report = Path(d) / "report.json"
        subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet",
                        "--report", str(report), "--target", str(Path(d) / "target"),
                        "--implementation", "cp", "--python-version", "3.13", "--only-binary=:all:",
                        *[a for p in LAMBDA_PLATFORMS for a in ("--platform", p)], *pins], check=True)
        return lines(json.loads(report.read_text(encoding="utf-8")))


if __name__ == "__main__":
    for name, packages in (("engine", LAMBDA_PACKAGES), ("search", SEARCH_PACKAGES)):
        locked = resolve(packages)
        LOCKS[name].parent.mkdir(exist_ok=True)
        LOCKS[name].write_text("# made by infra/lock_lambda.py: do not edit by hand\n" + "\n".join(locked) + "\n", encoding="utf-8")
        print(f"{LOCKS[name].relative_to(LOCKS[name].parents[2])}: {len(locked)} packages, each with its hash")
