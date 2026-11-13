"""
Lambda: the public search on the demo site (13 Nov 2026). A visitor types a question,
the page calls /search?q=... on the site's own address, CloudFront passes it here.

What it can see: documents.jsonl, bundled with it, the demo's tickets, emails and call
notes already redacted on the laptop (search_export.py: the brief's redaction and the
leak check). Nothing else: its role has no access to the data bucket, the key or any
secret, so even a flaw in this function can't reach real data.

What it does: the same BM25 search as the laptop (search.py, synonyms on) over that
file, and answers with the top [search] site_top_k accounts, each with its best
document (citation, date, title, the matching line) and the IDs of the other matches.

What it never does: write the question anywhere. No log line holds it, an error is
reported by its type only (a message could quote the question), and the API's access
log has the route, never the query string. Visitors may type real names to test it.

Answers carry Cache-Control, so CloudFront answers a repeated question itself.
"""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOP_K = int(os.environ.get("TOP_K", "5"))
MAX_CHARS = int(os.environ.get("MAX_CHARS", "200"))
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "3600"))

_con = None              # the index, built once per cold start and kept while warm


def _index():
    global _con
    if _con is None:
        import duckdb
        import search
        con = duckdb.connect()
        rows = [json.loads(line) for line in (HERE / "documents.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        search.add_views(con, rows=rows)
        _con = con
    return _con


def _reply(status, body, cache=False):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json; charset=utf-8",
                        "Cache-Control": f"public, max-age={CACHE_SECONDS}" if cache else "no-store"},
            "body": json.dumps(body, ensure_ascii=False)}


def handler(event, context=None):
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "GET")
    if method not in ("GET", "HEAD"):
        return _reply(405, {"error": "GET only"})
    q = ((event.get("queryStringParameters") or {}).get("q") or "").strip()
    if not q:
        return _reply(400, {"error": "Ask a question: /search?q=..."})
    if len(q) > MAX_CHARS:
        return _reply(400, {"error": f"Questions up to {MAX_CHARS} characters"})
    try:
        import search
        hits = search.search(_index(), q, k=TOP_K * 5)          # enough documents to fill TOP_K accounts
    except Exception as e:                                    # noqa: BLE001: the type only, never the message
        print(f"search failed: {type(e).__name__}")
        return _reply(500, {"error": "Search unavailable, try again in a minute"})
    accounts = {}
    for h in hits:
        if h["account_id"] not in accounts:
            if len(accounts) == TOP_K:
                continue
            accounts[h["account_id"]] = {
                "account_id": h["account_id"], "score": h["score"],
                "best": {k: h[k] for k in ("id", "kind", "date", "title", "line")},
                "matched": h["matched"], "more": []}
        elif h["id"] not in accounts[h["account_id"]]["more"]:
            accounts[h["account_id"]]["more"].append(h["id"])
    return _reply(200, {"results": list(accounts.values())}, cache=True)
