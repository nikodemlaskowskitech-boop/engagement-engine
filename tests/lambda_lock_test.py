"""
The Lambdas' libraries are locked (infra/locks/, made by infra/lock_lambda.py, 17 Nov 2026):
  - every line is name==version with a sha256 hash, nothing else;
  - the lock holds the same versions requirements.txt pins (a moved pin stops the build);
  - the build installs only the locked files (--require-hashes --no-deps);
  - lock_lambda.py reads pip's report in both of its hash spellings.
No network, no AWS. Run: python tests/lambda_lock_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra"))
import bundle        # noqa: E402
import lock_lambda   # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


print("\nThe locks")
LINE = re.compile(r"^[a-z0-9_.-]+==[\w.]+ --hash=sha256:[0-9a-f]{64}$")
for name, packages in (("engine", bundle.LAMBDA_PACKAGES), ("search", bundle.SEARCH_PACKAGES)):
    path = bundle.LOCKS[name]
    check(path.exists(), f"{name}: infra/locks/{path.name} exists (python infra/lock_lambda.py)")
    if not path.exists():
        continue
    body = [x for x in path.read_text(encoding="utf-8").splitlines() if x and not x.startswith("#")]
    check(body and all(LINE.match(x) for x in body), f"{name}: {len(body)} packages, each name==version with its sha256",
          [x for x in body if not LINE.match(x)][:3])
    locked = dict(x.split(" ")[0].split("==") for x in body)
    check(all(locked.get(p.lower()) == bundle.pinned(p) for p in packages),
          f"{name}: the same versions requirements.txt pins ({', '.join(packages)})", locked)

print("\nThe build installs exactly the locked files")
calls = []
real = bundle.subprocess.run
bundle.subprocess.run = lambda cmd, **kw: calls.append(cmd)
bundle.BUILD = Path(__import__("tempfile").mkdtemp())
try:
    if all(p.exists() for p in bundle.LOCKS.values()):
        bundle.lambda_deps()
        cmd = calls[-1] if calls else []
        check("--require-hashes" in cmd and "--no-deps" in cmd and "-r" in cmd and not any("==" in a for a in cmd),
              "pip gets the lock (-r), --require-hashes and --no-deps, and no loose pins", cmd)
finally:
    bundle.subprocess.run = real

print("\nlock_lambda.py reads pip's report")
fake = {"install": [
    {"metadata": {"name": "HTTPX", "version": "0.28.1"}, "download_info": {"archive_info": {"hashes": {"sha256": "a" * 64}}}},
    {"metadata": {"name": "duckdb", "version": "1.5.6"}, "download_info": {"archive_info": {"hash": "sha256=" + "b" * 64}}}]}
got = lock_lambda.lines(fake)
check(got == ["duckdb==1.5.6 --hash=sha256:" + "b" * 64, "httpx==0.28.1 --hash=sha256:" + "a" * 64],
      "both hash spellings, names in lower case, sorted", got)

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
