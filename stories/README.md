# Account stories: unstructured context from three systems

Fictional companies and people, written for the demo. Three accounts that
change owner, each with the raw material a new owner would have to dig
through, exported from three separate systems, the way they arrive in real life:

| System | Folder | Format | Joined to the account by |
| --- | --- | --- | --- |
| Helpdesk | `support_cases.csv` | a ticket export (one row per case) | the helpdesk organisation name |
| Email | `emails/*.eml` | real email files (headers, signatures, quoted replies) | the sender's or recipient's email domain |
| Call recorder | `calls/*.txt` | transcripts with speakers and timestamps | the CRM account ID in the header |

`directory.csv` holds the join keys (account ID, company name, email domain,
helpdesk organisation). `answer_key.json` lists, per story, the facts a good
handover brief must surface and the things it must not claim: it is how
`tests/handover_test.py` grades a brief, like the answer key grades the flags.

| Account | Owner change | The story |
| --- | --- | --- |
| acc_024 Yarrow Studio (Enterprise) | Mia Hofer to Lukas Brandt | Usage falling, budget cut, champion leaving, a price promise in writing, and an open iOS tracking bug that explains part of the drop |
| acc_012 Nettle Foods (Enterprise) | Tom Brennan to Anna Whitfield | Growing fast; a new sponsor pushes a competitor review, finance disputes an overage invoice, SSO blocks the wider rollout |
| acc_156 Driftwood Labs (Scale) | Priya Shah to Owen Clarke | Thin context: rollout stuck, a hire fell through, renewal in under a month. The brief must say what isn't known |

Every name, email address and phone number here is invented (`.example`
domains, 555 numbers). They are still treated as personal data: `handover.py`
replaces them before anything is sent to a model.
