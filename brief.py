"""
QBR briefs: an LLM turns each facts pack into a one-page brief for the CSM.

The model only writes up what's in the facts pack (data/qbr_facts.json). It
gets no raw data and no real user IDs, and every number in its draft is
checked against the pack afterwards: anything it can't trace is listed at
the bottom of the brief as "unverified", so the CSM knows to check it.

The real user IDs are put back only at the end, from data/qbr_user_key.csv,
outside the model.

Needs an Anthropic API key in a git-ignored .env file in the repo root:
    ANTHROPIC_API_KEY=sk-ant-...

Run qbr.py first, then:
    python brief.py              # a brief for every QBR in the next 30 days
    python brief.py acc_037      # just one account (cheaper while testing)

Writes data/briefs/<account_id>.md and prints what it cost.
"""

import csv
import json
import os
import re
import sys
from pathlib import Path

import anthropic

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parent   # the repo root, wherever the terminal is

MODEL = "claude-sonnet-5-5"
PRICE_PER_MILLION = {"input": 2.00, "output": 10.00}   # USD, Claude Sonnet 5.5 list price
MAX_OUTPUT_TOKENS = 1500

SYSTEM_PROMPT = """You write one-page QBR preparation briefs for a Customer Success Manager.

Rules:
- Use only the facts in the JSON you are given. Never add numbers, dates,
  names or events that are not in it. If something isn't in the facts, don't
  guess: leave it out.
- Quote numbers exactly as they appear in the facts (e.g. 300000000, not
  "300 million"; 35.0% may be written as 35%). Don't calculate new numbers.
- Refer to users only by their alias (User A, User B, ...).
- Write for a busy CSM: plain language, short sentences, no filler.
- This brief is for the CSM to prepare and investigate. It is not sent to
  the customer.

Format (Markdown):
# <account name>: QBR on <date>
One-sentence headline: the single most important thing for this QBR.

## Snapshot
3-5 bullets: contract and renewal timing, utilisation trend, weekly active
users trend, and how long since the last QBR.

## What's going well
1-3 bullets, only if the facts support them. Omit the section if nothing does.

## Risks to discuss
Each open flag and any other warning sign in the facts (falling usage,
champion slowing down, ingestion errors), with its numbers.

## Opportunities
Underused add-ons and any growth signal. Omit the section if there are none.

## People
Who carries the account (champion or not) and what that means for the QBR.

## Suggested focus
Exactly one ask or goal for this QBR, in one or two sentences, tied to the
facts above.
"""


def load_api_key():
    """Read ANTHROPIC_API_KEY from .env (git-ignored) without printing it.

    The .env file is looked up next to this script, not in the folder the
    terminal happens to be in, so it works wherever you run it from.
    """
    env = Path(__file__).resolve().parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "ANTHROPIC_API_KEY" and value.strip():
                os.environ["ANTHROPIC_API_KEY"] = value.strip().strip('"').strip("'")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(f"No API key: looked for ANTHROPIC_API_KEY in {env} "
                 f"({'file found' if env.exists() else 'file not found'})")


def numbers_in(text):
    """Every number in a text, normalised (so 35.0 and 35 match, and the "-25"
    in a date like 2026-10-25 matches a plain 25)."""
    found = set()
    for n in re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")):
        value = float(n)
        found.add(str(int(value)) if value == int(value) else str(value))
    return found


def unverified_numbers(brief, pack):
    """Numbers in the brief that appear nowhere in the facts pack.

    A few harmless ones are expected (the "3" in "3 bullets" never appears,
    but small counts like 1-3 can come from list lengths), so very small
    numbers are skipped; everything else should trace back to the pack.
    """
    allowed = numbers_in(json.dumps(pack))
    return sorted((n for n in numbers_in(brief) - allowed if float(n) > 3), key=float)


def put_back_user_ids(text, account_id):
    """Replace aliases with real user IDs, outside the model."""
    with open(REPO / "data" / "qbr_user_key.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["account_id"] == account_id:
                text = text.replace(row["alias"], f"{row['alias']} ({row['user_id']})")
    return text


def main():
    load_api_key()
    packs = json.loads(REPO / "data" / "qbr_facts.json".read_text(encoding="utf-8"))
    if len(sys.argv) > 1:
        packs = [p for p in packs if p["account"]["account_id"] == sys.argv[1]]
        if not packs:
            sys.exit(f"No QBR facts pack for {sys.argv[1]}: run python qbr.py and check the ID")

    client = anthropic.Anthropic()
    out_dir = REPO / "data" / "briefs"
    out_dir.mkdir(parents=True, exist_ok=True)
    total_in = total_out = 0

    for pack in packs:
        account_id = pack["account"]["account_id"]
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user",
                       "content": "Facts pack:\n```json\n" + json.dumps(pack, indent=2, default=str) + "\n```"}],
        )
        draft = "".join(block.text for block in response.content if block.type == "text")
        total_in += response.usage.input_tokens
        total_out += response.usage.output_tokens

        # check the draft before the real IDs go back in (the pack only has aliases)
        unchecked = unverified_numbers(draft, pack)
        brief = put_back_user_ids(draft, account_id)
        if unchecked:
            brief += ("\n\n---\n**Unverified numbers** (not found in the facts pack, check before "
                      "using): " + ", ".join(unchecked) + "\n")
        (out_dir / f"{account_id}.md").write_text(brief, encoding="utf-8")
        flag = f"  ({len(unchecked)} unverified numbers)" if unchecked else ""
        print(f"  {account_id}  {pack['account']['name']:<24} -> data/briefs/{account_id}.md{flag}")

    cost = (total_in * PRICE_PER_MILLION["input"] + total_out * PRICE_PER_MILLION["output"]) / 1_000_000
    print(f"\n{len(packs)} briefs, {total_in:,} tokens in, {total_out:,} tokens out, about ${cost:.3f}")


if __name__ == "__main__":
    main()
