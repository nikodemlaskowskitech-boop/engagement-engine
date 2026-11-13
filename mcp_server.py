"""
The Cairnpath MCP server (Phase 9, 13 Nov 2026): the engine's facts and the document
search as tools any MCP client can call, such as Claude Desktop on the laptop.
"Which of my accounts are at risk, and what do their emails say?" becomes a few tool
calls answered from the same SQL as the cockpit.

Local only: the client starts this script on the laptop and talks to it over
standard input and output. There's no network address, so there's nothing to sign in
to and nothing to attack (a remote version needs sign-in first: THREAT_MODEL.md).

Same rules as the cockpit:
  - one person's view: [mcp] person (an owner ID from the CS team, or "leader") decides
    which accounts the tools may show, cut exactly like that person's cockpit page
    (roles.py slice_export). An account outside it is refused, not hidden in silence.
  - read only: no tool changes anything (each says so to the client: readOnlyHint).
  - the documents go out redacted: search results through search.redacted() (the brief's
    redaction, then the leak check); an account the leak check holds back isn't sent.
  - the facts are the cockpit's facts (facts.py): account names, owners, ARR, flags with
    their reasons. No user IDs, no customer people.

What leaves the laptop: whatever a tool returns goes to the client's model (Claude
Desktop sends it to Anthropic). That's the facts above and redacted document text. For
the demo the data is fictional; a real team would use it under its own AI agreement.

    python mcp_server.py               # started by the client, not by hand
    python mcp_server.py --check       # list the tools and answer one question, then exit

Claude Desktop: Settings > Developer > Edit config, then add under "mcpServers":
    "cairnpath": {"command": "<repo>\\.venv\\Scripts\\python.exe",
                  "args": ["<repo>\\mcp_server.py"]}
"""
import json
import re
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError   # a refusal the model sees; any other error stays on the server
from mcp.types import ToolAnnotations

import facts
import roles
import search
from settings import OUTPUTS, SETTINGS

CONF = SETTINGS["mcp"]
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

server = MCPServer(
    name="cairnpath",
    title="Cairnpath engagement engine",
    instructions=("Customer Success facts for one person's book of business (fictional demo data): "
                  "accounts, churn risk with reasons, timelines, and a search over support tickets, "
                  "emails and call notes (people redacted as Customer A, CS 1). Read only. "
                  "Cite document IDs (S1, E2, C1) and account IDs when you use them."))

_state = {}


def scope():
    """Who this server answers as, and the accounts that person may see: the cockpit's own cut."""
    if "scope" not in _state:
        export = json.loads((OUTPUTS / "views_data.json").read_text(encoding="utf-8"))
        who = CONF["person"]
        everyone = {roles.ROLES["leader_id"]: {"role": "leader", "name": roles.ROLES["leader_name"], "manager": None},
                    **roles.people(export["team"])}
        pid = roles.ROLES["leader_id"] if who == "leader" else who
        if pid not in everyone:
            sys.exit(f"[mcp] person = {who!r} isn't in the CS team (or 'leader')")
        _, meta = roles.slice_export(export, pid, everyone[pid])
        _state["scope"] = {"id": pid, "name": everyone[pid]["name"], "role": everyone[pid]["role"],
                           "accounts": set(meta["accounts"])}
    return _state["scope"]


def con():
    if "con" not in _state:
        _state["con"] = facts.connect_all()
    return _state["con"]


def search_con():
    if "search" not in _state:
        from cairnpath_db import connect
        c = connect()
        search.add_views(c)
        _state["search"] = c
    return _state["search"]


def _mine(account_id):
    if not re.fullmatch(r"[\w-]+", str(account_id)):
        raise ToolError(f"not an account ID: {account_id!r}")
    if account_id not in scope()["accounts"]:
        raise ToolError(f"{account_id} isn't in {scope()['name']}'s book (or doesn't exist)")
    return account_id


@server.tool(annotations=READ_ONLY)
def who_am_i() -> dict:
    """Whose book this server answers for, how many accounts that is, and the data's as-of date."""
    s = scope()
    return {"person": s["name"], "role": s["role"], "accounts": len(s["accounts"]), "as_of": facts.as_of(con()),
            "note": "Fictional, generated demo data."}


@server.tool(annotations=READ_ONLY)
def account(account_id: str) -> dict:
    """One account: tier, region, owner, ARR, renewal, contract, its open flags with their
    reasons and value at stake, whether it was called, weekly active users for 13 weeks
    (oldest first) and the last 30 days of utilisation (%)."""
    a = facts.account(con(), _mine(account_id))
    if a is None:
        raise ToolError(f"{account_id} isn't in today's book")
    a["util_last_30_days"] = [u for u in a.pop("util")[-30:]]
    a["weekly_active_users"] = a.pop("wau")
    return a


@server.tool(annotations=READ_ONLY)
def at_risk(owner: str = "", manager: str = "", region: str = "", tier: str = "", status: str = "") -> list:
    """Churn risk across the book, biggest ARR at risk first, each with its status (Act,
    Watch, Shelfware), reasons, renewal date and the day it was called (if it was).
    Filters (all optional): owner or manager (owner IDs like csm_mia, mgr_sophie),
    region (UK&I, DACH, France & Benelux, Nordics), tier (Enterprise, Scale,
    Self-service), status (Act, Watch or Shelfware)."""
    statuses = (status,) if status else ("Act", "Watch", "Shelfware")
    rows = facts.at_risk(con(), owner=owner or None, manager=manager or None, region=region or None,
                         tier=tier or None, statuses=statuses)
    return [r for r in rows if r["account_id"] in scope()["accounts"]][: int(CONF["max_results"])]


@server.tool(annotations=READ_ONLY)
def timeline(account_id: str, limit: int = 20) -> list:
    """What happened on one account, newest first: customer touches (emails, meetings),
    tasks sent to CS and what became of them, next steps planned and renewal forecasts."""
    items = facts.timeline(con(), _mine(account_id))
    return items[: max(1, min(int(limit), 100))]


@server.tool(annotations=READ_ONLY)
def search_documents(question: str, account_id: str = "") -> dict:
    """Search support tickets, emails and call notes in plain words (keywords plus a CS
    vocabulary, not meaning). Returns the best documents, each with its account, citation
    ID, date, title, the matching line and its text, all redacted: people are labels
    (Customer A, CS 1) per account, the company is "the customer". account_id = search
    one account's documents only."""
    if not str(question).strip():
        raise ToolError("ask a question")
    only = _mine(account_id) if account_id else None
    hits = [h for h in search.search(search_con(), question, k=int(CONF["max_results"]) * 3, account_id=only)
            if h["account_id"] in scope()["accounts"]][: int(CONF["max_results"])]
    safe, held = search.redacted(search_con(), hits)
    return {"results": [{k: h[k] for k in ("account_id", "id", "kind", "date", "title", "line", "text")} for h in safe],
            "held_back": len({b["account_id"] for b in held}),
            "note": "Keyword search: a question in unusual words finds less. Documents exist for about 30 accounts."}


if __name__ == "__main__":
    if "--check" in sys.argv:
        import asyncio
        sys.stdout.reconfigure(encoding="utf-8")
        print("Tools:", ", ".join(t.name for t in asyncio.run(server.list_tools())))
        print("As:", who_am_i())
        top = at_risk()[:3]
        print("At risk:", [(r["account_id"], r["status"], r["at_risk"]) for r in top])
        sys.exit(0)
    server.run("stdio")
