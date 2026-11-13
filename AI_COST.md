# AI cost: what we do, what we don't, and why

Every way we know to cut the cost of running a model over unstructured text
(emails, tickets, call notes), with a decision for each: **Done**, **Planned**
(planner Phase 9) or **Not now**, and the reason. A "Not now" says what would
change our mind, so the decision can be checked again later rather than forgotten.

Written 13 Nov 2026. Prices are Anthropic's list prices on that day, per million
tokens: Sonnet 5.5 $2 in / $10 out, Haiku 5.5 $0.10 in / $0.50 out (prompts up to
100k tokens). Our own numbers come from the AI spend ledger (`python llm.py --spend`).

## Where we start

A handover brief costs about 3 cents. Three stories, rewritten only when something
changed: a few briefs a month. So nothing below is urgent at demo scale. It matters
at 500 accounts, and it's the question every buyer asks second, after "is our data
safe?".

## The decisions

| # | Technique | Decision | Why |
|---|---|---|---|
| 1 | Call only when something changed | **Done** (5 Nov 2026) | The handover cache: a brief is rewritten only when its documents, a flag, a booked QBR, the owner or the contract changed. Most days nothing has, so most days cost nothing. This is usually the biggest saving of all. |
| 2 | Cap the output | **Done** | `[handover] max_output_tokens` = 1500. Output costs 5x input on Sonnet. A cut-off answer is never saved, so a cap that's too low shows up as a missing brief, not a broken one. |
| 3 | Clean the text first | **Planned** | Email is the worst offender: each reply quotes the thread below it. We remove quoted replies, signatures and legal footers with plain code, no model. One rule: a quoted block is dropped only when the message it quotes is in our documents on its own. Otherwise the quote may be the only copy, and we keep it. Tokens before and after go in the ledger. |
| 4 | Send only the relevant parts (RAG) | **Planned** | For questions ("what did they say about the competitor?"), not for the brief. A brief summarises everything, so there's no narrow question to search on. About $0.013 a question, against about $0.40 if a large account's whole history were sent. |
| 5 | Prompt caching | **Planned, where it pays** | The model can keep the start of a prompt for 5 minutes. On Sonnet 5.5, writing it costs 1.25x the input price and each read 0.05x, so it pays once the same start is read again within 5 minutes. **Not for the brief's instructions today:** they're about 250 tokens, under the 512-token minimum, so the API would quietly ignore the cache. **Yes for the Ask box:** the same account's chunks, several questions within minutes. |
| 6 | A cheaper model for the bulk work (routing) | **Planned, with a quality check** | Haiku 5.5 is 20x cheaper than Sonnet. It reads each document once and pulls out fields: dates, a competitor named, who spoke, the tone. Sonnet only writes the final answer. We'll check it against the answer key first: a cheap model that misses a fact costs more than it saves. |
| 7 | Extract once, then ask with SQL | **Planned**, the same step as 6 | The fields from 6 are stored as rows. "Which accounts mentioned a competitor this quarter?" then becomes a SQL query that costs nothing, and fits our rule that SQL computes every fact. |
| 8 | Batch processing | **Not now** | 50% off, but the results come back later (within 24 hours, often sooner). The engine Lambda stops at 15 minutes, so it would need a "send" step and a "collect" step on the next schedule: two more moving parts, and a brief up to a day late, to save 1 or 2 cents a run. **Revisit when** a run writes 20 or more briefs, or briefs run every night for the whole book. On Bedrock, check its batch limits first. |
| 9 | Summarise in layers | **Not now** | Summarise each quarter once, store it, and read only the summaries plus what's new. We have one year of generated data, not years of history. A stored summary that's wrong keeps being wrong, so each one needs a person to confirm it and a fresh read now and then (see "Handover brief, long histories" in CLAUDE.md's Planned next). **Revisit when** one account's documents pass about 50k tokens (about 10 cents to read whole), or with a real multi-year dataset. |

## How we'll show it

Each planned item is measured in the spend ledger, before and after, on the same
questions: tokens in, tokens out, cost per question. Planned items are in the
planner's Phase 9 ("AI cost demo").
