"""
Handover cache test: is the model called only when the context changed?

    python tests/handover_cache_test.py

No API key and no cost: a stand-in model counts its calls. Works on copies of
the stories and the export in a temporary folder, so real briefs are never
touched. Needs data/views_data.json (run .\\run_all.ps1 first).
Exit 1 on any FAIL.
"""

import copy
import json
import shutil
import sys
import tempfile
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# --- a stand-in for the anthropic library: answers like the model, counts calls ---
calls = []
BRIEF = ("## Where things stand\n- The account renews soon and usage is low. [DATA]\n"
         "## People\n- Customer A is the contact. [C1]\n## Risks\n- Finance questions the volume. [E2]\n")


class _Messages:
    def create(self, **kw):
        calls.append(kw)
        block = types.SimpleNamespace(type="text", text=BRIEF)
        return types.SimpleNamespace(stop_reason="end_turn", content=[block],
                                     usage=types.SimpleNamespace(input_tokens=1000, output_tokens=200))


sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(messages=_Messages()))

import brief      # noqa: E402
import handover   # noqa: E402

brief.load_api_key = lambda: None        # no key needed: the model is the stand-in

tmp = Path(tempfile.mkdtemp())
shutil.copytree(REPO / "stories", tmp / "stories")
views = json.loads((REPO / "data" / "views_data.json").read_text(encoding="utf-8"))
handover.STORIES, handover.OUT, handover.VIEWS = tmp / "stories", tmp / "out", tmp / "views_data.json"
ACC = "acc_156"


def save_views(v):
    handover.VIEWS.write_text(json.dumps(v), encoding="utf-8")


def step(label, expect_call, **kw):
    """Run handover for ACC and check whether the model was called."""
    global failed
    before = len(calls)
    handover.run(ACC, kw.get("dry_run", False), kw.get("force", False))
    called = len(calls) > before
    ok = called == expect_call
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: model {'called' if called else 'not called'}"
          f" (expected {'a call' if expect_call else 'no call'})")


failed = 0
save_views(views)
print("\nHandover cache: the model runs only when the context changed")
step("first run, no brief yet", True)
step("second run, nothing changed", False)

v = copy.deepcopy(views)                                     # daily numbers move: no rewrite
a = next(x for x in v["accounts"] if x["account_id"] == ACC)
a["util"] = [u + 3 if u is not None else u for u in a["util"]]
a["wau"] = [w + 1 for w in a["wau"]]
v["as_of"] = "2099-01-01"
save_views(v)
step("utilisation, users and the date moved", False)

a["flags"][0]["status"] = "Act" if a["flags"][0]["status"] != "Act" else "Watch"   # a flag changes status
save_views(v)
step("a flag changed status", True)
step("run again after that rewrite", False)

a["next_qbr_date"] = "2099-02-01"                            # a QBR newly booked
save_views(v)
step("a QBR was booked", True)

# a new email arrives, deliberately with the same subject and date as an old one
# (a same-day reply): it must still count as new
eml = next((handover.STORIES / handover.CONF["email_folder"]).glob("*driftwood*"))
raw = eml.read_text(encoding="utf-8")
raw = "\n".join(l for l in raw.splitlines() if not l.lower().startswith("message-id"))
eml.with_name("2026-10-20_driftwood-labs_new-email.eml").write_text(raw, encoding="utf-8")
step("a new email arrived", True)
meta = json.loads((handover.OUT / f"{ACC}_meta.json").read_text(encoding="utf-8"))
ok = any(w.startswith("new email") for w in meta["why"])
failed += not ok
print(f"  {'PASS' if ok else 'FAIL'}  the rewrite says why: {'; '.join(meta['why'])}")

step("--dry-run with a change pending", False, dry_run=True)
step("--force with nothing changed", True, force=True)

support = handover.STORIES / handover.CONF["support_file"]                  # this account's ticket reopens
support.write_text("\n".join(l.replace(",solved,", ",reopened,") if l.startswith("DL-118,") else l
                             for l in support.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
step("a ticket changed status", True)

shutil.rmtree(tmp)
print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
