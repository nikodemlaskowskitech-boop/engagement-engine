"""
Ask about one account (Phase 9, 14 Nov 2026): a question in plain words, answered by
the model in a few sentences from the engine's facts and the account's own tickets,
emails and call notes, every sentence citing its source.

    python ask.py acc_177 "Why might they downsize at renewal?"
    python ask.py acc_177 "..." --dry-run      # show what the model would get, no call
    python ask.py acc_177 "..." --redacted     # the AWS way: from the redacted file only

Two places, one way (26 Nov 2026, the Ask box): on the laptop it reads the raw documents and
redacts them here; in AWS (source="redacted") it reads only the redacted file the public
search uses and the engine's export, so the function there never holds a customer's name.

How (retrieval-augmented generation, RAG, on the laptop):
  1. Retrieve: the account's documents that match the question best (search.py, BM25
     with the CS vocabulary), [ask] top_k of them, plus the engine's facts ([DATA],
     handover.engine_facts). An account without documents is answered from its facts.
  2. Redact: the handover brief's redaction (handover.cleaner, with the folder's contact
     list), then its leak check; anything person-shaped left = no call.
  3. Ask: through llm.call, so the [llm] budget for "ask_account" is checked first and
     the call goes in the ledger (laptop only: AWS has no budget for it, so it's refused).
  4. Check: handover.check lists unknown citations, sentences without one and numbers in
     no source; real names go back in after the model (handover.restore), never before.

SQL computes the facts; the model only reads and writes them up.
"""
import re
import sys

import documents
import handover
import llm
import search
from cairnpath_db import connect
from settings import SETTINGS

CONF = SETTINGS["ask"]


class Refused(Exception):
    """Not asked, with the reason a person can act on (the CLI prints it, the Ask box shows it)."""

SYSTEM_PROMPT = """You answer one question about one customer account for a Customer
Success manager. You get the engine's own data [DATA] and the documents that match the
question best, with people, companies, emails and phone numbers already replaced by
labels (Customer A, CS 1, [email], [phone], the customer).

Answer in at most 150 words of plain, direct English. No headings.

Rules:
- Every sentence ends with the source IDs it rests on, e.g. [E1] or [S2, DATA].
  Use only the IDs you are given.
- Use only facts in the sources. If the sources don't answer the question, say so in a
  sentence starting "Not in the sources:". Never guess names, dates or amounts.
- Keep the labels exactly as given; never try to work out real names.
- Where the data and the documents tell different stories, say so.
"""


def gather(con, account_id, question):
    """The facts, the documents chosen for the question and the redaction for this account.
    Returns (sent, key, chosen documents, the cleaner) or exits with a reason."""
    if not re.fullmatch(r"[\w-]+", account_id):
        raise Refused(f"not an account ID: {account_id!r}")
    account, team, facts = handover.engine_facts(account_id)      # exits if not in the export
    row = con.sql(f"SELECT folder FROM doc_folders WHERE account_id = '{account_id}'").fetchone()
    if row:
        folder = row[0]
        entry = documents.directory(folder)[account_id]
        docs = documents.for_account(account_id, folder, entry)  # all of them: people named anywhere
        known = handover.contacts(account_id, folder)
    else:                                       # no documents: the facts alone, the company still redacted
        entry = {"account_id": account_id, "company": account["account_name"],
                 "helpdesk_org": account["account_name"], "email_domain": ""}
        docs, known = [], []
    hits = search.ranked(con, question, int(CONF["top_k"]), account_id=account_id) if docs else []   # [embed] mode (23 Nov 2026)
    picked = {h["id"] for h in hits}
    chosen = [d for d in docs if d["id"] in picked]               # oldest first, like the brief
    clean, key = handover.cleaner(entry, team, facts, docs, known)
    parts = [f"[DATA] The engine's own data (usage, flags, CS touches, forecast):\n{clean(facts)}"]
    for d in chosen:
        parts.append(f"[{d['id']}] {clean(d['title'])} ({d['date']})\n{clean(d['text'])}")
    return "\n\n---\n\n".join(parts), key, chosen, clean


def gather_redacted(con, account_id, question, export, rows, mode="keywords"):
    """The same as gather(), from what may leave the laptop only (the Ask function in AWS):
    the export's facts, redacted here (the account's and the team's names: the facts have no
    customer people), and the account's documents from the redacted file, already without a
    name. The key can only turn our own team and the account's name back, never a customer's."""
    if not re.fullmatch(r"[\w-]+", account_id):
        raise Refused(f"not an account ID: {account_id!r}")
    if not any(a["account_id"] == account_id for a in export["accounts"]):
        raise Refused(f"{account_id} is not in the engine's export")
    account, team, facts = handover.engine_facts(account_id, export)
    entry = {"account_id": account_id, "company": account["account_name"],
             "helpdesk_org": account["account_name"], "email_domain": ""}
    docs = [r for r in rows if r["account_id"] == account_id]
    hits = search.ranked(con, question, int(CONF["top_k"]), mode=mode, account_id=account_id) if docs else []
    picked = {h["id"] for h in hits}
    chosen = [d for d in docs if d["id"] in picked]               # oldest first, like the brief
    clean, key = handover.cleaner(entry, team, facts, [], [])
    # The documents were labelled on their own (CS 1, Customer A per account), so "CS 1" in
    # them may be someone else than the facts' "CS 1": our team in the facts is "Our team 1"
    # instead, and the model is told the two sets don't match.
    ours = lambda text: re.sub(r"\bCS (\d+)\b", r"Our team \1", text)          # noqa: E731
    key = {ours(label): name for label, name in key.items()}
    parts = ["[DATA] The engine's own data (usage, flags, CS touches, forecast). Our people here are "
             "'Our team 1, 2'; the documents label people on their own (CS 1, Customer A), so a label "
             f"in a document is not the same person as one in [DATA]:\n{ours(clean(facts))}"]
    for d in chosen:
        parts.append(f"[{d['id']}] {clean(d['title'])} ({d['date']})\n{clean(d['text'])}")
    asked_clean = lambda text: ours(clean(text))                                    # noqa: E731
    return "\n\n---\n\n".join(parts), key, chosen, asked_clean


def ask(account_id, question, dry_run=False, client=None):
    """The answer with its sources and checks, or what would be sent (dry run).
    Returns a dict: answer (real names back in), sources, checks, sent (redacted).
    In AWS: ask_redacted(), the same steps from the redacted file."""
    return _ask(account_id, question, dry_run, client, None)


def ask_redacted(account_id, question, export, rows, con=None, dry_run=False, client=None, mode="keywords"):
    """ask() for the Ask function in AWS: export = the engine's views_data, rows = the
    redacted documents (the public search's file)."""
    if con is None:
        import duckdb
        con = duckdb.connect()
        search.add_views(con, rows=rows)
    return _ask(account_id, question, dry_run, client,
                lambda q: gather_redacted(con, account_id, q, export, rows, mode))


def _ask(account_id, question, dry_run, client, gatherer):
    question = question.strip()
    if not question:
        raise Refused("Ask a question.")
    if gatherer is None:                        # the laptop: the raw documents, redacted here
        con = connect()
        search.add_views(con)
        gatherer = lambda q: gather(con, account_id, q)          # noqa: E731
    sent, key, chosen, clean = gatherer(question)
    asked = clean(question)                     # a name typed into the question is redacted too
    leaks = handover.leak_check(sent + "\n" + asked, key)
    if leaks:                                   # nothing goes out: a person looks first (PRIVACY.md)
        raise Refused(f"Not sent: {len(leaks)} thing(s) still look like a person or a contact detail: "
                      f"{', '.join(leaks)}. If one isn't a person, add it to [handover] leak_check_allow.")
    result = {"account_id": account_id, "question": question, "sent": sent, "asked": asked,
              "sources": [{"id": "DATA", "title": "the engine's own data"}]
              + [{"id": d["id"], "title": d["title"], "date": d["date"]} for d in chosen]}
    if dry_run:
        return result
    client = client or llm.client()
    response = llm.call(client, "ask_account", account_id,
                        model=llm.MODEL, max_tokens=int(CONF["max_output_tokens"]), system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": f"Question: {asked}\n\nSources:\n\n{sent}"}])
    draft = "".join(b.text for b in response.content if b.type == "text")
    if response.stop_reason != "end_turn" or not draft.strip():
        raise Refused(f"No answer: the model stopped with '{response.stop_reason}' "
                      f"(raise [ask] max_output_tokens?)")
    result["checks"] = handover.check(draft, sent, {"DATA"} | {d["id"] for d in chosen})
    result["answer"] = handover.restore(draft, key)
    return result


def show(r):
    print(f"\n{r['account_id']}: {r['question']}\n")
    if "answer" in r:
        print(r["answer"].strip() + "\n")
    else:
        print("(dry run: no model call) What the model would get, redacted:\n\n" + r["sent"] + "\n")
    print("Sources:")
    for s in r["sources"]:
        print(f"  [{s['id']}] {s['title']}" + (f" ({s['date']})" if s.get("date") else ""))
    c = r.get("checks")
    if c:
        flags = ([f"citations to sources that don't exist: {', '.join(c['unknown_citations'])}"] if c["unknown_citations"] else []) \
            + ([f"numbers found in no source: {', '.join(c['unverified_numbers'])}"] if c["unverified_numbers"] else []) \
            + ([f"{len(c['uncited_sentences'])} sentence(s) without a source"] if c["uncited_sentences"] else []) \
            + ([f"a person's label used for the account: {'; '.join(c['label_mixups'])}"] if c.get("label_mixups") else [])
        print("\nUnverified (check before relying on it):\n  - " + "\n  - ".join(flags) if flags
              else "\nEvery sentence cites a source; every number traces back to one.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        sys.exit('Usage: python ask.py <account_id> "<question>" [--dry-run] [--redacted]')
    try:
        if "--redacted" in sys.argv:            # the AWS path, tried on the laptop
            import json
            from pathlib import Path
            export = json.loads(handover.VIEWS.read_text(encoding="utf-8"))
            site_file = Path(__file__).resolve().parent / SETTINGS["search"]["site_documents_file"]
            rows = [json.loads(x) for x in site_file.read_text(encoding="utf-8").splitlines() if x.strip()]
            show(ask_redacted(args[0], args[1], export, rows, dry_run="--dry-run" in sys.argv))
        else:
            show(ask(args[0], args[1], dry_run="--dry-run" in sys.argv))
    except Refused as e:
        sys.exit(str(e))
    except llm.OverBudget as e:
        sys.exit(f"Not asked, over budget: {e} (python llm.py --spend)")
