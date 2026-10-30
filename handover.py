"""
Handover brief: everything a new owner needs to know about an account, from
data scattered across separate systems, in one page with its sources.

    python handover.py acc_024            one account (writes data/handover/)
    python handover.py acc_024 --dry-run  everything but the model call: shows
                                          exactly what would be sent
    python handover.py                    every account in the stories directory

The steps, and who does what:

  1. Gather (code)    the engine's own facts from data/views_data.json (flags
                      and their reasons, ARR, usage trend, CS touches, the
                      owner's forecast and next step), plus three silos joined
                      to the account: helpdesk tickets (by organisation name),
                      emails (by email domain) and call transcripts (by CRM
                      account ID). See stories/README.md.
  2. Redact (code)    every person becomes a label (Customer A, CS 1), every
                      email address and phone number is masked, the company
                      becomes "the customer". The key to undo it stays in
                      data/handover/ and is never sent.
  3. Write (model)    the LLM writes a short brief from the redacted sources;
                      every sentence must cite its source, e.g. [C1] or [DATA].
  4. Check (code)     citations that don't exist, sentences with no citation
                      and numbers found in no source are listed as unverified
                      at the bottom, like brief.py does for QBR briefs.
  5. Restore (code)   the real names go back in, outside the model.

Outputs in data/handover/ (git-ignored): <account>.md (the brief),
<account>_sent.txt (exactly what the model saw), <account>_key.json (the
redaction key: local only) and <account>_check.json (the checks).
tests/handover_test.py grades a brief against stories/answer_key.json.

Needs ANTHROPIC_API_KEY in .env (like brief.py), except with --dry-run.
"""

import csv
import email
import json
import re
import sys
from email import policy
from pathlib import Path

from settings import SETTINGS

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent
CONF = SETTINGS["handover"]
STORIES = REPO / CONF["stories_folder"]
OUT = REPO / "data" / "handover"
LLM = SETTINGS["llm"]

SYSTEM_PROMPT = """You write handover briefs for a Customer Success manager taking over an
account from a colleague. You get the account's data and documents from
several systems, with people, companies, emails and phone numbers already
replaced by labels (Customer A, CS 1, [email], [phone], the customer).

Write at most 350 words of plain, direct English in Markdown, with these
headings: Where things stand; People; Promises and open commitments; Risks;
What's next; Ask the previous owner (3 questions).

Rules:
- Every sentence ends with the source IDs it rests on, e.g. [C1] or [E2, S1]
  or [DATA]. Use only the IDs you are given.
- Use only facts in the sources. If something important is unknown, say so
  ("Not in the sources: ..."). Never guess names, dates or amounts.
- Keep the labels exactly as given (Customer A, CS 1); never try to work out
  real names.
- Prefer the newest source when two disagree, and say that they disagree.
- Point out where the data and the documents tell different stories.
"""


# --- 1. Gather ---------------------------------------------------------------------

def directory():
    with (STORIES / "directory.csv").open(encoding="utf-8") as f:
        return {r["account_id"]: r for r in csv.DictReader(f)}


def engine_facts(account_id):
    """The engine's own facts for the account, from the export (no model involved)."""
    d = json.loads((REPO / "data" / "views_data.json").read_text(encoding="utf-8"))
    a = next((x for x in d["accounts"] if x["account_id"] == account_id), None)
    if not a:
        sys.exit(f"{account_id} is not in data/views_data.json: run .\\run_all.ps1 first")
    names = {t["owner_id"]: t["owner_name"] for t in d["team"]}
    util = [u for u in a["util"] if u is not None]
    touches = [t for t in d["touches"] if t["account_id"] == account_id
               and t["event"] in ("meeting_held", "email_sent", "workshop_attended")]
    lines = [
        f"Account: {a['account_name']}, {a['tier']}, {a['region']}. Owner: {a['owner'] or 'none'}.",
        f"ARR {a['arr']:,.0f} {SETTINGS['money']['currency']}; contract {a['contract_start']} to {a['renewal_date']} (renewal).",
        f"30-day utilisation of the contract: {util[0] if util else 'n/a'}% on {d['util_dates'][0]}, "
        f"{util[-1] if util else 'n/a'}% on {d['as_of']} (the data's latest day).",
        f"Weekly active users: {a['wau'][0]} thirteen weeks ago, {a['wau'][-1]} last week.",
    ]
    for f in a["flags"]:
        v = f" Value at stake: {f['value']:,.0f}." if f.get("value") else ""
        lines.append(f"Engine flag: {f['flag']} {f['status']}: {f['reasons']}.{v}")
    if a.get("next_qbr_date"):
        lines.append(f"Next QBR booked: {a['next_qbr_date']}.")
    for t in touches[-6:]:
        lines.append(f"CS touch on {t['date']}: {t['event'].replace('_', ' ')} ({(t['topic'] or '').replace('_', ' ')}) "
                     f"by {names.get(t['owner_id'], t['owner_id'])}.")
    for fc in [x for x in d.get("forecasts", []) if x["account_id"] == account_id][-1:]:
        lines.append(f"Owner's renewal forecast: {fc['call']}{' to ' + format(fc['amount'], ',.0f') if fc.get('amount') else ''} "
                     f"({fc.get('reason') or 'no reason given'}).")
    for ns in [x for x in d.get("next_steps", []) if x["account_id"] == account_id][-1:]:
        lines.append(f"Next step planned: {ns['type']} by {ns['due']} ({ns.get('note') or ''}).")
    return a, d["team"], "\n".join(lines)


def tickets(entry):
    with (STORIES / CONF["support_file"]).open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["organization"] == entry["helpdesk_org"]]
    return [{"kind": "S", "date": r["created_at"], "title": f"Ticket {r['ticket_id']} ({r['priority']}, {r['status']}): {r['subject']}",
             "text": f"Opened {r['created_at']} by {r['requester_name']} <{r['requester_email']}>, last updated {r['updated_at']}.\n"
                     f"{r['description']}\nLatest comment: {r['latest_comment']}"} for r in rows]


def emails(entry):
    out = []
    for p in sorted((STORIES / CONF["email_folder"]).glob("*.eml")):
        m = email.message_from_bytes(p.read_bytes(), policy=policy.default)
        people = " ".join(str(m.get(h, "")) for h in ("From", "To", "Cc"))
        if "@" + entry["email_domain"] not in people:
            continue                         # joined by email domain, like a mail archive would be
        body = m.get_body(preferencelist=("plain",)).get_content().strip()
        out.append({"kind": "E", "date": m["Date"].datetime.date().isoformat(), "title": f"Email: {m['Subject']}",
                    "text": f"From: {m['From']}\nTo: {m['To']}\n" + (f"Cc: {m['Cc']}\n" if m["Cc"] else "") + f"\n{body}"})
    return out


def calls(account_id):
    out = []
    for p in sorted((STORIES / CONF["calls_folder"]).glob("*.txt")):
        text = p.read_text(encoding="utf-8")
        head = dict(re.findall(r"^(\w[\w ]*): (.*)$", text.split("\n\n")[0], re.M))
        if head.get("CRM account") != account_id:
            continue                         # joined by the CRM account ID the recorder stores
        out.append({"kind": "C", "date": head["Date"][:10], "title": f"Call: {head['Title']}", "text": text})
    return out


def gather(account_id):
    entry = directory().get(account_id)
    if not entry:
        sys.exit(f"{account_id} has no story in {STORIES / 'directory.csv'}")
    account, team, facts = engine_facts(account_id)
    docs = sorted(tickets(entry) + emails(entry) + calls(account_id), key=lambda x: x["date"])
    counts = {}
    for doc in docs:                         # stable IDs per kind, oldest first: S1, E1, C1 ...
        counts[doc["kind"]] = counts.get(doc["kind"], 0) + 1
        doc["id"] = f"{doc['kind']}{counts[doc['kind']]}"
    return entry, account, team, facts, docs


# --- 2. Redact ---------------------------------------------------------------------

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+\d[\d ()-]{7,}\d")    # international numbers; dates like 2026-10-05 stay
NAME_RE = r"[A-Z][a-z]+(?: [A-Z][a-z]+)+"


def people_in(entry, team, docs, extra=""):
    """Every person named anywhere: email headers, ticket requesters and agents,
    call participants, the CS team. Customer side vs. ours by email domain or label."""
    found = {}
    alltext = " ".join(d["text"] for d in docs)
    for t in team:                           # our own people, only those who appear
        if " " in t["owner_name"] and t["owner_type"] != "pool" and t["owner_name"] in alltext + " " + extra:
            found[t["owner_name"]] = "cs"
    for doc in docs:
        for name, addr in re.findall(r"(" + NAME_RE + r") <([^>]+)>", doc["text"]):
            found.setdefault(name, "customer" if addr.endswith(entry["email_domain"]) else "cs")
        for name in re.findall(r"\(agent (" + NAME_RE + r")\)", doc["text"]):
            found.setdefault(name, "cs")
        for name, org in re.findall(r"(" + NAME_RE + r") \(([^,)]+)", doc["text"]):
            found.setdefault(name, "cs" if org == "Cairnpath" else "customer")
        for name in re.findall(r"^\[[\d:]+\] (" + NAME_RE + r"):", doc["text"], re.M):
            found.setdefault(name, "customer")
    return found


def redact(entry, team, facts, docs):
    """Replace people, contact details and the company. Returns the redacted
    text and the key (label -> real value), which never leaves the laptop."""
    people = people_in(entry, team, docs, facts)
    key, labels, n = {}, {}, {"customer": 0, "cs": 0}
    for name, side in sorted(people.items(), key=lambda x: (x[1], x[0])):
        n[side] += 1
        label = f"Customer {chr(64 + n[side])}" if side == "customer" else f"CS {n[side]}"
        labels[name] = label
        key[label] = name
    company = entry["company"]
    org = entry["helpdesk_org"]

    def clean(text):
        text = EMAIL_RE.sub("[email]", text)
        text = PHONE_RE.sub("[phone]", text)
        for name in sorted(labels, key=len, reverse=True):      # full names first, then first names alone
            text = text.replace(name, labels[name])
        for name in sorted(labels, key=len, reverse=True):
            first = name.split()[0]
            text = re.sub(rf"\b{re.escape(first)}\b", labels[name], text)
        for c in sorted({org, company, company.split()[0]}, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(c)}\b", "the customer", text)
        return text

    parts = [f"[DATA] The engine's own data (usage, flags, CS touches, forecast):\n{clean(facts)}"]
    for doc in docs:
        parts.append(f"[{doc['id']}] {clean(doc['title'])} ({doc['date']})\n{clean(doc['text'])}")
    key["the customer"] = company
    return "\n\n---\n\n".join(parts), key


# --- 4. Check ----------------------------------------------------------------------

def numbers_in(text):
    found = set()
    for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")):
        v = float(n)
        found.add(str(int(v)) if v == int(v) else str(v))
    return found


def check(brief, sent, ids):
    cited = set(re.findall(r"\[([A-Z]+\d*)(?:,|\])", brief)) | {c.strip() for g in re.findall(r"\[([A-Z0-9, ]+)\]", brief) for c in g.split(",")}
    unknown = sorted(c for c in cited if c and c not in ids)
    uncited = []
    for line in brief.splitlines():
        s = line.strip(" -*")
        if len(s) > 40 and not s.startswith("#") and not re.search(r"\[[A-Z0-9, ]+\]", s):
            uncited.append(s[:120])
    numbers = sorted((x for x in numbers_in(brief) - numbers_in(sent) if float(x) > 3), key=float)
    return {"unknown_citations": unknown, "uncited_sentences": uncited, "unverified_numbers": numbers}


def restore(text, key):
    for label in sorted(key, key=len, reverse=True):
        text = text.replace(label, key[label])
    return text


# --- run ---------------------------------------------------------------------------

def run(account_id, dry_run):
    entry, account, team, facts, docs = gather(account_id)
    sent, key = redact(entry, team, facts, docs)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{account_id}_sent.txt").write_text(sent, encoding="utf-8")
    (OUT / f"{account_id}_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    kinds = {k: sum(d["kind"] == k for d in docs) for k in "SEC"}
    print(f"  {account_id}  {entry['company']:<16} {kinds['S']} tickets, {kinds['E']} emails, {kinds['C']} calls; "
          f"{len(key) - 1} people redacted -> data/handover/{account_id}_sent.txt")
    if dry_run:
        return 0, 0
    import anthropic                        # only needed for the real call
    from brief import load_api_key
    load_api_key()
    client = anthropic.Anthropic()
    # the previous owner by label only: the model never sees a real name
    previous = {t["owner_id"]: t["owner_name"] for t in team}.get(entry["handover_from"], "")
    previous = next((label for label, name in key.items() if name == previous), "the previous owner")
    response = client.messages.create(
        model=LLM["model"], max_tokens=CONF["max_output_tokens"], system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content":
                   f"The account is moving from {previous} to a new owner. Sources:\n\n{sent}"}])
    draft = "".join(b.text for b in response.content if b.type == "text")
    usage = (response.usage.input_tokens, response.usage.output_tokens)
    # Only a finished answer counts. "max_tokens" = cut short; an empty draft = nothing written.
    # Either way, save no brief: an empty or half brief would pass every citation check.
    if response.stop_reason != "end_turn" or not draft.strip():
        problem = {"incomplete": f"model stopped with '{response.stop_reason}' after {usage[1]} tokens, "
                                 f"{len(draft)} characters written"}
        (OUT / f"{account_id}.md").unlink(missing_ok=True)
        (OUT / f"{account_id}_check.json").write_text(json.dumps(problem, indent=1), encoding="utf-8")
        print(f"           NO BRIEF: {problem['incomplete']} (raise [handover] max_output_tokens?)")
        return usage
    checks = check(draft, sent, {"DATA"} | {d["id"] for d in docs})
    checks["stop_reason"] = response.stop_reason
    brief = restore(draft, key)
    sources = "\n".join(f"- **{d['id']}** {d['title']} ({d['date']})" for d in docs)
    flags = []
    if checks["unknown_citations"]:
        flags.append("Citations to sources that don't exist: " + ", ".join(checks["unknown_citations"]))
    if checks["unverified_numbers"]:
        flags.append("Numbers found in no source: " + ", ".join(checks["unverified_numbers"]))
    if checks["uncited_sentences"]:
        flags.append(f"{len(checks['uncited_sentences'])} sentence(s) without a source")
    brief += "\n\n---\n**Sources**\n- **DATA** the engine's own data\n" + restore(sources, {})
    brief += ("\n\n**Unverified** (check before relying on it):\n- " + "\n- ".join(flags)) if flags else "\n\nAll sentences cite a source; every number traces back to one."
    brief += "\n\n_Draft: the previous owner confirms it before the new owner relies on it._\n"
    (OUT / f"{account_id}.md").write_text(brief, encoding="utf-8")
    (OUT / f"{account_id}_check.json").write_text(json.dumps(checks, indent=1), encoding="utf-8")
    print(f"           brief -> data/handover/{account_id}.md"
          + (f"  ({'; '.join(flags)})" if flags else "  (all checks passed)"))
    return usage


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    ids = args or list(directory())
    tin = tout = 0
    for account_id in ids:
        i, o = run(account_id, dry)
        tin, tout = tin + i, tout + o
    if not dry:
        cost = (tin * LLM["price_input_per_million"] + tout * LLM["price_output_per_million"]) / 1e6
        print(f"\n{len(ids)} briefs, {tin:,} tokens in, {tout:,} out, about ${cost:.3f}")
