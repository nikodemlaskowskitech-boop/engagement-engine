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
import bundle                                       # noqa: E402
from bundle import bundle_search, search_mode       # noqa: E402
import embed                                        # noqa: E402
from settings import SETTINGS                       # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


TMP = Path(tempfile.mkdtemp())
out = bundle_search(TMP / "search", deps=False, vectors=TMP / "none.jsonl")
print("\nThe bundle")
names = sorted(p.name for p in out.iterdir())
check(names == sorted(["handler.py", "search.py", "documents.py", "settings.py", "settings.toml", "documents.jsonl"]),
      "handler, search code, settings and the redacted documents: nothing else", names)
check(not (out / "handover.py").exists() and not (out / "stories").exists(),
      "no redaction code, no stories, no contacts: it only ever holds redacted text")
check((out / "documents.jsonl").read_bytes() == (ROOT / SETTINGS["search"]["site_documents_file"]).read_bytes(),
      "its documents are search_export.py's committed file")

check(search_mode(out) == "keywords", "no vectors on the laptop: keywords only (no model call, no model permission)")

print("\nThe vectors go along only when every document has an up-to-date one")
docs = embed.documents()
model, dims = SETTINGS["embed"]["model"], int(SETTINGS["embed"]["dimensions"])
si = embed.StandIn(dims)
fake = [{"account_id": d["account_id"], "id": d["id"], "model": model, "dims": dims, "hash": embed.digest(d),
         "vector": [round(x, 6) for x in si(embed.text_of(d))[0]]} for d in docs]


def vec_file(name, rows):
    p = TMP / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


full = vec_file("full.jsonl", fake + [{**fake[0], "id": "gone-1"}])        # plus one the site no longer has
hyb = bundle_search(TMP / "hybrid", deps=False, vectors=full)
carried = [json.loads(x) for x in (hyb / "vectors.jsonl").read_text(encoding="utf-8").splitlines()] if search_mode(hyb) == "hybrid" else []
check(search_mode(hyb) == "hybrid" and len(carried) == len(docs) and not any(r["id"] == "gone-1" for r in carried),
      "all up to date: hybrid, one vector per document it carries (nothing else)", (search_mode(hyb), len(carried)))
stale = [dict(r) for r in fake]
stale[3]["hash"] = "0" * 16
for name, rows, why in (("stale", stale, "one document changed since its vector"),
                        ("partial", fake[:-1], "one document has no vector"),
                        ("other", [{**r, "model": "stand-in"} for r in fake], "vectors from another model (the stand-in)")):
    o = bundle_search(TMP / name, deps=False, vectors=vec_file(name + ".jsonl", rows))
    check(search_mode(o) == "keywords", f"{why}: keywords only")
SETTINGS["aws"]["search_meaning"], was = False, SETTINGS["aws"].get("search_meaning")
off = bundle_search(TMP / "off", deps=False, vectors=full)
SETTINGS["aws"]["search_meaning"] = was
check(search_mode(off) == "keywords", "[aws] search_meaning = false (the kill switch): keywords only, even with vectors")

SECRET = "Ingrid Solberg 4471"                     # a "real name" a visitor might type
RUN = r"""
import json, sys
import handler, search
if handler.MODE == "hybrid":
    import hashlib, math, re
    def standin(text, dims=handler.DIMS):                # embed.StandIn: the bundle has no embed.py
        v = [0.0] * dims
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dims] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]
    handler.embed = standin
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
if handler.MODE == "hybrid":
    q = "Which accounts are evaluating a competitor?"
    con = handler._index()
    want = search.fuse([search.search(con, q, k=handler.TOP_K * 5),
                        search.by_vector(con, standin(q), k=handler.TOP_K * 5, query=q)], handler.TOP_K * 5)
    seen = []
    for h in want:
        if h["account_id"] not in seen:
            seen.append(h["account_id"])
    out["laptop"] = seen[:handler.TOP_K]
    def no_titan(q):
        raise PermissionError("AccessDenied for " + q)
    handler.embed = no_titan
    out["no_titan"] = call(sys.argv[1])
    out["no_titan_good"] = call("Which accounts are evaluating a competitor?")
    out["keywords"] = [a["account_id"] for a in search.accounts(con, "Which accounts are evaluating a competitor?", handler.TOP_K)]
def boom(*a, **k):
    raise ValueError("failed on: " + sys.argv[1])
search.search = boom
out["error"] = call(sys.argv[1])
print("RESULT " + json.dumps(out))
"""


def run(where, mode):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(where)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]),
           "TOP_K": str(SETTINGS["search"]["site_top_k"]), "MAX_CHARS": str(SETTINGS["aws"]["search_max_chars"]),
           "CACHE_SECONDS": str(SETTINGS["aws"]["search_cache_seconds"]), "MODE": mode, "MODEL": model,
           "DIMS": str(dims), "REGION": "eu-north-1"}
    proc = subprocess.run([sys.executable, "-c", RUN, SECRET], cwd=where, env=env, capture_output=True, text=True, encoding="utf-8")
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None)
    if not line:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
        sys.exit("the handler didn't run")
    return json.loads(line[len("RESULT "):]), proc.stdout.replace(line, "") + proc.stderr


r, logs = run(out, "keywords")
check(r["good"]["body"].get("ranked_by") == "keywords", "keywords bundle: says it ranked by keywords")
rh, logs_h = run(hyb, "hybrid")

print("\nHybrid (a stand-in for Titan)")
hres = rh["good"]["body"].get("results", [])
check(rh["good"]["status"] == 200 and rh["good"]["body"].get("ranked_by") == "hybrid" and hres,
      "a question: 200, ranked by keywords and meaning merged", rh["good"]["body"].get("ranked_by"))
check([x["account_id"] for x in hres] == rh["laptop"], "the same accounts as search.fuse of the two lists on the laptop",
      ([x["account_id"] for x in hres], rh["laptop"]))
nt = rh["no_titan_good"]
check(nt["status"] == 200 and nt["body"].get("ranked_by") == "keywords"
      and [x["account_id"] for x in nt["body"]["results"]] == rh["keywords"],
      "Titan refuses (throttled, no permission): still answers, by keywords alone", nt["body"].get("ranked_by"))
check(rh["no_titan"]["status"] == 200 and rh["no_titan"]["body"].get("ranked_by") == "keywords",
      "...for any question, the visitor's own included", rh["no_titan"]["status"])
check("PermissionError" in logs_h and SECRET not in logs_h and "competitor" not in logs_h.lower(),
      "the failure is logged by its type only, never the question", logs_h[-300:])
check(rh["error"]["status"] == 500 and SECRET not in json.dumps(rh["error"]), "an internal error: 500, without the question")

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
