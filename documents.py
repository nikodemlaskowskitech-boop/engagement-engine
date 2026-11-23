"""
One home for the unstructured data (ARCHITECTURE.md fix D, 13 Nov 2026): support
tickets, emails and call notes, in one shape, read one way.

Each lives in its own system and joins to an account by its own key, like in a
real company: the helpdesk by organisation name, the mail archive by email
domain, the call recorder by CRM account ID (stories/directory.csv maps each
account to all three). Until now handover.py read them its own way; search and
the MCP server (Phase 9) would have been a second and third way.

Every document comes out as the same dict:
    kind    S (support ticket), E (email) or C (call)
    source  its own ID in its own system ("ticket 4512", "email <id@...>", "call x.txt"):
            what the handover cache recognises it by
    date    YYYY-MM-DD
    title   one line ("Ticket 4512 (high, open): ...", "Email: ...", "Call: ...")
    text    the full text, as written: real names, emails and phone numbers
    id      S1, E2, C1 ...: per account and kind, oldest first (the citations)
    case    the case it belongs to (23 Nov 2026): the citation of the case's oldest document.
            One case = documents linked by keys that are certain, never by topic: an email's
            replies (In-Reply-To, References, or "Re:" on the same subject), a ticket number or
            an invoice number quoted by another document, a call logged against a ticket
            ("Related case:"). A wrong merge would hide a second problem inside the first.
    team    who it comes from: Support (tickets), CS, or Implementation
    logged_by  the teams that logged it in the CRM: an email both the CSM and the
            implementation lead logged is one email, logged twice, kept once (dedup by
            Message-ID), with both teams here

These are raw texts. Nothing here goes to a model: handover.py (and later the
search) redacts them first (PRIVACY.md).

    import documents
    documents.for_account("acc_024")      # that account's documents, oldest first
    documents.add_table(con)              # a DuckDB table "documents" over all of them

The file names come from settings.toml [handover] (stories_folder, support_file,
email_folder, calls_folder). Every function takes the stories folder, so tests
can point at a copy.
"""
import csv
import email
import json
import re
import sys
import tempfile
from email import policy
from pathlib import Path

from settings import REPO, SETTINGS

CONF = SETTINGS["handover"]
STORIES = REPO / CONF["stories_folder"]


def directory(stories=None):
    """Each story account and its key in each system: helpdesk_org, email_domain."""
    with (Path(stories or STORIES) / "directory.csv").open(encoding="utf-8") as f:
        return {r["account_id"]: r for r in csv.DictReader(f)}


TEAMS = ("Support", "CS", "Implementation")
_REPLY = re.compile(r"^\s*((re|fwd?|aw|sv)\s*:\s*)+", re.I)
_INVOICE = re.compile(r"\bINV-\d{3,}\b")


def tickets(entry, stories=None):
    """The helpdesk's tickets for the account, joined by organisation name."""
    with (Path(stories or STORIES) / CONF["support_file"]).open(encoding="utf-8") as f:
        found = [r for r in csv.DictReader(f) if r["organization"] == entry["helpdesk_org"]]
    # "source" = each document's own ID in its system: what the cache recognises it by
    return [{"kind": "S", "source": f"ticket {r['ticket_id']}", "date": r["created_at"],
             "team": "Support", "logged_by": ["Support"], "_keys": {f"T:{r['ticket_id']}"},
             "title": f"Ticket {r['ticket_id']} ({r['priority']}, {r['status']}): {r['subject']}",
             "text": f"Opened {r['created_at']} by {r['requester_name']} <{r['requester_email']}>, last updated {r['updated_at']}.\n"
                     f"{r['description']}\nLatest comment: {r['latest_comment']}"} for r in found]


def emails(entry, stories=None):
    """The mail archive's emails with the account's domain in From, To or Cc. One email logged
    by two people (each with the CRM plugin) is two files with one Message-ID: kept once, with
    both teams in logged_by (X-Logged-By: CS, Implementation; none = CS, the CSM's own log)."""
    out, seen = [], {}
    for p in sorted((Path(stories or STORIES) / CONF["email_folder"]).glob("*.eml")):
        m = email.message_from_bytes(p.read_bytes(), policy=policy.default)
        people = " ".join(str(m.get(h, "")) for h in ("From", "To", "Cc"))
        if "@" + entry["email_domain"] not in people:
            continue                         # joined by email domain, like a mail archive would be
        mid = str(m["Message-ID"] or p.name).strip()
        logged = str(m.get("X-Logged-By") or "CS").strip()
        if mid in seen:                      # the same email, logged again by someone else
            seen[mid]["logged_by"] = sorted(set(seen[mid]["logged_by"]) | {logged})
            continue
        body = m.get_body(preferencelist=("plain",)).get_content().strip()
        subject = str(m["Subject"] or "")
        links = {f"M:{x}" for x in re.findall(r"<[^>]+>", f"{m.get('In-Reply-To', '')} {m.get('References', '')}")}
        if _REPLY.match(subject):            # "Re: X" without headers (a pasted reply): its subject
            links.add("S:" + _REPLY.sub("", subject).strip().lower())
        doc = {"kind": "E", "source": f"email {mid}",
               "date": m["Date"].datetime.date().isoformat(), "title": f"Email: {subject}",
               "text": f"From: {m['From']}\nTo: {m['To']}\n" + (f"Cc: {m['Cc']}\n" if m["Cc"] else "") + f"\n{body}",
               "team": str(m.get("X-Team") or "CS").strip(), "logged_by": [logged],
               "_keys": {f"M:{mid}"} | ({"S:" + subject.strip().lower()} if not _REPLY.match(subject) else set()),
               "_links": links}
        seen[mid] = doc
        out.append(doc)
    return out


def calls(account_id, stories=None):
    """The call recorder's transcripts tagged with the CRM account ID."""
    out = []
    for p in sorted((Path(stories or STORIES) / CONF["calls_folder"]).glob("*.txt")):
        text = p.read_text(encoding="utf-8")
        head = dict(re.findall(r"^(\w[\w ]*): (.*)$", text.split("\n\n")[0], re.M))
        if head.get("CRM account") != account_id:
            continue                         # joined by the CRM account ID the recorder stores
        who = head.get("Participants", "")
        team = "Implementation" if "(Cairnpath, Implementation" in who else "CS"
        out.append({"kind": "C", "source": f"call {p.name}", "date": head["Date"][:10],
                    "title": f"Call: {head['Title']}", "text": text, "team": team, "logged_by": [team],
                    "_keys": {f"C:{p.name}"},
                    "_links": {f"T:{t.strip()}" for t in head.get("Related case", "").split(",") if t.strip()}})
    return out


def for_account(account_id, stories=None, entry=None):
    """All of one account's documents, oldest first, each with its citation ID
    (S1, E1, C1 ...: stable per kind). [] for an account with no story."""
    entry = entry or directory(stories).get(account_id)
    if not entry:
        return []
    docs = sorted(tickets(entry, stories) + emails(entry, stories) + calls(account_id, stories),
                  key=lambda x: x["date"])
    counts = {}
    for doc in docs:
        counts[doc["kind"]] = counts.get(doc["kind"], 0) + 1
        doc["id"] = f"{doc['kind']}{counts[doc['kind']]}"
    _cases(docs)
    return docs


def _cases(docs):
    """Each document's case (see the top): documents joined by a key both carry, in one
    account. Union-find over the keys; the case is named by its oldest document's citation."""
    ticket_ids = {k[2:] for d in docs for k in d.get("_keys", ()) if k.startswith("T:")}
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for d in docs:
        text = d["title"] + "\n" + d["text"]
        quoted = {f"T:{t}" for t in ticket_ids if re.search(rf"\b{re.escape(t)}\b", text)}
        quoted |= {f"I:{i}" for i in _INVOICE.findall(text)}
        for key in set(d.pop("_keys", ())) | set(d.pop("_links", ())) | quoted:
            parent[find("D:" + d["id"])] = find(key)
    first = {}
    for d in docs:                                       # oldest first: the first seen names the case
        first.setdefault(find("D:" + d["id"]), d["id"])
    for d in docs:
        d["case"] = first[find("D:" + d["id"])]


def all_documents(stories=None):
    """Every story account's documents, one row each, with its account_id."""
    return [{"account_id": acc, **doc} for acc in directory(stories) for doc in for_account(acc, stories)]


def add_table(con, stories=None, rows=None):
    """A DuckDB table "documents" (account_id, id, kind, source, date, title, text, case_id,
    team, logged_by) over
    every document, for search and SQL. stories = one folder or a list of folders (the
    stories and the search corpus). Raw text: redact before anything leaves.
    rows = documents already in this shape instead (the public search's redacted file,
    which has no source: the ID stands in)."""
    if rows is None:
        folders = stories if isinstance(stories, (list, tuple)) else [stories]
        rows = [r for folder in folders for r in all_documents(folder)]
    # the redacted file has no source (the ID stands in); a row without a case is its own case
    rows = [{"source": r["id"], "case": r["id"], "team": "", "logged_by": [], **r} for r in rows]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as tmp:
        tmp.write("\n".join(json.dumps(r) for r in rows))
    path = tmp.name.replace("\\", "/")
    con.sql(f"""CREATE OR REPLACE TABLE documents AS
                SELECT account_id, id, kind, source, "date"::DATE AS date, title, text,
                       "case" AS case_id, team, logged_by
                FROM read_json('{path}', format = 'newline_delimited', columns = {{
                    account_id: 'VARCHAR', id: 'VARCHAR', kind: 'VARCHAR', source: 'VARCHAR',
                    date: 'VARCHAR', title: 'VARCHAR', text: 'VARCHAR', "case": 'VARCHAR',
                    team: 'VARCHAR', logged_by: 'VARCHAR[]'}})""")
    Path(tmp.name).unlink()
    return len(rows)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for acc, entry in directory().items():
        docs = for_account(acc)
        kinds = {k: sum(d["kind"] == k for d in docs) for k in "SEC"}
        print(f"{acc}  {kinds['S']} tickets, {kinds['E']} emails, {kinds['C']} calls")
