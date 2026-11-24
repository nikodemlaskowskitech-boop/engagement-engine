"""
Lambda: the public search on the demo site (13 Nov 2026). A visitor types a question,
the page calls /search?q=... on the site's own address, CloudFront passes it here.

What it can see: documents.jsonl, bundled with it, the demo's tickets, emails and call
notes already redacted on the laptop (search_export.py: the brief's redaction and the
leak check). Nothing else: its role has no access to the data bucket, the key or any
secret, so even a flaw in this function can't reach real data.

What it does: the same search as the laptop (search.py, synonyms on) over that file,
and answers with the top [search] site_top_k accounts, each with its best document
(citation, date, title, the matching line) and the IDs of the other matches.
MODE=hybrid (23 Nov 2026, when the deploy carried vectors.jsonl): the question also goes
to Titan for its vector, and the keyword and meaning lists are merged (search.fuse), as
on the laptop. Titan in our own account, the question only, nothing stored by AWS
(PRIVACY.md §13). Any failure there (throttled, quota, no permission) answers with the
keywords alone: a search always answers. MODE=keywords: no model call at all.
An account ID in the question (23 Nov 2026; the page turns a name into it) makes it an explicit
search of that account: its documents only, ranked by the rest of the question, or newest
first when nothing else was asked. A code (an account ID, DL-118, INV-36164) goes to the
keywords alone: Titan sees no meaning in it.

What it never does: write the question anywhere. No log line holds it, an error is
reported by its type only (a message could quote the question), and the API's access
log has the route, never the query string. Visitors may type real names to test it.

Answers carry Cache-Control, so CloudFront answers a repeated question itself (and
Titan isn't asked twice in [aws] search_cache_seconds for the same question).
"""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOP_K = int(os.environ.get("TOP_K", "5"))
POOL = TOP_K * 2                                  # accounts returned: the page shows TOP_K of them
MAX_CHARS = int(os.environ.get("MAX_CHARS", "200"))
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "3600"))
MODE = os.environ.get("MODE", "keywords")         # hybrid only with vectors.jsonl in the bundle
MODEL = os.environ.get("MODEL", "")
DIMS = int(os.environ.get("DIMS", "512"))
REGION = os.environ.get("REGION", "")
ACCOUNT_DOCS = 30                                 # one account, no question: its newest documents
QUESTION_CHARS = 2000                             # far over MAX_CHARS: a guard, not a limit

_con = None              # the index, built once per cold start and kept while warm
_bedrock = None          # the Titan client, made once per cold start


def _index():
    global _con
    if _con is None:
        import duckdb
        import search
        con = duckdb.connect()
        rows = [json.loads(line) for line in (HERE / "documents.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
        search.add_views(con, rows=rows)
        if MODE == "hybrid":
            search.VECTORS_FILE = HERE / "vectors.jsonl"
            search._vectors(con)                         # load them now, once
        _con = con
    return _con


def embed(q):
    """The question's vector from Titan (the same model and size as the documents'). Tests
    replace this function: no AWS in the sandbox."""
    global _bedrock
    if _bedrock is None:
        import boto3                                      # in the Lambda runtime already
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION or None)
    body = json.dumps({"inputText": q[:QUESTION_CHARS], "dimensions": DIMS, "normalize": True})
    return json.loads(_bedrock.invoke_model(modelId=MODEL, body=body)["body"].read())["embedding"]


def rank(con, q, k, account_id=None):
    """Keywords always; in hybrid, merged with the closest documents by meaning. Returns the
    hits and how they were ranked (the page shows it). A code (DL-118, acc_087) has no
    meaning: keywords only, and no model call. account_id = that account's documents only."""
    import search
    words = search.search(con, q, k=k, account_id=account_id)
    if MODE != "hybrid" or search.has_code(q):
        return words, "keywords"
    try:
        near = search.by_vector(con, embed(q), k=k, account_id=account_id, query=q)
    except Exception as e:                                # noqa: BLE001: the type only, never the message
        print(f"meaning unavailable, keywords only: {type(e).__name__}")
        return words, "keywords"
    return search.fuse([words, near], k), "hybrid"


def _reply(status, body, cache=False):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json; charset=utf-8",
                        "Cache-Control": f"public, max-age={CACHE_SECONDS}" if cache else "no-store"},
            "body": json.dumps(body, ensure_ascii=False)}


def handler(event, context=None):
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath") or (event.get("requestContext") or {}).get("http", {}).get("path", "")
    if path == "/mcp":                            # the public MCP server (24 Nov 2026): mcp_public.py
        if method != "POST" or os.environ.get("MCP") != "on":
            return _reply(405, {"error": "POST only"} if os.environ.get("MCP") == "on" else {"error": "Not here"})
        import mcp_public
        return mcp_public.handle(event, _index)
    if method not in ("GET", "HEAD"):
        return _reply(405, {"error": "GET only"})
    q = ((event.get("queryStringParameters") or {}).get("q") or "").strip()
    if not q:
        return _reply(400, {"error": "Ask a question: /search?q=..."})
    if len(q) > MAX_CHARS:
        return _reply(400, {"error": f"Questions up to {MAX_CHARS} characters"})
    import search
    account, rest = search.split_account(q)
    if account:                                   # an explicit search of one account (23 Nov 2026)
        try:
            con = _index()
            if rest:
                hits, how = rank(con, rest, TOP_K * 4, account)
            else:
                hits, how = search.account_documents(con, account, ACCOUNT_DOCS), "newest"
        except Exception as e:                    # noqa: BLE001: the type only, never the message
            print(f"search failed: {type(e).__name__}")
            return _reply(500, {"error": "Search unavailable, try again in a minute"})
        docs = [{k: h[k] for k in ("id", "kind", "date", "title", "line", "matched")} for h in hits]
        grouped = search.cases(con, hits, newest=not rest)        # its cases, each once (23 Nov 2026)
        return _reply(200, {"account": account, "documents": docs, "ranked_by": how,
                            "cases": grouped[0]["cases"] if grouped else []}, cache=True)
    try:
        # twice TOP_K accounts (24 Nov 2026): the page orders them by relevance or by ARR and
        # shows TOP_K, so "biggest ARR first" picks among strong matches, not just the first five
        hits, how = rank(_index(), q, POOL * 5)                  # enough documents to fill POOL accounts
    except Exception as e:                                    # noqa: BLE001: the type only, never the message
        print(f"search failed: {type(e).__name__}")
        return _reply(500, {"error": "Search unavailable, try again in a minute"})
    grouped = {a["account_id"]: a["cases"] for a in search.cases(_index(), hits)}
    accounts = {}
    for h in hits:
        if h["account_id"] not in accounts:
            if len(accounts) == POOL:
                continue
            accounts[h["account_id"]] = {
                "account_id": h["account_id"], "score": h["score"],
                "best": {k: h[k] for k in ("id", "kind", "date", "title", "line")},
                "matched": h["matched"], "more": []}
        elif h["id"] not in accounts[h["account_id"]]["more"]:
            accounts[h["account_id"]]["more"].append(h["id"])
    for acc in accounts.values():                  # one line per case, not per message (23 Nov 2026)
        acc["cases"] = grouped.get(acc["account_id"], [])
    return _reply(200, {"results": list(accounts.values()), "ranked_by": how}, cache=True)
