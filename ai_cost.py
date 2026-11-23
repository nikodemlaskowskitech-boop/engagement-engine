"""
The AI cost demo (Phase 9, 23 Nov 2026; AI_COST.md "How we'll show it"): what one question
to ask.py costs, and why, measured on the demo's own questions. No model is called.

For every question in the search corpus's answer key, asked about each account that answers
it (38 pairs today), it builds what ask.py would send in two ways, both redacted exactly as
ask.py does:
  RAG    the engine's facts plus only the documents the search picks ([ask] top_k)
  all    the engine's facts plus every document the account has (no retrieval)
and prices both at the [llm] list prices, with the answer at its [ask] max_output_tokens cap.

Then two lines that only grow in value with the book:
  - as an account's history grows ([ask] cost_demo_sizes documents), "all" grows with it
    and RAG doesn't: it still sends top_k documents;
  - the real calls so far, from the AI spend ledger (ask_account rows), to set our token
    estimate against the real counts.

Tokens are estimated at [ask] chars_per_token characters each (about 4 for English): an
estimate, labelled as one; the ledger holds the real counts once a question has been asked.

    python ai_cost.py        prints the report and writes data/<[outputs] ai_cost>
"""
import csv
import json
import statistics
import sys

import ask
import llm
import search
from cairnpath_db import connect
from settings import OUTPUTS, SETTINGS, output_file

CONF = SETTINGS["ask"]
PRICE_IN = float(SETTINGS["llm"]["price_input_per_million"]) / 1e6
PRICE_OUT = float(SETTINGS["llm"]["price_output_per_million"]) / 1e6
EMBED_PRICE = float(SETTINGS["embed"]["price_per_million"]) / 1e6
CPT = float(CONF["chars_per_token"])
OUT_TOKENS = int(CONF["max_output_tokens"])


def tokens(text):
    """An estimate: characters / chars_per_token (the real count is in the ledger after a call)."""
    return round(len(text) / CPT)


def pairs():
    """(account, question) for each account that answers each corpus question."""
    c = SETTINGS["search"]
    key = json.loads((OUTPUTS / c["corpus_folder"] / c["key_file"]).read_text(encoding="utf-8"))
    return [(a, q["question"]) for q in key["questions"] for a in sorted(q["accounts"])]


def every_document(con_, query, k=None, mode=None, synonyms=True, provider=None, account_id=None):
    """Stands in for search.ranked while measuring "all": the account's every document."""
    return [{"id": i} for (i,) in con_.sql(f"SELECT id FROM documents WHERE account_id = '{account_id}' ORDER BY id").fetchall()]


def measure():
    """One row per (account, question): documents, RAG and "all" input tokens."""
    con = connect()
    search.add_views(con)
    system = tokens(ask.SYSTEM_PROMPT)
    rows, ranked = [], search.ranked
    saved_mode = search.EMBED.get("mode")
    search.EMBED["mode"] = "keywords"          # no model call here; the size depends on top_k, not on the mode
    try:
        for account_id, question in pairs():
            rag, _, chosen, _ = ask.gather(con, account_id, question)
            search.ranked = every_document
            try:
                full, _, every, _ = ask.gather(con, account_id, question)
            finally:
                search.ranked = ranked
            q = tokens(question) + 10           # "Question: ...\n\nSources:" around it
            rows.append({"account_id": account_id, "question": question, "documents": len(every), "picked": len(chosen),
                         "rag_in": system + q + tokens(rag), "all_in": system + q + tokens(full)})
    finally:
        search.EMBED["mode"] = saved_mode
    return rows


def cost(tok_in, tok_out=OUT_TOKENS):
    return tok_in * PRICE_IN + tok_out * PRICE_OUT


def report(rows):
    med = lambda k: statistics.median(r[k] for r in rows)          # noqa: E731
    rag, full = sum(r["rag_in"] for r in rows), sum(r["all_in"] for r in rows)
    per_doc = statistics.median([round((r["all_in"] - r["rag_in"]) / (r["documents"] - r["picked"]))
                                 for r in rows if r["documents"] > r["picked"]] or [0])
    c_rag, c_all = cost(med("rag_in")), cost(med("all_in"))
    lines = [
        f"AI cost demo: one question to ask.py, {len(rows)} questions on the demo corpus (estimates at "
        f"{CPT:g} characters a token; [llm] prices ${PRICE_IN * 1e6:g} in, ${PRICE_OUT * 1e6:g} out per million)",
        "",
        "Today (an account has a median of {:g} documents; RAG picks a median of {:g}):".format(med("documents"), med("picked")),
        f"  input, RAG          median {med('rag_in'):,.0f} tokens   (all {rag:,} over the {len(rows)} questions)",
        f"  input, everything   median {med('all_in'):,.0f} tokens   (all {full:,}): RAG sends {rag / full:.0%} of it",
        f"  answer, capped      {OUT_TOKENS} tokens at most ([ask] max_output_tokens)",
        f"  one question        ${c_rag:.4f} with RAG, ${c_all:.4f} sending everything; "
        f"the answer is {OUT_TOKENS * PRICE_OUT / c_rag:.0%} of it",
        f"  the question's vector (hybrid search)  ${20 * EMBED_PRICE:.7f}: about 20 tokens to Titan, nothing worth counting",
        "",
        f"As an account's history grows (a document is about {per_doc:,} tokens here; RAG still sends {int(CONF['top_k'])}):",
    ]
    base = round(med("rag_in") - med("picked") * per_doc)                 # the facts, the prompt, the question
    for n in [int(x) for x in CONF["cost_demo_sizes"]]:
        full_n, rag_n = base + n * per_doc, base + min(n, int(CONF["top_k"])) * per_doc
        lines.append(f"  {n:>5} documents      everything {full_n:>9,} tokens, ${cost(full_n):.3f} a question;"
                     f"  RAG {rag_n:,} tokens, ${cost(rag_n):.4f}  ({cost(full_n) / cost(rag_n):.0f}x)")
    lines.append(f"  (demo documents are short, about {per_doc} tokens; a real call transcript runs to thousands, "
                 "so a real history grows faster than this)")
    real = ledger_rows()
    lines.append("")
    if real:
        mi, mo = statistics.median(r[0] for r in real), statistics.median(r[1] for r in real)
        lines.append(f"Real calls so far (ledger, ask_account): {len(real)}, median {mi:,.0f} tokens in and {mo:,.0f} out, "
                     f"median ${statistics.median(r[2] for r in real):.4f} a question. Our estimate for the input: "
                     f"{med('rag_in'):,.0f} (the questions differ, so read it as a scale check).")
    else:
        lines.append("Real calls so far (ledger, ask_account): none yet. Ask one (python ask.py <account> \"<question>\") "
                     "and run this again: the real counts appear here.")
    lines += ["", "What it shows: at today's size the answer, not the documents, is most of the bill, so the output cap",
              "(AI_COST.md #2) saves more than retrieval does; retrieval pays as histories grow, and keeps paying."]
    return "\n".join(lines)


def ledger_rows():
    """The ask_account calls in the AI spend ledger: (tokens in, tokens out, cost)."""
    if not llm.LEDGER.exists():
        return []
    with llm.LEDGER.open(encoding="utf-8", newline="") as f:
        return [(int(r["input_tokens"]), int(r["output_tokens"]), float(r["cost_usd"]))
                for r in csv.DictReader(f) if r.get("feature") == "ask_account"]


def main(out=None):
    rows = measure()
    text = report(rows)
    out = out or output_file("ai_cost")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    return rows, text


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(main()[1])
