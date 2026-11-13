"""
The local MCP server (mcp_server.py): the engine's facts and the document search as
tools, with the cockpit's role rules and redaction.

  - five tools, every one marked read-only for the client;
  - as the leader: one account's facts are the cockpit's (facts.account), trimmed;
  - as a CSM: an account outside their book is refused with a clear message (not a crash),
    and no tool returns one (at_risk, search);
  - search results are redacted: no contact name, email, phone or company from the
    stories or the corpus; an ID with a quote never reaches the SQL;
  - a real round trip, the way Claude Desktop does it: the server started as its own
    program from another folder, over standard input and output.

Needs DuckDB, data/ (with views_data.json) and the corpus. Run: python tests/mcp_server_test.py
"""
import asyncio
import csv
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import documents                                       # noqa: E402
import facts                                           # noqa: E402
import handover                                        # noqa: E402
import mcp_server as m                                 # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from settings import OUTPUTS, SETTINGS                 # noqa: E402

failed = 0


def check(ok, what, detail=""):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {what}" + (f"  ->  {str(detail)[:300]}" if detail and not ok else ""))
    failed += not ok


def call(name, **args):
    """A tool's answer as the client gets it: (ok, the JSON it returned or the error text)."""
    try:
        r = asyncio.run(m.server.call_tool(name, args))
    except ToolError as e:
        return False, str(e)
    texts = [c.text for c in r.content]
    try:
        parsed = [json.loads(x) for x in texts]
    except json.JSONDecodeError:
        return not r.is_error, " ".join(texts)
    # a dict comes back as one block; a list as one block per row (none for an empty list)
    one = len(parsed) == 1 and isinstance(parsed[0], dict) and name not in LISTS
    return not r.is_error, parsed[0] if one else parsed


LISTS = {"at_risk", "timeline"}


def as_person(pid):
    m.CONF["person"] = pid
    m._state.pop("scope", None)


print("\nThe tools")
tools = asyncio.run(m.server.list_tools())
names = sorted(t.name for t in tools)
check(names == sorted(["who_am_i", "account", "at_risk", "timeline", "search_documents"]), "five tools", names)
check(all(t.annotations and t.annotations.read_only_hint and not t.annotations.destructive_hint for t in tools),
      "every one marked read-only, none destructive")

print("\nAs the leader: the cockpit's facts")
as_person("leader")
ok, me = call("who_am_i")
book = json.loads((OUTPUTS / "views_data.json").read_text(encoding="utf-8"))
check(ok and me["role"] == "leader" and me["accounts"] == len(book["accounts"]), "the whole book", me)
acc = book["accounts"][0]["account_id"]
ok, a = call("account", account_id=acc)
f = facts.account(m.con(), acc)
check(ok and a["arr"] == f["arr"] and a["flags"] == f["flags"] and a["weekly_active_users"] == f["wau"]
      and a["util_last_30_days"] == f["util"][-30:], "an account: the same facts as the cockpit (utilisation trimmed to 30 days)")
check("user_id" not in json.dumps(a), "no user IDs in it")
ok, risk = call("at_risk", status="Act")
check(ok and risk and all(r["status"] == "Act" for r in risk)
      and [r["at_risk"] for r in risk] == sorted((r["at_risk"] for r in risk), reverse=True),
      f"at risk, Act only, biggest first ({len(risk)})")
ok, tl = call("timeline", account_id=acc, limit=5)
check(ok and 0 < len(tl) <= 5 and [x["date"] for x in tl] == sorted((x["date"] for x in tl), reverse=True),
      "a timeline, newest first, as long as asked")
for bad in ("acc_1' OR '1'='1", "acc_nope"):
    ok, msg = call("account", account_id=bad)
    check(not ok and ("not an account ID" in msg or "isn't in" in msg), f"refused, with a reason: {bad!r}", msg)

print("\nAs a CSM: only their book")
corpus = set(documents.directory(OUTPUTS / SETTINGS["search"]["corpus_folder"])) | set(documents.directory())
csm = next((t["owner_id"] for t in book["team"] if t["owner_type"] == "csm"
            and any(x["owner_id"] == t["owner_id"] and x["account_id"] in corpus for x in book["accounts"])), None)
as_person(csm)
mine = m.scope()["accounts"]
outside = next(x["account_id"] for x in book["accounts"] if x["account_id"] not in mine)
ok, me = call("who_am_i")
check(ok and me["role"] == "csm" and 0 < me["accounts"] < len(book["accounts"]), f"{csm}: {me.get('accounts')} accounts", me)
ok, msg = call("account", account_id=outside)
check(not ok and "isn't in" in msg, f"another team's account ({outside}) is refused, with a reason", msg)
ok, msg = call("timeline", account_id=outside)
check(not ok, "...its timeline too")
ok, risk = call("at_risk")
check(ok and all(r["account_id"] in mine for r in risk), "at risk: only their accounts", [r["account_id"] for r in risk if r["account_id"] not in mine])
everywhere = []
for q in ("budget pressure", "competitor", "invoice dispute", "data missing", "leaving the company"):
    ok, res = call("search_documents", question=q)
    everywhere += res["results"] if ok else []
check(all(r["account_id"] in mine for r in everywhere), f"search: only their accounts ({len(everywhere)} results over five questions)")

print("\nSearch results are redacted")
as_person("leader")
secret = set()
for folder in (documents.STORIES, OUTPUTS / SETTINGS["search"]["corpus_folder"]):
    with (folder / SETTINGS["handover"]["contacts_file"]).open(encoding="utf-8") as fh:
        secret |= {r[k] for r in csv.DictReader(fh) for k in ("name", "email")}
    secret |= {r["company"] for r in documents.directory(folder).values()}
results = []
for q in ("Which accounts are evaluating a competitor?", "budget", "invoice", "leaving", "security questionnaire"):
    ok, res = call("search_documents", question=q)
    results += res["results"]
blob = json.dumps(results, ensure_ascii=False)
check(results and not [s for s in secret if s and s in blob] and not handover.EMAIL_RE.findall(blob) and not handover.phones(blob),
      f"{len(results)} results: no name, email, phone or company", [s for s in secret if s and s in blob][:3])
ok, msg = call("search_documents", question="  ")
check(not ok and "ask a question" in msg, "an empty question is refused")

print("\nA real round trip, like Claude Desktop")


async def round_trip():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")],
                                   env=dict(os.environ), cwd=tempfile.gettempdir())
    with open(os.devnull, "w") as quiet:
        async with stdio_client(params, errlog=quiet) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                listed = sorted(t.name for t in (await s.list_tools()).tools)
                me = await s.call_tool("who_am_i", {})
                refused = await s.call_tool("account", {"account_id": "acc_nope"})
                return listed, me, refused


listed, me, refused = asyncio.run(round_trip())
check(listed == names, "started from another folder, over standard input/output: it lists the same five tools", listed)
check(not me.is_error and '"leader"' in me.content[0].text, "who_am_i answers", me.content[0].text[:120])
check(refused.is_error and "isn't in" in refused.content[0].text,
      "a refusal reaches the client as an error with its reason, not a crash", refused.content[0].text[:160])

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
