"""
Leak check test: does a person the redaction missed stop the model call?

    python tests/leak_check_test.py

PRIVACY.md upgrade C. No API key and no cost: a stand-in model counts its
calls. Works on copies of the stories in a temporary folder (real stories and
briefs untouched). Needs data/views_data.json (run .\\run_all.ps1 first).
  - the three stories as they are: no false alarm, the model is called
  - a name only in an email body (gap 1): blocked, nothing sent, listed
  - a contact detail or a name from the key left in the text: blocked
  - a brand that looks like a name: blocked until it's on the allow list
Exit 1 on any FAIL.
"""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

calls = []
BRIEF = "## Where things stand\n- Usage is low. [DATA]\n"


class _Messages:
    def create(self, **kw):
        calls.append(kw)
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=BRIEF)],
                                     usage=types.SimpleNamespace(input_tokens=100, output_tokens=20))


_fake = lambda **kw: types.SimpleNamespace(messages=_Messages())
sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=_fake, AnthropicBedrock=_fake)

import handover   # noqa: E402
import llm        # noqa: E402

llm.load_api_key = lambda: None
llm.LEDGER = Path(tempfile.mkdtemp()) / "ai_spend.csv"   # the stand-in's calls never reach the real ledger

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


def fresh():
    """A clean copy of the stories and an empty output folder."""
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(REPO / "stories", tmp / "stories")
    handover.STORIES, handover.OUT = tmp / "stories", tmp / "out"
    handover.BLOCKED.clear()
    return tmp


def run(acc):
    """One account; returns (model called?, what it printed)."""
    before, out = len(calls), io.StringIO()
    with contextlib.redirect_stdout(out):
        handover.run(acc, False, True)
    return len(calls) > before, out.getvalue()


def plant(tmp, acc, sentence):
    """Add a sentence to the body of one of this account's emails."""
    domain = next(r for r in handover.directory().values() if r["account_id"] == acc)["email_domain"]
    eml = next(p for p in sorted((tmp / "stories" / "emails").glob("*.eml")) if domain in p.read_text(encoding="utf-8"))
    eml.write_text(eml.read_text(encoding="utf-8").rstrip() + "\n\n" + sentence + "\n", encoding="utf-8")


ACCS = list(handover.directory())

print("\nLeak check: nothing person-shaped reaches the model")
fresh()
for acc in ACCS:
    called, out = run(acc)
    check(called and "BLOCKED" not in out, f"{acc}: the story as it is passes, the model is called (no false alarm)", out)

tmp = fresh()
acc = ACCS[0]
plant(tmp, acc, "Can you loop in Hannah Weiss from procurement before the renewal?")
called, out = run(acc)
sent = (handover.OUT / f"{acc}_sent.txt").read_text(encoding="utf-8")
check("Hannah Weiss" in sent, "the planted name really slips past the redaction (gap 1: only in an email body)")
check(not called and acc in handover.BLOCKED, "...so the leak check stops the model call", out)
check("Hannah Weiss" in out and "leak_check_allow" in out, "...and names what it found, with what to do next", out)
check(not (handover.OUT / f"{acc}.md").exists(), "...and writes no brief")

check(handover.leak_check("Call me on +44 20 7946 0958 [C1]", {}) == ["+44 20 7946 0958"]
      and handover.leak_check("write to jo@x.example", {}) == ["jo@x.example"],
      "a phone number or email address left in the text is caught too")
check(handover.leak_check("Customer A met Jane Doe. [C1]", {"Customer A": "Jane Doe", "the customer": "Acme"}) == ["Jane Doe"],
      "a name the redaction knew about but didn't replace is caught (belt and braces)")
check(handover.leak_check("Hi Customer A, the Head Of Data and the Finance Business Partner met on Monday March.", {}) == [],
      "labels, roles, greetings and dates aren't people")

tmp = fresh()
plant(tmp, acc, "We also run the Oak Ridge brand now.")
called, out = run(acc)
check(not called and "Oak Ridge" in out, "a brand that looks like a name is stopped until someone has looked at it")
handover.CONF.setdefault("leak_check_allow", [])
handover.CONF["leak_check_allow"].append("Oak Ridge")
handover.BLOCKED.clear()
called, out = run(acc)
handover.CONF["leak_check_allow"].remove("Oak Ridge")
check(called, "...and passes once it's on [handover] leak_check_allow", out)

tmp = fresh()
plant(tmp, acc, "Can you loop in Hannah Weiss from procurement before the renewal?")
before, out = len(calls), io.StringIO()
with contextlib.redirect_stdout(out):
    handover.run(acc, True, False)
check(len(calls) == before and "BLOCKED" in out.getvalue(), "--dry-run shows the block too (and never calls the model)")

print("\nNames next to a role or greeting (audit, 6 Nov 2026)")
for text in ("Thanks Hannah Weiss for the call.", "Our Finance Director Hannah Weiss signs off.",
             "Dear Hannah Weiss,", "Head of Data Hannah Weiss agreed"):
    check(handover.leak_check(text, {}) == ["Hannah Weiss"], f"caught in: {text!r}", handover.leak_check(text, {}))
check(handover.leak_check("Customer A and CS 1 met the Head of Data", {}) == [],
      "labels and roles on their own are still not people")

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
