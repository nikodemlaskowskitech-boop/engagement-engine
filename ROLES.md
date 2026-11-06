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

## The matrix

| | Leader | Manager | CSM | Pool member |
| --- | --- | --- | --- | --- |
| Accounts on the page | Whole book | Their team's | Their team's | Their team's |
| Self-service accounts | Yes | Those in their team's regions | No | No |
| ARR, ARR at risk, upside | Yes | Yes | Yes | Yes |
| Colleagues' renewal forecasts | Yes | Yes | Yes, read-only | Yes, read-only |
| Set a forecast or next step | No | Team's accounts | Own accounts | Own accounts |
| Reassign a task, approve a hand-back | No | Team's tasks | No | No |
| Confirm a handover brief | No | Team's accounts | Own accounts (outgoing owner) | Own accounts |
| "Did it work?" | By owner | By owner, their team | Team total, no names | Team total, no names |
| Leadership tab | Yes (opens here) | Yes | No | No |
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
- **No Leadership tab below manager** (confirmed 6 Nov 2026). It compares owners
  against the median: a management view.
- **Self-service only for managers and the leader** (confirmed 6 Nov 2026). Those
  accounts have no owner; they go to the Self-service report, not to a person.
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
  calls by owner).
- A real customer user ID (true of every page already).

## Where to change it

| To change | Edit |
| --- | --- |
| What a role sees or opens on | `settings.toml` `[roles.<role>] tabs` (first = opens on) |
| Whole book vs. team | `[roles.<role>] scope` = `"book"` or `"team"` |
| Self-service on the page | `[roles.<role>] self_service` |
| What a role may change | `[roles.<role>] edit` = `{ forecast = "own" \| "team", ... }`; a missing entry = can't |
| Outcomes by owner or team total | `[roles.<role>] outcomes` = `"by_owner"` or `"team_total"` |
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
