# Roles: who sees what, who changes what

The cockpit is one page per person. Each page holds only what that person's role
may see. Nothing outside their scope is in the page at all: it isn't hidden by a
filter, it was never exported. The matrix lives in `settings.toml` under
`[roles]`; this page explains each choice and how to change it.

## The rule

**The boundary is the team.** A team is everyone under the same manager (the
`manager` column of `cs_team.csv`). Inside the team, people see everything. The
limits are on what they can *do*, not on what they can *see* (decided 6 Nov 2026).

In the demo data:

| Team | Manager | People | Pools |
| --- | --- | --- | --- |
| UK&I and Nordics | James (`mgr_james`) | Anna, Tom, Erik; pool members Priya, Owen, Hannah, Sofie | UK&I, Nordics |
| DACH and France & Benelux | Sophie (`mgr_sophie`) | Lukas, Mia, Claire; pool members Jonas, Lena, Camille, Bram | DACH, F&B |

A team's accounts are the accounts owned by anyone in it: the CSM (`csm_owner`),
the pool (`cs_pool`) or the named pool member (`pool_owner`). An account covered
from another region belongs to its owner's team, not to its customer's region.

Only today's book counts: an account the CRM marks `status = churned` left at its
renewal and is on no page, not even the leader's (decided 12 Nov 2026, with the
12-month demo data). Its history stays in the engine's trends, not in anyone's book:
the weekly history counts it under a key of its own (`ob_1`, ...) with only its
tier, region, owner and manager, never its id or name, so a page's trend includes
it exactly when the same rule above would have put the account on that page. An
account with no CRM row (a handoff gap) has no team, so only the leader's trends
count it.

## The matrix

| | Leader | Manager | CSM | Pool member |
| --- | --- | --- | --- | --- |
| Accounts on the page | Whole book | Their team's | Their team's | Their team's |
| Accounts with no owner (Self-service, or Enterprise with no CSM yet) | Yes | Those in their team's regions | No | No |
| ARR, ARR at risk, upside | Yes | Yes | Yes | Yes |
| Colleagues' renewal forecasts | Yes | Yes | Yes, read-only | Yes, read-only |
| Set a forecast or next step | No | Team's accounts | Own accounts | Own accounts |
| Reassign a task, approve a hand-back | No | Team's tasks | No | No |
| Confirm a handover brief | No | Team's accounts | Own accounts (outgoing owner) | Own accounts |
| "Did it work?" | By owner | By owner, their team | Team total, no names | Team total, no names |
| Leadership tab | Yes (opens here) | Yes | No | No |
| Business case tab (GRR, NRR, the forecast) | Yes | No | No | No |
| Opens on | Leadership | Tasks | My book | My book |

Every change is checked twice: the page offers only what the role allows, and
`cockpit_sync.py` checks each saved decision again on our side (never trust the
page).

## Why each choice

- **Visibility inside the team, limits on actions** (6 Nov 2026). Cover,
  handovers and peer help need the whole team's picture. What goes wrong in
  practice is someone changing what isn't theirs, so that is what's limited.
- **ARR for everyone** (6 Nov 2026). The work is driven by value; a CSM who
  can't see what's at stake can't prioritise.
- **Colleagues' forecasts, read-only** (6 Nov 2026). Transparency inside the
  team. The cost, anchoring (seeing a colleague's call pulls yours towards it),
  is accepted; forecasts already sit next to the data's own number.
- **The leader is read-only** (6 Nov 2026). A leader doesn't override someone
  else's forecast; they ask the owner or the manager.
- **Outcomes by owner only for managers and the leader** (6 Nov 2026). Where
  account assignment drives commission, per-owner results next to colleagues'
  invite comparison on luck of the draw. Below manager level, the team total.
- **The business case is the leader's** (12 Nov 2026, from a senior CS leader's
  feedback: tie the work to GRR and NRR). It's the page for the CFO and the board,
  on the whole book; Leadership stays the operational page. Its data (the whole
  book's cohort and renewals) is only on pages with its tab.
- **No Leadership tab below manager** (confirmed 6 Nov 2026). It compares owners
  against the median: a management view.
- **Self-service only for managers and the leader** (confirmed 6 Nov 2026). Those
  accounts have no owner; they go to the Self-service report, not to a person.
  The same for an Enterprise account with no CSM yet (6 Nov 2026, from the code
  review): its "No CSM assigned" alert is the manager's to act on, so it sits on
  the managers' pages for its region; nobody can forecast it until it has an owner.
- **Small groups show no sums** (6 Nov 2026, from the code review). Below manager
  level "Did it work?" is team totals, but a total over one or two accounts is
  someone's own result. Groups smaller than `[roles] min_group` (5) show their
  count only, and then the "All renewals" sums go too (total minus the other rows
  would give the hidden one back); in the two-row calls table, both go. The
  matrix and warning counts stay: counts, no money, no names.
- **Call speed by owner is for managers** (6 Nov 2026, from the code review).
  Below manager level the CS team tab shows the team's accounts, not an
  owner-by-owner table; My book shows your own number next to a median.
- **Medians from the whole book** (6 Nov 2026, found in the acceptance check). A
  page holds only its team, so "against the median" on it compared people with
  their team alone (Mia's "median for CSMs" read 0.5 on her page, 1 on the full
  cockpit). roles.py now works the medians out once on the whole export and puts
  only the numbers on each page. `[roles] peer_median = "team"` switches to the
  team's own medians; the leader always gets the book's.
- **The open account goes with a switch** (6 Nov 2026, from the acceptance
  check). Switching person while an account is open on the timeline opens it on
  the new page if it's there; if not, a note says it isn't on that person's page
  and the page opens as usual. Only the id travels, so the boundary shows without
  anything crossing it. Every switch starts the new page on its own defaults,
  never a filter saved earlier.
- **Pinned accounts** (6 Nov 2026, Nikodem's idea). The Find box in the segment
  bar pins an account as a chip; pin several, and every tab shows exactly the
  pinned ones, whatever the other filters say, until they're unpinned (x) or
  "Whole book". They stay across tabs and visits, and travel with a switch: the
  ones on the new page are kept, the rest left behind and counted in a note (ids
  only, never a name).
- **A small book in full sight** (6 Nov 2026, same check). My book folds accounts
  with no flags away, risk first; in a book of `[cockpit] open_no_flags_up_to`
  (15) or fewer they're listed open, so a CSM sees all of theirs at a glance.
- **Landing on the leader's page, a dropdown to the others** (5 Nov 2026). For
  the demo only, so a visitor can switch views. In real use each person gets
  their own page's link, or signs in (SSO) and is sent to it; the dropdown and
  the other pages are not theirs to open. See THREAT_MODEL.md.

## What a page must never contain

A test checks the raw HTML of each page, not what the screen shows, so a hidden
element still counts.

- An account ID or name outside the person's scope.
- A forecast, next step, task or handover brief on such an account.
- Below manager level: any per-owner outcome (caught / missed, warning time,
  calls by owner), or a renewal outcome row per account (totals only, and no
  sums for groups under `[roles] min_group`).
- Once published: a decision (forecast, next step, handover confirm) on an
  account outside the page's scope, read from the shared database. The page
  reads only its own accounts' decisions; a real deployment keeps one database
  per team (see THREAT_MODEL.md).
- A real customer user ID (true of every page already).

## Where to change it

| To change | Edit |
| --- | --- |
| What a role sees or opens on | `settings.toml` `[roles.<role>] tabs` (first = opens on) |
| Whole book vs. team | `[roles.<role>] scope` = `"book"` or `"team"` |
| Self-service on the page | `[roles.<role>] self_service` |
| What a role may change | `[roles.<role>] edit` = `{ forecast = "own" \| "team", ... }`; a missing entry = can't |
| Outcomes by owner or team total | `[roles.<role>] outcomes` = `"by_owner"` or `"team_total"` |
| Medians: the whole book's or the team's | `[roles] peer_median` = `"book"` or `"team"` |
| Who is in which team | `cs_team.csv` `manager` column (your own data: `[columns.team]`) |
| The leader's name | `[roles] leader_name` |

A different operating model, for example regions as the boundary instead of
managers, is a change to how a team is worked out, not to the matrix.

## Build contract (what the tests may rely on)

The pages are tested as a black box, from this page and the inputs only.

- `python export_views.py` writes one page per person to `data/pages/`:
  `<owner_id>.html` for every manager, CSM and pool member in `cs_team.csv`
  (pools are queues, not people: no page), `leader.html` for the leader
  (`[roles] leader_id`), and `index.html`, a copy of the landing page.
- Managers are the ids in the `manager` column (they have no row of their own);
  their name is their id without `mgr_`, capitalised (`mgr_sophie` = Sophie).
- Every page carries one `<script type="application/json" id="page-meta">` with:
  `person` (id), `role` (leader | manager | csm | member), `tabs` (in order,
  the first is where it opens), `accounts` (every account id on the page),
  `edit` (for each action in the role's `edit`, the account ids it is allowed
  on), `outcomes` (by_owner | team_total).
- Every page has a page switcher (`<select id="page-switch">`) whose options
  link to the other pages' file names. In the demo it lists every page.
- Every page carries its data in `<script type="application/json" id="views-data">`.
  On a team-total page its `renewals` list is empty and `outcomes_total` holds
  the team's totals; any group there (in `value` or `calls`) with `n` under
  `[roles] min_group` has `small: true` and no sums (`arr`, `at_risk`, `lost`,
  `churned`, `kept` are null); so does the "All renewals" total in `value` when
  any group is small, and every `calls` row when either is.
- Account ids look like `acc_123`. Any `acc_` id in a page's raw HTML,
  anywhere, counts as being on the page.
- `cockpit_sync.py` refuses a decision the role may not make, by the person in
  `by` (or `manager` for an assignment), on top of its existing checks.

## How it's built and tested (the independent way)

1. This spec.
2. A separate agent, who hasn't seen the code, writes the acceptance tests
   from this page only.
3. The pages are built until those tests pass.
4. Another separate agent reviews the code.
5. Nikodem's acceptance check.

Built 6 Nov 2026: `roles.py` cuts the export per person (export_views.py calls it);
`tests/roles_test.py` (the separate agent's, from this page only) and
`tests/role_pages_test.js` (the pages in a browser) check it. The tests found one
leak before anything shipped: an example account id in a code comment of the
template, on every page of the other team.
