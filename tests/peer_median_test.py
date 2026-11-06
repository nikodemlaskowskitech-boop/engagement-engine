"""
Peer medians on the role pages (roles.py peer_medians, settings.toml [roles] peer_median).

    python tests/peer_median_test.py        (needs data/views_data.json)

A person's page holds only their team, so "against the median" can't be worked out on
the page without comparing people with their team alone. roles.py computes the medians
on the whole export. Checks: book mode gives every page the same book-wide medians,
numbers only; team mode gives a team's pages their own team's medians; the leader
always gets the book's. Exit 1 on any FAIL.
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import roles  # noqa: E402

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


src = roles.OUTPUTS / "views_data.json"
if not src.exists():
    print("  FAIL  data/views_data.json is missing: run python export_views.py"); sys.exit(1)
export = json.loads(src.read_text(encoding="utf-8"))
people = roles.people(export["team"])
mia, tom = people["csm_mia"], people["csm_tom"]
everyone = [t["owner_id"] for t in export["team"]]
book = roles.peer_medians(export, everyone)

print("\n[roles] peer_median = \"book\" (the default)")
real = roles.ROLES.get("peer_median")
roles.ROLES["peer_median"] = "book"
pm_mia = roles.slice_export(export, "csm_mia", mia)[0]["peer_medians"]
pm_tom = roles.slice_export(export, "csm_tom", tom)[0]["peer_medians"]
check(pm_mia["scope"] == "book" and pm_mia["csm"] == book["csm"] == pm_tom["csm"],
      "Mia's and Tom's pages (two teams) carry the same book-wide medians", (pm_mia.get("csm"), pm_tom.get("csm")))
check(all(isinstance(v, (int, float)) or v is None for k in ("csm", "member", "pool") for v in pm_mia.get(k, {}).values()),
      "numbers only: no names, no ids")

print("\n[roles] peer_median = \"team\"")
roles.ROLES["peer_median"] = "team"
pm_mia = roles.slice_export(export, "csm_mia", mia)[0]["peer_medians"]
pm_tom = roles.slice_export(export, "csm_tom", tom)[0]["peer_medians"]
sophies = [t["owner_id"] for t in export["team"] if t["manager"] == "mgr_sophie"]
check(pm_mia["scope"] == "team" and pm_mia["csm"] == roles.peer_medians(export, sophies)["csm"],
      "Mia's page: the median of Sophie's team's CSMs only")
check(pm_mia["csm"] != pm_tom["csm"] or pm_mia["csm"] == book["csm"], "Tom's page: his own team's, not Mia's")
pm_lead = roles.slice_export(export, "leader", {"role": "leader", "name": "x", "manager": None})[0]["peer_medians"]
check(pm_lead["scope"] == "book" and pm_lead["csm"] == book["csm"], "the leader (whole book) always gets the book's medians")
roles.ROLES["peer_median"] = real

print("\nFAILED" if failed else "\nOK")
sys.exit(1 if failed else 0)
