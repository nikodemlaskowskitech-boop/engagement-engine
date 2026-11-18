"""
generate_events.meeting_log (18 Nov 2026): what a CSM logged with a generated meeting.
Only the outcomes the Slack form offers; the same meeting always gets the same answer;
it never touches the random draws (so every other number in the demo stays put); about
8 in 10 meetings logged, most with a note, and no note repeated word for word everywhere.

    python tests/meeting_log_test.py
"""
import collections
import random
import sys
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_events as g      # noqa: E402
import slack_loop                # noqa: E402

failed = 0


def check(ok, name, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ->  {detail}" if not ok and detail else ""))


rng = random.Random(7)
events = [{"event_id": str(uuid.UUID(int=rng.getrandbits(128), version=4))} for _ in range(4000)]
pers = list(g.PERSONALITIES)
logs = [g.meeting_log(e, pers[i % len(pers)], ["qbr", "check_in", "churn_call"][i % 3]) for i, e in enumerate(events)]
outs = collections.Counter(o for o, _ in logs)
check(set(outs) - {None} <= set(slack_loop.OUTCOMES), "only outcomes the Slack form offers", dict(outs))
check(0.72 < 1 - outs[None] / len(logs) < 0.88, f"about 8 in 10 meetings logged ({1 - outs[None] / len(logs):.0%})")
with_note = [n for o, n in logs if o and n]
check(0.65 < len(with_note) / (len(logs) - outs[None]) < 0.85, f"most logged meetings have a note ({len(with_note)})")
check(logs == [g.meeting_log(e, pers[i % len(pers)], ["qbr", "check_in", "churn_call"][i % 3]) for i, e in enumerate(events)],
      "the same meeting always gets the same outcome and note")
state = random.getstate(); r2 = random.Random(1); s2 = r2.getstate()
for e in events[:200]:
    g.meeting_log(e, "declining", "qbr")
check(random.getstate() == state and r2.getstate() == s2, "never touches the random draws (the rest of the demo can't move)")
risky = [o for (o, _), i in zip(logs, range(len(logs))) if o and pers[i % len(pers)] in g.AT_RISK]
check(risky and 0.6 < risky.count("reached_at_risk") / len(risky) < 0.9, "at-risk personalities mostly sound at risk, not always")
top = collections.Counter(with_note).most_common(1)[0][1]
check(len(set(with_note)) >= 30 and top / len(with_note) < 0.1, f"{len(set(with_note))} different notes, none on more than 1 in 10 meetings")
check(all(n.endswith(".") and len(n) < 140 for n in with_note), "notes are short sentences")

print("\nFAILED" if failed else "\nAll passed")
sys.exit(1 if failed else 0)
