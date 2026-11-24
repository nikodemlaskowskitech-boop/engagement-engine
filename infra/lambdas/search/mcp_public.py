"""
The public remote MCP server (24 Nov 2026): the demo's documents as tools any MCP client can
add by URL (Claude's custom connectors, the MCP Inspector), with no sign-in, because a CV demo
has to open for anyone (Nikodem). It runs inside the public search function: same bundle,
same role (no data bucket, no key, no secret), same redacted file. What keeps it cheap and safe:

  - read-only, and only what the public site already shows: the redacted documents and the
    account list of the search page (names, tier, ARR with its currency, open flags, as of the bundle's date);
  - keywords only: no model call, so a visitor can't run up a Bedrock bill through it;
  - its own gateway limit ([aws] mcp_rate_per_second, mcp_burst) and the function's ceiling
    of [aws] search_max_runs at once, shared with the search;
  - the arguments are never logged (the method and tool name are, for counting);
  - [aws] mcp_public = false and a deploy remove the route.

The protocol, kept small: MCP's Streamable HTTP transport, stateless (no session ID, no
server-to-client stream), JSON-RPC over POST, answered with plain JSON. initialize, ping,
tools/list and tools/call; notifications get 202 and no body.
"""

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]     # newest first; we answer in the client's when we know it
MAX_BODY = 16_000                                          # bytes: a tool call is a few hundred

def _conf():
    """[mcp] public_* and the instructions text, bundled at deploy (settings.toml, mcp_instructions.md)."""
    from settings import SETTINGS
    c = SETTINGS["mcp"]
    return {"name": c["public_name"], "title": c["public_title"], "rows": int(c["public_max_results"]),
            "chars": int(SETTINGS["aws"]["search_max_chars"]),
            "instructions": (HERE / "mcp_instructions.md").read_text(encoding="utf-8").strip()}



TOOLS = [
    {"name": "search_documents",
     "title": "Search the demo documents",
     "description": "Find the demo accounts whose tickets, emails or call notes match a question, "
                    "best first, each with its matching cases (one line per case, its best document and "
                    "the line that matched). Give an account (ID like acc_012, or its name) to search it "
                    "only. Matches words (plus a small CS vocabulary), not meaning.",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string", "description": "What to look for, in plain words"},
         "account": {"type": "string", "description": "Optional: one account's ID or name"}},
         "required": ["question"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "list_accounts",
     "title": "List the demo accounts",
     "description": "The demo accounts the documents cover: name, ID, tier, ARR (annual recurring revenue, "
                    "in the currency given) and open flags "
                    "(e.g. 'Churn Act', 'Shelfware', 'QBR coming up'), biggest ARR first. Filter by "
                    "tier (Enterprise, Scale) or by words in a flag (e.g. 'churn').",
     "inputSchema": {"type": "object", "properties": {
         "tier": {"type": "string", "description": "Optional: Enterprise or Scale"},
         "flag": {"type": "string", "description": "Optional: words a flag must contain, e.g. churn"}},
         "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
    {"name": "account_cases",
     "title": "One account's cases",
     "description": "Every case on one demo account, newest first: its title, size, dates, the teams "
                    "it came from (Support, CS, Implementation), the status of its ticket if it has one "
                    "(ticket_status: the ticket only, not the whole case; latest_after_ticket = messages came "
                    "after the ticket, so the case may still be live), and each document with its ID and date.",
     "inputSchema": {"type": "object", "properties": {
         "account": {"type": "string", "description": "The account's ID (acc_012) or name"}},
         "required": ["account"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}},
]

_accounts = None


def accounts():
    """The search page's accounts, written into the bundle at deploy (bundle.py): {"as_of", "accounts"}."""
    global _accounts
    if _accounts is None:
        path = HERE / "accounts.json"
        _accounts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"as_of": "", "accounts": {}}
    return _accounts


class Refused(Exception):
    """A tool call we answer with a reason (isError), not a crash."""


def _account_id(text):
    """An account ID or name -> its ID, among the accounts the documents cover."""
    text = str(text or "").strip()
    if not text:
        return ""
    known = accounts()["accounts"]
    if re.fullmatch(r"acc_[a-z0-9]+", text, re.I):
        if text.lower() in known:
            return text.lower()
        raise Refused(f"No documents for {text}: this demo covers {len(known)} accounts (list_accounts).")
    for aid, a in known.items():
        if a["n"].lower() == text.lower():
            return aid
    raise Refused(f"No demo account named {text!r}: use list_accounts for the names.")


def _card(aid):
    a = accounts()["accounts"].get(aid, {})
    # the currency with every amount (24 Nov 2026: Claude's first answer said "I've assumed it's in euros")
    return {"account_id": aid, "name": a.get("n", ""), "tier": a.get("t", ""), "arr": a.get("a", 0),
            "currency": accounts().get("currency", ""), "flags": a.get("f", [])}


def _case(c, docs=False):
    out = {k: c[k] for k in ("case", "title", "n", "first", "last", "teams")}
    # the ticket's status, named as such (24 Nov 2026: Alder Media's competitor case said "status: solved"
    # from an old export ticket while the renewal threat in its emails was open); none = no ticket
    out["ticket_status"] = c["status"] or None
    out["latest_after_ticket"] = bool(c["status"]) and c["last"] > max(
        (d["date"] for d in c.get("docs", []) if d["kind"] == "S"), default=c["last"])
    out["best"] = {k: c["best"][k] for k in ("id", "kind", "date", "title", "line")}
    if docs:
        out["documents"] = [{k: d[k] for k in ("id", "kind", "date", "title", "team")} for d in c["docs"]]
    return out


def call(con, name, args):
    """One tool call -> its answer (a dict). con = the search's index of the redacted file."""
    import search
    if name == "list_accounts":
        tier, flag = str(args.get("tier") or "").strip().lower(), str(args.get("flag") or "").strip().lower()
        rows = [_card(aid) for aid in accounts()["accounts"]]
        rows = [r for r in rows if (not tier or r["tier"].lower() == tier)
                and (not flag or any(flag in f.lower() for f in r["flags"]))]
        rows.sort(key=lambda r: (-r["arr"], r["account_id"]))
        return {"as_of": accounts()["as_of"], "currency": accounts().get("currency", ""), "count": len(rows),
                "accounts": rows}
    if name == "account_cases":
        aid = _account_id(args.get("account"))
        if not aid:
            raise Refused("Give an account's ID (acc_012) or name.")
        hits = search.account_documents(con, aid, 200)
        found = search.cases(con, hits, newest=True)
        return {**_card(aid), "cases": [_case(c, docs=True) for c in (found[0]["cases"] if found else [])]}
    if name == "search_documents":
        q = re.sub(r"\s+", " ", str(args.get("question") or "")).strip()
        if not q:
            raise Refused("Ask something: the question is empty.")
        if len(q) > _conf()["chars"]:
            raise Refused(f"Keep the question under {_conf()['chars']} characters.")
        aid = _account_id(args.get("account"))
        if not aid:
            aid, q = search.split_account(q)          # an ID inside the question works too, as on the site
            q = q or ""
            aid = aid or ""
        if aid and not q:
            raise Refused("That only names an account: use account_cases for all its cases.")
        rows = _conf()["rows"]
        hits = search.search(con, q, k=rows * 5, account_id=aid or None)   # keywords: no model call
        grouped = search.cases(con, hits)[:rows]
        return {"question": q, "matched_by": "words", "results": [
            {**_card(g["account_id"]), "cases": [_case(c) for c in g["cases"][:3]]} for g in grouped]}
    raise KeyError(name)


def _rpc(con, msg):
    """One JSON-RPC message -> its response dict, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    method, mid, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:                                      # a notification: nothing to answer
        return None
    ok = lambda result: {"jsonrpc": "2.0", "id": mid, "result": result}                       # noqa: E731
    err = lambda code, text: {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}  # noqa: E731
    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": _conf()["name"], "title": _conf()["title"], "version": "1.0"},
                   "instructions": _conf()["instructions"]})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return err(-32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return err(-32602, "arguments must be an object")
        allowed = set(next(t for t in TOOLS if t["name"] == name)["inputSchema"]["properties"])
        if set(args) - allowed:
            return ok({"isError": True, "content": [{"type": "text", "text":
                       f"Unknown argument(s): {', '.join(sorted(set(args) - allowed))}"}]})
        try:
            answer = call(con, name, args)
        except Refused as e:
            return ok({"isError": True, "content": [{"type": "text", "text": str(e)}]})
        return ok({"content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}]})
    return err(-32601, f"Method not found: {method}")


def handle(event, index):
    """The HTTP side: POST /mcp with one JSON-RPC message. index() = the search's index."""
    import base64
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    reply = lambda status, body=None: {                                                     # noqa: E731
        "statusCode": status, "body": "" if body is None else json.dumps(body, ensure_ascii=False),
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"}}
    if "application/json" not in headers.get("content-type", ""):
        return reply(415, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Send application/json"}})
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    if len(raw.encode("utf-8")) > MAX_BODY:
        return reply(413, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request too large"}})
    try:
        msg = json.loads(raw)
    except ValueError:
        return reply(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    if isinstance(msg, list):
        return reply(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Send one message, not a batch"}})
    name = (msg.get("params") or {}).get("name") if isinstance(msg, dict) and msg.get("method") == "tools/call" else ""
    print(f"mcp {msg.get('method') if isinstance(msg, dict) else '?'} {name or ''}".strip())   # never the arguments
    try:
        out = _rpc(index(), msg)
    except Exception as e:                                   # noqa: BLE001: the type only, never the message
        print(f"mcp failed: {type(e).__name__}")
        return reply(500, {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                           "error": {"code": -32603, "message": "Internal error, try again in a minute"}})
    return reply(202) if out is None else reply(200, out)
