"""
Search by meaning (Phase 9, 22 Nov 2026): one vector per document, next to the keywords.

An embedding model turns a text into a list of numbers (a vector) so that texts saying
the same thing in different words end up close together: "we'll hit the limit in six
weeks" lands near "which customers need more capacity?" though they share no word.
search.py ranks by those vectors next to BM25 (hybrid search); this script makes them.

What it reads: the redacted documents only (search_export.py's committed file), the
same text the public search and research.py see. Nothing with a real name, email or
company is ever sent to the model (PRIVACY.md §13).

Where the model runs: [embed] provider.
  bedrock    Amazon Titan Text Embeddings V2 in our own AWS account ([aws] region and
             profile, `aws login --profile cairnpath` first): no API key, IAM decides.
  stand-in   a made-up vector from the document's own words, no AWS: for the tests,
             which check the plumbing, never the quality.

The vectors go to data/<[outputs] vectors> (git-ignored), with each document's text hash:
a run embeds only the documents that are new or changed (the rest are reused), so the
model is paid once per document. Every call is checked against the [llm] budget for
"embed" first and written to the AI spend ledger (llm.py), like every other model call.

    python embed.py            make or refresh the vectors (Bedrock: sign in first)
    python embed.py --check    say whether they're up to date, call nothing
"""
import hashlib
import json
import math
import re
import sys
import time

import llm
import search_export
from settings import SETTINGS, output_file

CONF = SETTINGS["embed"]
VECTORS = output_file("vectors")
FEATURE = "embed"                 # its line in the AI spend ledger and its budget in [llm]


def text_of(doc):
    """What gets embedded: the title and the text, cut to [embed] max_chars."""
    return f"{doc['title']}\n{doc['text']}"[: int(CONF["max_chars"])]


def digest(doc):
    return hashlib.sha256(text_of(doc).encode("utf-8")).hexdigest()[:16]


class StandIn:
    """No model: each word adds 1 to one of the vector's slots (picked by a hash of the
    word), then the vector is scaled to length 1. Two texts sharing words land close,
    nothing more: enough to test the plumbing, never a measure of meaning."""
    name = "stand-in"

    def __init__(self, dims):
        self.dims = dims

    def __call__(self, text):
        v = [0.0] * self.dims
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dims] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v], len(text) // 5


class Titan:
    """Amazon Titan Text Embeddings V2 on Bedrock, in our account and region."""
    name = "bedrock"

    def __init__(self, dims):
        import boto3                                  # only here: the stand-in needs no AWS
        aws = SETTINGS["aws"]
        session = boto3.Session(profile_name=aws.get("profile") or None, region_name=aws["region"])
        self.client, self.dims = session.client("bedrock-runtime"), dims

    def __call__(self, text):
        body = json.dumps({"inputText": text, "dimensions": self.dims, "normalize": True})
        r = json.loads(self.client.invoke_model(modelId=CONF["model"], body=body)["body"].read())
        return r["embedding"], int(r.get("inputTextTokenCount", 0))


def embedder(provider=None):
    provider = provider or CONF["provider"]
    dims = int(CONF["dimensions"])
    if provider == "stand-in":
        return StandIn(dims)
    if provider == "bedrock":
        return Titan(dims)
    raise ValueError(f"[embed] provider must be bedrock or stand-in, not {provider!r}")


def model_name(e):
    return CONF["model"] if e.name == "bedrock" else "stand-in"


def embed_one(e, text, account_id=""):
    """One model call: the budget first, then the call, then the ledger (llm.py)."""
    llm.check_budget(FEATURE)
    started = time.perf_counter()
    vector, tokens = e(text)
    if e.name == "bedrock":                       # the stand-in costs nothing and isn't a model call
        llm.record_usage(FEATURE, account_id, "bedrock", CONF["model"], tokens, 0,
                         tokens * float(CONF["price_per_million"]) / 1e6, time.perf_counter() - started)
    return vector


def load(path=VECTORS):
    """The vectors file: {(account_id, id): row}. Rows carry model, dims, hash, vector."""
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {(r["account_id"], r["id"]): r for r in rows}


def documents():
    """The redacted documents, from the committed file (search_export.py)."""
    return [json.loads(line) for line in search_export.OUT.read_text(encoding="utf-8").splitlines() if line.strip()]


def plan(docs, old, model, dims):
    """Which documents need a vector: new, changed text, or another model or size."""
    todo, keep = [], []
    for d in docs:
        r = old.get((d["account_id"], d["id"]))
        same = r and r["hash"] == digest(d) and r["model"] == model and int(r["dims"]) == dims
        (keep if same else todo).append(d)
    return todo, keep


def build(provider=None, path=VECTORS, docs=None):
    """Make or refresh the vectors. Returns (embedded, reused)."""
    e = embedder(provider)
    model, dims = model_name(e), int(CONF["dimensions"])
    docs = documents() if docs is None else docs
    old = load(path)
    todo, keep = plan(docs, old, model, dims)
    rows = {(d["account_id"], d["id"]): old[(d["account_id"], d["id"])] for d in keep}
    for n, d in enumerate(todo, 1):
        rows[(d["account_id"], d["id"])] = {"account_id": d["account_id"], "id": d["id"], "model": model,
                                            "dims": dims, "hash": digest(d),
                                            "vector": [round(x, 6) for x in embed_one(e, text_of(d), d["account_id"])]}
        if n % 50 == 0:
            print(f"  {n} of {len(todo)} embedded")
    path.parent.mkdir(parents=True, exist_ok=True)
    # one line per document, in a stable order; a document gone from the file is dropped
    path.write_text("".join(json.dumps(rows[k], separators=(",", ":")) + "\n" for k in sorted(rows)), encoding="utf-8")
    return len(todo), len(keep)


def embed_query(text, provider=None):
    """The question's vector, the same model as the documents' (one call)."""
    return embed_one(embedder(provider), str(text)[: int(CONF["max_chars"])], "query")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--check" in sys.argv:
        e_name = CONF["provider"]
        model = CONF["model"] if e_name == "bedrock" else "stand-in"
        todo, keep = plan(documents(), load(), model, int(CONF["dimensions"]))
        print(f"{VECTORS.name}: {len(keep)} up to date, {len(todo)} to embed" + ("" if todo else " (nothing to do)"))
        sys.exit(1 if todo else 0)
    done, reused = build()
    print(f"{done} documents embedded, {reused} reused -> {VECTORS}")
    if done and CONF["provider"] == "bedrock":
        print(llm.spend_report())
