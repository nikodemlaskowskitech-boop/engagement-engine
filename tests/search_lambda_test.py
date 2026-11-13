"""
The public search function (infra/lambdas/search/handler.py), from its real bundle.

  - the bundle holds the handler, the search code, the settings and the redacted
    documents, and nothing else: no handover.py (it can't redact, so it can only ever
    hold redacted text), no stories, no contacts, no answer key;
  - a question gets 200, at most [search] site_top_k accounts, each with its best
    document (a citation that's really in the file), cacheable;
  - the same accounts in the same order as search.py on the laptop over the same file;
  - an empty or too long question 400, anything but GET 405, an internal error 500
    that says nothing about the question;
  - the question is never written anywhere: not on a good answer, not on an error.

Runs the handler in a separate Python from the bundle folder, like Lambda would.
Needs DuckDB. Run: python tests/search_lambda_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "infra"))
from bundle import bundle_search                    # noqa: E402
from settings import SETTINGS                       # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


out = bundle_search(Path(tempfile.mkdtemp()) / "search", deps=False)
print("\nThe bundle")
names = sorted(p.name for p in out.iterdir())
check(names == sorted(["handler.py", "search.py", "documents.py", "settings.py", "settings.toml", "documents.jsonl"]),
      "handler, search code, settings and the redacted documents: nothing else", names)
check(not (out / "handover.py").exists() and not (out / "stories").exists(),
      "no redaction code, no stories, no contacts: it only ever holds redacted text")
check((out / "documents.jsonl").read_bytes() == (ROOT / SETTINGS["search"]["site_documents_file"]).read_bytes(),
      "its documents are search_export.py's committed file")

SECRET = "Ingrid Solberg 4471"                     # a "real name" a visitor might type
RUN = r"""
import json, sys
import handler, search
def call(q=None, method="GET"):
    ev = {"requestContext": {"http": {"method": method}}, "queryStringParameters": None if q is None else {"q": q}}
    r = handler.handler(ev)
    return {"status": r["statusCode"], "cache": r["headers"]["Cache-Control"], "body": json.loads(r["body"])}
out = {}
out["good"] = call("Which accounts are evaluating a competitor?")
out["secret"] = call(sys.argv[1])
out["empty"] = call("   ")
out["missing"] = call(None)
out["long"] = call("x" * (handler.MAX_CHARS + 1))
out["post"] = call("hello", "POST")
rows = [json.loads(l) for l in open("documents.jsonl", encoding="utf-8")]
out["file"] = [[r["account_id"], r["id"], r["title"]] for r in rows]
out["laptop"] = [a["account_id"] for a in search.accounts(handler._index(), "Which accounts are evaluating a competitor?", handler.TOP_K)]
def boom(*a, **k):
    raise ValueError("failed on: " + sys.argv[1])
search.search = boom
out["error"] = call(sys.argv[1])
print("RESULT " + json.dumps(out))
"""
env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(out)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]),
       "TOP_K": str(SETTINGS["search"]["site_top_k"]), "MAX_CHARS": str(SETTINGS["aws"]["search_max_chars"]),
       "CACHE_SECONDS": str(SETTINGS["aws"]["search_cache_seconds"])}
proc = subprocess.run([sys.executable, "-c", RUN, SECRET], cwd=out, env=env, capture_output=True, text=True, encoding="utf-8")
line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None)
if not line:
    print(proc.stdout[-2000:], proc.stderr[-2000:])
    sys.exit("the handler didn't run")
r = json.loads(line[len("RESULT "):])
logs = proc.stdout.replace(line, "") + proc.stderr

print("\nAnswers")
good = r["good"]
res = good["body"].get("results", [])
in_file = {(a, i): t for a, i, t in r["file"]}
check(good["status"] == 200 and 0 < len(res) <= int(SETTINGS["search"]["site_top_k"]),
      f"a question: 200 and at most {SETTINGS['search']['site_top_k']} accounts", (good["status"], len(res)))
check(all(in_file.get((x["account_id"], x["best"]["id"])) == x["best"]["title"] and x["best"]["line"] and x["best"]["date"]
          for x in res), "each with its best document: a real citation from the file, its date and the matching line")
check(good["cache"] == f"public, max-age={SETTINGS['aws']['search_cache_seconds']}", "cacheable by CloudFront", good["cache"])
check([x["account_id"] for x in res] == r["laptop"], "the same accounts, same order, as search.py on the laptop",
      ([x["account_id"] for x in res], r["laptop"]))
for name, want in (("empty", 400), ("missing", 400), ("long", 400), ("post", 405)):
    check(r[name]["status"] == want and r[name]["cache"] == "no-store", f"{name} question: {want}, never cached", r[name])
check(r["error"]["status"] == 500 and SECRET not in json.dumps(r["error"]), "an internal error: 500, without the question",
      r["error"])

print("\nThe question is never written anywhere")
check(SECRET not in logs and "competitor" not in logs.lower(),
      "not on an answer, not on an error: the log has the error's type only", logs[-300:])
check("ValueError" in logs, "...but the error's type is logged, so a failure is still visible")

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
