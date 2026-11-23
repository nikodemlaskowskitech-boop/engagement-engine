"""
Account search over support tickets, emails and call notes (Phase 9, 13 Nov 2026).

Ask in plain words ("which customers are under budget pressure?") and get the
documents that match best, each with its account, citation ID and a line of
context, and the accounts they belong to, best first.

How it ranks: BM25, the standard keyword ranking behind most search engines (and
DuckDB's own full-text extension). A document scores higher when it holds more of
the question's words, when those words are rare across all documents (so "invoice"
counts more than "team"), and less when it's long (a word in a short note says more).
Written here in plain SQL rather than with DuckDB's fts extension, because the
extension is downloaded from the internet on first use: our sandbox can't, and the
engine in AWS shouldn't fetch code while it runs. Same numbers everywhere, nothing to
install.

Synonyms (13 Nov 2026): keywords only find the words used, so a CS-owned vocabulary in
settings ([search.synonyms]) widens a question: "competitor" also searches for
benchmark, RFP, alternative... A synonym counts [search] synonym_weight (less than the
question's own word), so a document that uses the question's words still ranks first.
It's the classic fix in enterprise search: cheap, explainable, and a person can read and
change the list; it can't find what nobody listed. synonyms=False searches without it.

Words are cut to a rough stem (evaluating, evaluate -> evaluat; invoices -> invoice)
and common words dropped ([search] stop_words), the same way for documents and
questions. Keywords only find the words used: "competitor" doesn't find "we're
benchmarking Lumen Analytics". tests/search_test.py measures how much that costs
(recall per question) against the corpus's answer key. Meaning (22 Nov 2026): embed.py
gives each redacted document a vector; meaning() ranks by them and ranked() merges both
lists ([embed] mode: keywords, meaning or hybrid).

The documents are raw text with real names. search() and accounts() rank them, on the
laptop. redacted(con, hits) is the only form that may leave (a model, the MCP server):
each account's results cleaned by the handover brief's own redaction (people to
Customer A / CS 1, emails and phones masked, the company to "the customer"; labels are
per account), then the leak check; an account where anything person-shaped survives is
held back and listed, never sent (PRIVACY.md).

    python search.py "which customers are under budget pressure?"
    python search.py "invoice" --account acc_053
"""
import argparse
import re
import sys

import documents
from settings import OUTPUTS, SETTINGS

CONF = SETTINGS["search"]
CORPUS = OUTPUTS / CONF["corpus_folder"]

# The same steps for documents and questions: lower case, split on anything that isn't a
# letter or digit, drop stop words, cut to a rough stem (Porter-lite: -ing, -ed, -ies, -s, -e).
STEM_SQL = """
CREATE OR REPLACE MACRO stem1(w) AS CASE
    WHEN length(w) > 5 AND w LIKE '%ing' THEN left(w, length(w) - 3)
    WHEN length(w) > 4 AND w LIKE '%ed'  THEN left(w, length(w) - 2)
    WHEN length(w) > 4 AND w LIKE '%ies' THEN left(w, length(w) - 3) || 'y'
    WHEN length(w) > 3 AND w LIKE '%s' AND w NOT LIKE '%ss' THEN left(w, length(w) - 1)
    ELSE w END;
CREATE OR REPLACE MACRO stem(w) AS
    CASE WHEN length(stem1(w)) > 4 AND stem1(w) LIKE '%e' THEN left(stem1(w), length(stem1(w)) - 1)
         ELSE stem1(w) END;
"""


def _words_sql(text_sql):
    """SQL for the stemmed words of a text expression, one row per word (stop words out)."""
    stops = ", ".join(f"'{w}'" for w in CONF["stop_words"])
    return f"""SELECT stem(w) AS term FROM (
                 SELECT unnest(regexp_split_to_array(lower({text_sql}), '[^a-z0-9äöüßéèàâçøåæ]+')) AS w)
               WHERE length(w) > 1 AND w NOT IN ({stops})"""


def add_views(con, folders=None, rows=None):
    """The documents (the stories and, if generated, the search corpus) and the search
    index over them: doc_terms (how often each word is in each document) and doc_stats
    (how many documents, their average length). Returns the number of documents.
    rows = already-redacted documents instead of folders (the public search, which has
    nothing else: no folders, no contacts, no raw text)."""
    if rows is not None:
        folders = []
    elif folders is None:
        folders = [documents.STORIES] + ([CORPUS] if (CORPUS / "directory.csv").exists() else [])
    n = documents.add_table(con, folders, rows)
    # which folder each account's documents came from: redaction reads that folder's contacts
    pairs = ", ".join(f"('{acc}', '{str(folder).replace(chr(92), '/')}')" for folder in folders
                      for acc in documents.directory(folder) if re.fullmatch(r"[\w-]+", acc))
    pairs = pairs or "(NULL, NULL)"
    con.sql(f"CREATE OR REPLACE TABLE doc_folders AS SELECT * FROM (VALUES {pairs}) v(account_id, folder)")
    con.sql(STEM_SQL)
    con.sql(f"""CREATE OR REPLACE TABLE doc_terms AS
        WITH words AS (
            SELECT d.account_id, d.id, stem(t.w) AS term
            FROM documents d,
                 unnest(regexp_split_to_array(lower(d.title || ' ' || d.text), '[^a-z0-9äöüßéèàâçøåæ]+')) t(w)
            WHERE length(t.w) > 1 AND t.w NOT IN ({", ".join(f"'{w}'" for w in CONF["stop_words"])}))
        SELECT account_id, id, term, count(*) AS tf FROM words GROUP BY ALL""")
    con.sql("""CREATE OR REPLACE TABLE doc_stats AS
        WITH lens AS (SELECT account_id, id, sum(tf) AS len FROM doc_terms GROUP BY ALL)
        SELECT (SELECT count(*) FROM documents) AS n_docs, avg(len) AS avg_len FROM lens""")
    con.sql("""CREATE OR REPLACE TABLE doc_len AS
        SELECT account_id, id, sum(tf) AS len FROM doc_terms GROUP BY ALL""")
    # The vocabulary: each word of a question and the words it also stands for, both stemmed
    pairs = [(_word(k), _word(s)) for k, words in CONF.get("synonyms", {}).items() for s in words]
    values = ", ".join(f"('{k}', '{s}')" for k, s in pairs if k and s) or "(NULL, NULL)"
    con.sql(f"""CREATE OR REPLACE TABLE synonyms AS
        SELECT DISTINCT stem(k) AS term, stem(s) AS also FROM (VALUES {values}) v(k, s) WHERE k IS NOT NULL""")
    return n


def _word(w):
    """A vocabulary entry: lower-case letters only (it goes into SQL text)."""
    return re.sub(r"[^a-zäöüßéèàâçøåæ]", "", str(w).lower())


def _clean(query):
    """Only letters, digits and spaces reach the SQL (the question is typed by a person)."""
    return re.sub(r"[^\w ]+", " ", str(query), flags=re.UNICODE).replace("_", " ").strip()


def search(con, query, k=None, account_id=None, synonyms=True):
    """The k documents that match the question best (BM25), best first: account_id, id
    (the citation: S1, E2, C1), kind, date, title, score and the line that matched best.
    account_id = search one account's documents only. synonyms = widen the question with
    [search.synonyms], each counting synonym_weight."""
    k = int(k or CONF["top_k"])
    q = _clean(query)
    if not q:
        return []
    only = ""
    if account_id:
        if not re.fullmatch(r"[\w-]+", account_id):
            raise ValueError(f"not an account ID: {account_id!r}")
        only = f"AND t.account_id = '{account_id}'"
    k1, b = float(CONF["bm25_k1"]), float(CONF["bm25_b"])
    weight = float(CONF["synonym_weight"])
    rel = con.sql(f"""
        WITH asked AS (SELECT DISTINCT term FROM ({_words_sql(f"'{q}'")})),
             -- the question's own words count 1, the words they also stand for synonym_weight
             q AS (SELECT term, max(weight) AS weight FROM (
                      SELECT term, 1.0 AS weight FROM asked
                      UNION ALL
                      SELECT s.also, {weight} FROM synonyms s JOIN asked a USING (term) WHERE {bool(synonyms)})
                   GROUP BY term),
             df AS (SELECT term, count(*) AS df FROM doc_terms WHERE term IN (SELECT term FROM q) GROUP BY term),
             scored AS (
                SELECT t.account_id, t.id,
                       sum(q.weight * ln(1 + (s.n_docs - df.df + 0.5) / (df.df + 0.5))
                           * t.tf * ({k1} + 1)
                           / (t.tf + {k1} * (1 - {b} + {b} * l.len / s.avg_len))) AS score,
                       list(t.term ORDER BY t.term) AS matched
                FROM doc_terms t JOIN df USING (term) JOIN q USING (term)
                     JOIN doc_len l USING (account_id, id), doc_stats s
                WHERE true {only}
                GROUP BY ALL)
        SELECT s.account_id, s.id, d.kind, d.date, d.title, round(s.score, 3) AS score, s.matched, d.text
        FROM scored s JOIN documents d USING (account_id, id)
        ORDER BY s.score DESC, s.account_id, s.id   -- no ties: the same question, the same order
        LIMIT {k}""")
    out = []
    for account, doc_id, kind, day, title, score, matched, text in rel.fetchall():
        out.append({"account_id": account, "id": doc_id, "kind": kind, "date": str(day), "title": title,
                    "score": float(score), "matched": list(matched), "line": _best_line(text, matched)})
    return out


# --- Search by meaning (22 Nov 2026): vectors from embed.py, next to the keywords ---------
EMBED = SETTINGS.get("embed", {})
VECTORS_FILE = None               # tests point this at their own file; None = data/<[outputs] vectors>


def _vectors(con):
    """The documents' vectors (embed.py) as a table, loaded once per connection."""
    if con.sql("SELECT count(*) FROM duckdb_tables() WHERE table_name = 'doc_vectors'").fetchone()[0]:
        return
    from settings import output_file
    path = VECTORS_FILE or output_file("vectors")
    if not path.exists():
        raise FileNotFoundError(f"No vectors yet ({path.name}): run python embed.py first")
    con.sql(f"""CREATE TABLE doc_vectors AS
        SELECT account_id, id, vector::DOUBLE[] AS vector
        FROM read_json('{str(path).replace(chr(92), "/")}', format = 'newline_delimited',
                       columns = {{account_id: 'VARCHAR', id: 'VARCHAR', vector: 'DOUBLE[]'}})""")


def meaning(con, query, k=None, provider=None, account_id=None):
    """The k documents closest in meaning to the question (cosine of the vectors), best first,
    in search()'s shape. The question is embedded by the same model as the documents.
    account_id = one account's documents only."""
    import embed
    k = int(k or CONF["top_k"])
    q = str(query).strip()
    if not q:
        return []
    only = ""
    if account_id:
        if not re.fullmatch(r"[\w-]+", account_id):
            raise ValueError(f"not an account ID: {account_id!r}")
        only = f"WHERE v.account_id = '{account_id}'"
    _vectors(con)
    qv = "[" + ",".join(f"{x:.6f}" for x in embed.embed_query(q, provider)) + "]::DOUBLE[]"
    rel = con.sql(f"""
        SELECT v.account_id, v.id, d.kind, d.date, d.title,
               round(list_cosine_similarity(v.vector, {qv}), 4) AS score, d.text
        FROM doc_vectors v JOIN documents d USING (account_id, id)
        {only}
        ORDER BY score DESC, v.account_id, v.id
        LIMIT {k}""")
    words = [w for w in re.findall(r"[a-z]+", q.lower()) if len(w) > 3]
    return [{"account_id": a, "id": i, "kind": kind, "date": str(day), "title": title, "score": float(score),
             "matched": [], "line": _best_line(text, words)}
            for a, i, kind, day, title, score, text in rel.fetchall()]


_fell_back = False                # warn once per process, not once per question


def ranked(con, query, k=None, mode=None, synonyms=True, provider=None, account_id=None):
    """The documents, ranked the [embed] mode's way: keywords (BM25), meaning (vectors) or
    hybrid (both lists merged by reciprocal rank fusion: each list adds 1 / (rrf_k + rank),
    so a document high in either list rises, and high in both rises most).
    With no vectors yet, or no answer from the model (not signed in to AWS, offline), it
    says so once and falls back to keywords: a search always answers (23 Nov 2026)."""
    global _fell_back
    k = int(k or CONF["top_k"])
    mode = mode or EMBED.get("mode", "keywords")
    if mode not in ("keywords", "meaning", "hybrid"):
        raise ValueError(f"[embed] mode must be keywords, meaning or hybrid, not {mode!r}")
    if mode == "keywords":
        return search(con, query, k, account_id, synonyms)
    try:
        by_meaning = meaning(con, query, k * 3, provider, account_id)
    except Exception as e:                       # noqa: BLE001 (any failure: the keywords still answer)
        if not _fell_back:
            print(f"search: {mode} unavailable ({type(e).__name__}: {str(e)[:120]}); keywords only", file=sys.stderr)
            _fell_back = True
        return search(con, query, k, account_id, synonyms)
    if mode == "meaning":
        return by_meaning[:k]
    rrf = int(EMBED.get("rrf_k", 60))
    lists = [search(con, query, k * 3, account_id, synonyms), by_meaning]
    best, fused = {}, {}
    for hits in lists:
        for rank, h in enumerate(hits, 1):
            key = (h["account_id"], h["id"])
            fused[key] = fused.get(key, 0.0) + 1.0 / (rrf + rank)
            best.setdefault(key, h)            # the keyword hit first: it knows the words that matched
    order = sorted(fused, key=lambda key: (-fused[key], key))   # no ties: the same question, the same order
    return [{**best[key], "score": round(fused[key], 5)} for key in order[:k]]


def accounts(con, query, k=None, synonyms=True, mode=None, provider=None):
    """The accounts whose documents match the question best: each account once, ranked by
    its best document, with the citations that matched (k accounts). mode: see ranked()."""
    k = int(k or CONF["top_k"])
    hits = ranked(con, query, k * 5, mode, synonyms, provider)   # enough documents to fill k accounts
    by_account = {}
    for h in hits:
        r = by_account.setdefault(h["account_id"], {"account_id": h["account_id"], "score": h["score"], "documents": []})
        r["documents"].append(h["id"])
    return list(by_account.values())[:k]


def redacted(con, hits):
    """The hits in the only form that may leave the laptop: title, line and the document's
    text redacted per account, then the leak check. Returns (safe, held_back): safe = the
    hits without a real name, email, phone or company; held_back = the accounts the leak
    check stopped, with what it found (for a person on the laptop, never sent on).
    The redaction keys stay here: nothing returned can turn a label back into a name."""
    import handover                # its redaction and leak check: one way to redact (imported here
                                   # so the public search function, which never redacts, doesn't carry it)
    folders = dict(con.sql("SELECT account_id, folder FROM doc_folders").fetchall())
    team = [{"owner_id": i, "owner_name": n, "owner_type": t}
            for i, n, t in con.sql("SELECT owner_id, owner_name, owner_type FROM cs_team").fetchall()]
    safe, held_back = [], []
    for acc in dict.fromkeys(h["account_id"] for h in hits):        # each account once, in rank order
        folder = folders[acc]
        entry = documents.directory(folder)[acc]
        docs = documents.for_account(acc, folder, entry)            # all of them: people named anywhere
        clean, key = handover.cleaner(entry, team, "", docs, handover.contacts(acc, folder))
        text_of = {d["id"]: d["text"] for d in docs}
        mine = [{**h, "title": clean(h["title"]), "line": clean(h["line"]), "text": clean(text_of[h["id"]])}
                for h in hits if h["account_id"] == acc]
        found = handover.leak_check("\n".join(f"{h['title']}\n{h['line']}\n{h['text']}" for h in mine), key)
        if found:
            held_back.append({"account_id": acc, "found": found})
        else:
            safe += mine
    return safe, held_back


def _best_line(text, matched):
    """The line of the document holding most of the matched words: the context to show."""
    stems = set(matched)
    def hits(line):
        return sum(1 for w in re.findall(r"\w+", line.lower()) if any(w.startswith(s) for s in stems))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not re.match(r"^(From|To|Cc):", ln)]
    best = max(lines, key=hits, default="")
    return best if len(best) <= 200 else best[:197] + "..."


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Search tickets, emails and call notes")
    ap.add_argument("query")
    ap.add_argument("--account", help="search one account's documents only")
    ap.add_argument("-k", type=int, default=None, help="how many results")
    ap.add_argument("--exact", action="store_true", help="the question's own words only, no synonyms")
    ap.add_argument("--redacted", action="store_true", help="show the results as they'd leave the laptop")
    args = ap.parse_args()
    from cairnpath_db import connect
    con = connect()
    print(f"{add_views(con)} documents")
    hits = search(con, args.query, args.k, args.account, synonyms=not args.exact)
    if args.redacted:
        hits, held = redacted(con, hits)
        for b in held:
            print(f"Held back {b['account_id']}: the leak check found {', '.join(b['found'])}")
    for h in hits:
        print(f"{h['score']:6.2f}  {h['account_id']} [{h['id']}] {h['date']}  {h['title']}\n        {h['line']}")
