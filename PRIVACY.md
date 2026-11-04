# Privacy: how the AI briefs keep people's details away from the model

How the handover briefs (`handover.py`) pseudonymise people before an LLM sees
anything, and how real names come back afterwards: the method as it stands
today, how we prove it, where it's weak, and the upgrades that would close the
gaps. The QBR briefs (`brief.py`) use a simpler version of the same idea,
covered briefly at the end.

**The rule:** the model gets the facts, never the people. It sees labels
("Customer A", "CS 1", "the customer"); the mapping from labels to real names,
the *key*, stays with us, and real names go back in only after the model has
answered.

---

## 1. The method today, step by step

```
three silos ──> gather ──> identify people ──> label ──> clean text ──> leak check ──> model ──> check ──> restore names ──> brief
                                                  │          │                                  ▲
                                                  └── key ───┼──────── never sent ──────────────┘
                                                             └──> <account>_sent.txt (exactly what the model saw)
```

### 1.1 Gather (`gather()`)

For one account, three silos are read, each joined by its own key, the way a
real company's systems would be:

| Silo | File | Joined by |
| --- | --- | --- |
| Support tickets | `stories/support_cases.csv` | helpdesk organisation name |
| Emails | `stories/emails/*.eml` | the customer's email domain in From, To or Cc |
| Call transcripts | `stories/calls/*.txt` | the CRM account ID in the transcript header |

Plus the engine's own facts (`[DATA]`: usage, flags, CS touches, forecast),
from `data/views_data.json`. Each document gets a short ID, oldest first
(S1, S2 tickets; E1, E2 emails; C1 calls): what the model cites.

### 1.2 Identify people (`people_in()`)

Names are found by **position**, not by understanding the text:

| Where | Example | Side |
| --- | --- | --- |
| Email headers | `Jane Doe <jane@customer.example>` | customer if the address is on the customer's domain, else ours |
| Ticket agents | `(agent Tom Reid)` | ours |
| Call participant lists | `Jane Doe (Customer Co, Head of Data)` | ours if the organisation is Cairnpath, else customer |
| Transcript speaker lines | `[00:03:12] Jane Doe:` | customer |
| Our CS team (`cs_team.csv`) | any named CSM who appears in the text | ours |

A name is "First Last" (two or more capitalised words).

### 1.3 Label (`redact()`)

People are sorted (side, then name), so labels are the same on every run:
customer side **Customer A, B, C…**, our side **CS 1, 2, 3…**. The company
becomes **the customer**. The key is that mapping, e.g.
`{"Customer A": "Jane Doe", "CS 1": "Tom Reid", "the customer": "Customer Co"}`.

### 1.4 Clean the text (`clean()` inside `redact()`), in this order

1. Email addresses → `[email]`; international phone numbers (`+44 …`) →
   `[phone]`. Masked for good, not labelled: a brief never needs them.
2. Full names → their label, longest first ("Jane Doe" before "Jane").
3. First names on their own → the same label ("Jane said…").
4. The company name, its first word and the helpdesk organisation → "the
   customer".

The result is written to `data/handover/<account>_sent.txt`: exactly what the
model sees, so anyone can audit it. The cockpit shows it too ("What the model
saw").

### 1.5 Leak check (`leak_check()`), the last look before anything leaves

Added 4 Nov 2026 (upgrade C). Before the model call, the exact text about to
be sent is scanned for anything still shaped like a person:

- "First Last" (two or more capitalised words) that isn't a label, a role or
  title ("Head of Data", "Finance Business Partner"), a greeting, a day or a
  month, a product word, or on `settings.toml [handover] leak_check_allow`
  (known non-people that look like names, e.g. a customer's brand);
- any name redaction knew about (in the key) that is still in the text;
- any email address or phone number the masks missed.

Anything found **stops the model call for that account**: no brief is
written, the run lists what it found and says what to do (add a non-person to
the allow list, or fix the redaction), and `handover.py` ends with an error,
so run_all and the AWS run.json both show it. Nothing is sent until a person
has looked.

What it can't see: a first name on its own ("ask Hannah"), a name written in
lower case, a person named with one word. Upgrade B (a PII detector) is for
those.

### 1.6 The model writes (with labels only)

The prompt asks for a cited claim per sentence ([S1], [E2], [C1], [DATA]).
The model reasons about "Customer A", never about a real person.

### 1.7 Check (`check()`), on the redacted draft

Unknown citations, sentences without a source, numbers found in no source.
They're listed under "Unverified" at the bottom of the brief.

### 1.8 Restore (`restore()`)

Each label is replaced by its real value, longest label first, so
"Customer AB" is never read as "Customer A" and "CS 10" never as "CS 1". The
brief the new owner reads has real names; the model never had them.

### 1.9 Where each piece lives

| Piece | Laptop | AWS (engine Lambda) |
| --- | --- | --- |
| `_sent.txt` (what the model saw) | `data/handover/` | uploaded to `outputs/handover/` |
| `_key.json` (label → real name) | `data/handover/`, git-ignored | **never uploaded**: exists only in the Lambda's `/tmp` during the run |
| the brief (`.md`, real names) | `data/handover/` | `outputs/handover/` and the cockpit |
| `stories/answer_key.json` (test only) | repo | **never bundled** into the Lambda |
| the API key | `.env`, git-ignored | Parameter Store SecureString, read at run time by the briefs step only |

The bucket is private, encrypted and HTTPS only (see `infra/`).

---

## 2. How we prove it today

- `tests/handover_test.py`, part 1 (privacy, no model needed): for every
  account, no name from the key (full or first), no email address and no
  international phone number appears in `_sent.txt`.
- Same test, part 2 (quality): planted facts found, no forbidden claims,
  citations real (`stories/answer_key.json`).
- `tests/engine_lambda_test.py`: in AWS the redaction key never leaves the
  Lambda, the answer key is never bundled, the API key appears in no log.
- On 4 Nov 2026 the briefs written in AWS passed both parts, and the redacted
  text rebuilt on the laptop was byte-for-byte what AWS had sent the model.

**What the privacy test can't see:** it checks the names in the *key*, i.e.
the names step 1.2 found. A person step 1.2 missed is in neither the key nor
the test. The test proves "everyone we found is hidden", not "everyone is
found". That's gap 1 below.

- `tests/leak_check_test.py` (since 4 Nov 2026, upgrade C) covers that blind
  spot from the other side: it plants a person named only in an email body
  (gap 1), proves the redaction really misses her, and checks that the leak
  check stops the model call and names her. Also: the three stories as they
  are pass with no false alarm; a missed email, phone number or known name is
  caught; roles and greetings aren't people; a brand that looks like a name is
  stopped until it's on the allow list.

---

## 3. Known gaps

| # | Gap | Example | Risk |
| --- | --- | --- | --- |
| 1 | Names outside the known positions aren't found | an email body says "loop in Hannah Weiss from procurement", and she never appears in a header, participant list or speaker line | since the leak check (C): **the call is stopped** and a person looks; still unredacted, so the brief waits. A first name alone ("ask Hannah") still gets through |
| 2 | First names are replaced as whole words everywhere | a person called "Will" or "May" turns ordinary words into labels | garbled text, possibly a wrong statement |
| 3 | Two people with the same first name collide | every bare "Anna" becomes the first Anna's label | the model attributes something to the wrong person |
| 4 | Only international phone numbers are caught | `01632 960123` stays | a phone number reaches the model |
| 5 | Restore is exact and case-sensitive | "The customer" at the start of a sentence stays generic | low: nothing wrong shown, just less specific |
| 6 | Labels are per brief | "Customer A" means different people in two briefs | none today; matters if briefs are ever combined |
| 7 | Other identifiers aren't touched | job titles, street addresses, contract or invoice numbers, IBANs | a rare title ("our only CFO") can identify a person |

Today's three stories have none of gaps 1, 4 or 7 (checked by hand on 4 Nov
2026), which is why the tests pass. Real data will have them.

---

## 4. Upgrades, in order of value

| | Upgrade | Closes | How | Proof it works | Cost |
| --- | --- | --- | --- | --- | --- |
| A | **Contact list as the dictionary** | 1 (mostly), 3 | Add an optional `contacts.csv` input (account_id, name, email, side), the CRM's contact export, to the data contract. Every known person is found wherever they appear, not only in headers. Same-first-name people get first-name replacement only where unambiguous (else full names only). | A story with a contact named only in an email body: the privacy test must find and hide her. | none (an export the CRM already has) |
| B | **A PII detector as a second pass** | 1 (the rest), 4, 7 | After the rules, run each cleaned document through Amazon Comprehend `DetectPiiEntities` (or open-source Microsoft Presidio, on the laptop) and label anything it finds as a person, phone, address or account number. | A planted unknown name, a local phone number and an IBAN in a story: all masked. | Comprehend: fractions of a cent per brief |
| C ✅ | **A leak check on what the model would see** (done 4 Nov 2026, §1.5) | makes 1 visible | Scan `_sent.txt` for anything person-shaped (the detector from B, or "First Last" not in the key and not a known word). Anything found blocks the model call and is listed for a human. | The test from A, run *without* A: the check must stop the call. | none |
| D | **Safer first names** | 2, 3 | Replace a first name alone only when it's unique in the document set and not a common word (a short stop list). | Stories with "Will" and two Annas. | none |
| E | **Local phone formats** | 4 | Extend the phone pattern (national formats, or rely on B). | A local number in a story. | none |
| F | **Case-insensitive restore** | 5 | Restore "The customer" too, keeping the capital. | Unit test on restore. | none |
| G | **Stable pseudonyms across briefs** | 6 | A per-account token kept in a vault (e.g. DynamoDB), so the same person keeps the same label everywhere. | Two briefs naming the same person: same label. | only if briefs are ever combined |

Suggested order: **C first** (done: a silent leak is now a stopped call),
then **A** (biggest gain, no new service), then **B** (catches what nobody
listed). D, E and F are small fixes to do alongside. G only when needed.

---

## 5. The QBR briefs (`brief.py`), for completeness

The QBR facts packs (`qbr.py`) hold numbers, not documents. The only personal
data is user IDs, replaced by "User A/B/C" in `data/qbr_facts.json`; the key
is `data/qbr_user_key.csv` (never sent), and `brief.py` puts the real IDs
back after the model answers. No free text, so gaps 1 to 7 don't apply.

---

*Update this file whenever redaction, the inputs it reads or where its
outputs live changes.*
