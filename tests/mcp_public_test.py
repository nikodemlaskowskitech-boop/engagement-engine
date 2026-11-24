"""
The public remote MCP server (infra/lambdas/search/mcp_public.py, 24 Nov 2026): POST /mcp on the
demo site, answered by the search function. Run in the sandbox: no AWS.

  - the protocol: initialize, notifications (202), ping, tools/list, tools/call, the errors;
  - the tools: what they return, refusals with a reason, names and IDs both work;
  - safety: keywords only (never Titan, even in a hybrid bundle), nothing but the redacted
    file and the search page's accounts, the arguments never logged, the HTTP guards;
  - a real round trip with the official MCP client (the SDK in requirements.txt) over HTTP,
    the way Claude's custom connectors call it.
"""

import asyncio
import io
import json
import os
import re
import sys
import tempfile
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "infra"), str(ROOT)]

import bundle                                       # noqa: E402
from settings import SETTINGS                       # noqa: E402

failed = 0


def check(ok, label, detail=None):
    global failed
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok or detail is None else f"  ->  {str(detail)[:400]}"))
    failed += not ok


print("\nThe bundle")
out = bundle.bundle_search(Path(tempfile.mkdtemp()) / "search", deps=False, vectors=Path(tempfile.mkdtemp()) / "none")
check((out / "mcp_public.py").exists() and (out / "accounts.json").exists(),
      "the search bundle carries mcp_public.py and the search page's accounts (accounts.json)")
acc = json.loads((out / "accounts.json").read_text(encoding="utf-8"))
docs = [json.loads(x) for x in (out / "documents.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
check(acc["as_of"] and set(acc["accounts"]) == {d["account_id"] for d in docs},
      f"accounts.json: exactly the {len(acc['accounts'])} accounts in the redacted documents, with the as-of date ({acc['as_of']})")

os.environ.update({"MCP": "on", "MODE": "keywords", "TOP_K": str(SETTINGS["search"]["site_top_k"])})
sys.path.insert(0, str(out))
import handler                                      # noqa: E402

titan = []
handler.embed = lambda q: titan.append(q) or [0.0] * 512     # must never be called from /mcp


def post(body, ctype="application/json", method="POST", raw=False):
    ev = {"rawPath": "/mcp", "requestContext": {"http": {"method": method, "path": "/mcp"}},
          "headers": {"content-type": ctype}, "body": body if raw else json.dumps(body), "isBase64Encoded": False}
    buf = io.StringIO()
    with redirect_stdout(buf):
        r = handler.handler(ev)
    LOGS.append(buf.getvalue())
    return r["statusCode"], (json.loads(r["body"]) if r["body"] else None)


LOGS = []
rpc = lambda method, params=None, mid=1: {"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params else {})}  # noqa: E731
tool = lambda name, args: post(rpc("tools/call", {"name": name, "arguments": args}))                                              # noqa: E731
text = lambda r: r[1]["result"]["content"][0]["text"]                                                                             # noqa: E731

print("\nThe protocol (stateless Streamable HTTP, JSON answers)")
s, b = post(rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}))
check(s == 200 and b["result"]["protocolVersion"] == "2025-06-18" and "tools" in b["result"]["capabilities"]
      and "fictional" in b["result"]["instructions"], "initialize: our version, tools, and instructions that say it's fictional", b)
s, b = post(rpc("initialize", {"protocolVersion": "2099-01-01", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}))
check(b["result"]["protocolVersion"] == "2025-06-18", "a version we don't know: we answer with our newest")
s, b = post({"jsonrpc": "2.0", "method": "notifications/initialized"})
check(s == 202 and b is None, "a notification: 202, no body")
check(post(rpc("ping"))[1]["result"] == {}, "ping")
s, b = post(rpc("tools/list"))
names = [t["name"] for t in b["result"]["tools"]]
check(names == ["search_documents", "list_accounts", "account_cases"]
      and all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in b["result"]["tools"]),
      "three tools, every one read-only", names)
check(post(rpc("tools/call", {"name": "delete_everything", "arguments": {}}))[1]["error"]["code"] == -32602, "an unknown tool: -32602")
check(post(rpc("resources/list"))[1]["error"]["code"] == -32601, "a method we don't serve: -32601")
check(post([rpc("ping")])[0] == 400, "a batch: refused (one message per request)")
check(post("{nope", raw=True)[0] == 400, "not JSON: 400")
check(post(rpc("ping"), ctype="text/plain")[0] == 415, "not application/json: 415")
check(post(json.dumps(rpc("ping", {"pad": "x" * 20000})), raw=True)[0] == 413, "a request over 16 KB: 413")
check(post(rpc("ping"), method="GET")[0] == 405, "GET /mcp: 405 (no stream to open)")

print("\nThe tools")
r = tool("list_accounts", {})
la = json.loads(text(r))
check(la["count"] == len(acc["accounts"]) and la["as_of"] == acc["as_of"]
      and [x["arr"] for x in la["accounts"]] == sorted((x["arr"] for x in la["accounts"]), reverse=True)
      and all(x["name"] for x in la["accounts"]), "list_accounts: every account, biggest ARR first, with its name and the as-of date")
churn = json.loads(text(tool("list_accounts", {"flag": "churn", "tier": "Enterprise"})))
check(all(x["tier"] == "Enterprise" and any("churn" in f.lower() for f in x["flags"]) for x in churn["accounts"]),
      f"...filtered by tier and flag ({churn['count']} Enterprise accounts with a churn flag)")
sd = json.loads(text(tool("search_documents", {"question": "Which accounts are disputing an invoice?"})))
files = {(d["account_id"], d["id"]): d for d in docs}
check(sd["results"] and sd["matched_by"] == "words" and len(sd["results"]) <= 10
      and all(x["name"] and x["cases"] and all((x["account_id"], c["best"]["id"]) in files for c in x["cases"]) for x in sd["results"]),
      f"search_documents: {len(sd['results'])} accounts by name, each with its cases, every document from the redacted file")
pick = max(acc["accounts"], key=lambda a: sum(d["account_id"] == a for d in docs))
one = json.loads(text(tool("search_documents", {"question": "call", "account": acc["accounts"][pick]["n"]})))
check(one["results"] and all(x["account_id"] == pick for x in one["results"]), "...an account by its name: that account only")
ac = json.loads(text(tool("account_cases", {"account": pick})))
in_cases = sorted(d["id"] for c in ac["cases"] for d in c["documents"])
check(ac["name"] == acc["accounts"][pick]["n"] and in_cases == sorted(d["id"] for d in docs if d["account_id"] == pick)
      and [c["last"] for c in ac["cases"]] == sorted((c["last"] for c in ac["cases"]), reverse=True),
      f"account_cases: all {len(in_cases)} of {pick}'s documents, in cases, newest case first")
for args, why in (({"question": ""}, "an empty question"), ({"question": "x" * 300}, "a question over 200 characters"),
                  ({"question": "invoice", "account": "acc_zz999"}, "an account the demo doesn't have"),
                  ({"question": "invoice", "account": "Acme Nowhere"}, "a name the demo doesn't have"),
                  ({"question": "invoice", "secret": 1}, "an argument the tool doesn't take")):
    r = tool("search_documents", args)
    check(r[0] == 200 and r[1]["result"].get("isError") and r[1]["result"]["content"][0]["text"],
          f"{why}: refused with a reason the model can read (isError), not a crash", r[1])

print("\nSafety")
os.environ["MODE"] = "hybrid"
handler.MODE = "hybrid"
tool("search_documents", {"question": "Which customers are under budget pressure?"})
check(not titan, "keywords only: even in a hybrid bundle, /mcp never asks Titan (no model cost through it)", titan)
logs = "".join(LOGS)
check("mcp tools/call search_documents" in logs and "invoice" not in logs.lower() and "budget" not in logs.lower()
      and "Acme" not in logs, "the log holds the method and tool name, never the arguments")
os.environ["MCP"] = "off"
check(post(rpc("ping"))[0] == 405, "[aws] mcp_public off: the function refuses /mcp too")
os.environ["MCP"] = "on"

print("\nA real round trip: the official MCP client over HTTP")


class Gateway(BaseHTTPRequestHandler):            # stands in for API Gateway: HTTP in, the Lambda event out
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        ev = {"rawPath": self.path, "requestContext": {"http": {"method": "POST", "path": self.path}},
              "headers": dict(self.headers), "body": body, "isBase64Encoded": False}
        with redirect_stdout(io.StringIO()):
            r = handler.handler(ev)
        self.send_response(r["statusCode"])
        for k, v in r.get("headers", {}).items():
            self.send_header(k, v)
        data = r["body"].encode()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.do_POST() if False else (self.send_response(405), self.end_headers())

    def do_DELETE(self):
        self.send_response(405); self.end_headers()

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
threading.Thread(target=srv.serve_forever, daemon=True).start()


async def round_trip():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    async with streamable_http_client(f"http://127.0.0.1:{srv.server_address[1]}/mcp") as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            res = await session.call_tool("search_documents", {"question": "Which accounts are evaluating a competitor?"})
            bad = await session.call_tool("account_cases", {"account": "acc_zz999"})
            return init, tools, res, bad

try:
    init, tools, res, bad = asyncio.run(asyncio.wait_for(round_trip(), 30))
    got = json.loads(res.content[0].text)
    info = getattr(init, "serverInfo", None) or getattr(init, "server_info", None)
    version = getattr(init, "protocolVersion", None) or getattr(init, "protocol_version", None)
    check(info.name == "cairnpath-demo", f"the client connects (protocol {version})")
    check([t.name for t in tools.tools] == ["search_documents", "list_accounts", "account_cases"], "...lists the three tools")
    is_err = lambda r: getattr(r, "isError", None) if hasattr(r, "isError") else getattr(r, "is_error", None)  # noqa: E731
    check(not is_err(res) and got["results"], f"...calls search_documents and gets {len(got['results'])} accounts")
    check(is_err(bad) and re.search(r"No documents for", bad.content[0].text), "...and a refusal arrives as a tool error with its reason")
except Exception as e:                            # noqa: BLE001
    check(False, "the official MCP client completes a session", f"{type(e).__name__}: {e}")
finally:
    srv.shutdown()

print(f"\n{'All passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
