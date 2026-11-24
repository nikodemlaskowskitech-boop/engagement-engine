# Demo walkthrough (about 3½ minutes)

A script to say out loud, with the clicks in brackets. The numbers are from the
demo data as of 18 Oct 2026, two years of history (the pages show the last year); after a regenerate, check them on the pages first.

**Before you start:** open the cockpit on the leader's page, either the public
site (https://cairnpath.nikodem-laskowski.com, no login, saves stay in your
browser) or the published claude.ai cockpit. Clear any pins ("Whole book") and
use a full-width window. To show saving on the public site, switch to Mia or
Sophie first: the leader's page has no forms.

## 1. The question (15 s)

"When I led CS teams, leadership asked me two things: what's red and what are
we doing about it, and is the team in front of customers. The CRM couldn't
answer either, because it was built for sales. This is the tool I wished I'd
had."

## 2. The leader's view (60 s)

[Leadership tab, top: Performance]

"The page opens the way a CFO asks: are we keeping the revenue we have? GRR
88.6%, up from 87% the year before; NRR 103%. One point of GRR is worth €147k
here, so that's the yardstick for any budget request. Renewals at risk in the next two
quarters: €1M, about 7 points. Accounts we reached in time (7 days for
Enterprise, 14 for Scale) kept 60% of their ARR at renewal, against 43%: a
gap, but on only 14 against 15 accounts. That's why the page shows the method
and never claims we saved anything; a CFO trusts that more."

[Optional: By team, under the tiles]

"Is it the same everywhere? James's team kept 88.8%, Sophie's 90.6%: close.
One click on a team and every number on the page is that team's."

[Scroll: Exposure and the team]

"Then what's at stake now. €1.5M of the €12.3M Enterprise and Scale ARR is at
risk (€325k of it has no owner yet; the whole book is €14.7M), across 12
accounts at Act. 9 of those 12 have had a call (a churn call, or a QBR held in
the 30 days after the signal: the same rule on every tab), but only 3 have a
next step planned. That gap is the conversation I'd have with my managers this
week. Everything is sorted by value, and value guides the work. It's never
anyone's credit. And it moves both ways: since July 12 accounts got worse, and
12 got better, mostly Shelfware accounts that finally rolled out."

[Optional, 20 s: Slack. On Operational, click "Slack card" under a task]

"Nobody has to open a dashboard to get their work. Each task reaches its owner
as a card in Slack: why, what to do, and three buttons. [Click Log call, add a
promise and a date] That click becomes an event the engine reads on its next
run: the call shows on the account's timeline tagged via Slack, and the promise
is tracked until it's kept."

[Optional, 20 s: Operational tab]

"And is the work moving? Of 60 tasks that needed a person, 48 (80%) ended in
the right meeting. The biggest drop is the first one: 9 sit in a pool queue
with nobody named. Per team, James's team is at 67% against Sophie's 93%, so
that's the team lead I'd talk to: as director I act through them. Coverage
in value shows where: Shelfware on Scale, €291k of ARR with no meeting in 60
days, 2 of 9 accounts met. And what we promised customers: 20 promises
open, 13 of them late, the oldest by two months. That's trust leaking, and
no CRM report shows it."

## 3. Did it work? (35 s)

[Did it work? tab]

"Flags are only worth something if they predict. Against what happened at
renewal, the churn flags caught 32 of the 50 accounts we lost or that shrank,
64%, with a median of 187 days' warning. The outcomes are generated, so this
shows the method, not proof. On your own history, it's the first thing I'd
run. The top of the page turns that into decisions: each rule against its bar,
keep or change. Today all three keep. Under it, why they left: a move to a
competitor took the most, and the flags saw every one coming; none of the 8
acquisitions. That's not a threshold to tune, it needs a different signal."

## 4. One account (40 s)

[Leadership: click Yarrow Studio]

"Yarrow Studio, Mia's account. Utilisation went from 47% to 37% to 26%, and
weekly users are down 65%. Both signals, so it's Act and the whole €234k ARR is
at risk. Mia has forecast a downsize to €134k, and the cockpit flags that gap
for the forecast call. There's also an AI handover brief, built from support
cases, emails and calls. Names are removed before the model sees anything, and
every claim is cited."

## 5. A page per person (30 s)

[Viewing as → Mia]

"Same account, now as Mia sees it. Her page holds her team's book and nothing
else. She can update the forecast on her own account. On a teammate's account
she can read it but not change it."

## 6. The boundary (20 s)

[Viewing as → James]

"James runs another team. Yarrow Studio isn't on his page. It isn't hidden: it
was never put in the file. Every page is built that way, and a test checks it."

## 7. Close (15 s)

"It runs on a laptop or in AWS, every security control has a test, and the
whole operating model (tiers, routing, who sees what) lives in one settings
file. Change the settings, rerun, and compare."

## Variations

- **Technical or TAM audience:** swap step 3 for 30 seconds on the threat model
  (THREAT_MODEL.md): every way in, its control, and the test that proves it.
- **More time:** pin three accounts on Sophie's page, then switch to Lukas
  (they travel) and to James (they're left behind, and the note says so).
