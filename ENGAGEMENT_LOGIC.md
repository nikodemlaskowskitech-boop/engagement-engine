# Engagement logic by tier

**The automation spots the moment; the right person acts on it.** The same
signal is handled three ways, depending on how much human coverage the account
has, and every step has a time limit so nothing waits for ever.

The diagrams follow two conventions from customer success tooling: one lane per
coverage tier (tech touch, pooled, named CSM), and journey flows where a timed
check ("30 days: is the flag still there?") decides the next step, as in
Gainsight's Journey Orchestrator programs. Hexagons are those timed checks;
diamonds are plain yes/no decisions.

Colours: green = automated, purple = a CS person acts, amber = a manager
decides, grey = end state.

## 1. Who covers what

| Tier | Touch model | Coverage | Who acts on a flag | Where it lands | Safety net |
| --- | --- | --- | --- | --- | --- |
| Self-service (300) | Tech touch | No CSM | Automation | Simulated email (#email-preview) | One follow-up, then the Self-service report |
| Scale (150) | Pooled | One team per region | Whoever claims it | Shared queue (Slack cards) | Manager sees unclaimed tasks; approves hand-backs |
| Enterprise (48) | High touch | Named CSM, 8 accounts each | The account's CSM | The CSM's own task | QBR first; manager escalation |

## 2. Overview

```mermaid
flowchart TB
    sig(["Usage signal<br/>flags.py"]) --> tier{"Tier?"}

    tier -->|Self-service| ss1
    tier -->|Scale| sc1
    tier -->|Enterprise| en1

    subgraph SS["Tech touch: Self-service"]
        direction TB
        ss1["Automated email<br/>max 1 per 14 days"] --> ss2["One follow-up<br/>after 30 days, new angle"] --> ss3(["Stop<br/>Self-service report"])
    end

    subgraph SC["Pooled: Scale"]
        direction TB
        sc1["Card in the shared queue"] --> sc2["Claimed and worked<br/>by one person"] --> sc3["Follow-up goes back<br/>to that same person"]
    end

    subgraph EN["High touch: Enterprise"]
        direction TB
        en1{"QBR in the<br/>next 30 days?"} -->|no| en2["Task for the named CSM"]
        en1 -->|yes| en3["Folded into the QBR<br/>and its brief"]
    end

    subgraph MG["Manager layer"]
        direction LR
        m1["Approves hand-back<br/>follow-up not picked up in 5 days"]
        m2["Escalation<br/>no meeting in 30 days"]
        m3["Sees CS ops items<br/>handoff gaps, no CSM"]
    end

    sc3 -.-> m1
    sc2 -.-> m2
    en2 -.-> m2

    classDef auto fill:#E1F5EE,stroke:#0F6E56,color:#04342C
    classDef human fill:#EEEDFE,stroke:#534AB7,color:#26215C
    classDef mgr fill:#FAEEDA,stroke:#854F0B,color:#412402
    classDef fin fill:#F1EFE8,stroke:#5F5E5A,color:#2C2C2A
    class ss1,ss2 auto
    class sc1,sc2,sc3,en1,en2,en3 human
    class m1,m2,m3 mgr
    class ss3,sig,tier fin
```

## 3. Self-service: tech touch, one follow-up, then stop

Automated, so the guard rails are about the customer's inbox: the onboarding
control group never gets the programme, no account gets more than one
automated email in 14 days, and each issue gets two touches at most.

```mermaid
flowchart TB
    f["Flag: Stalled, Silent<br/>or Shelfware"] --> h{"Onboarding<br/>holdout?"}
    h -->|yes| ho(["Held out<br/>logged, never sent"])
    h -->|no| cap{"Emailed in the<br/>last 14 days?"}
    cap -->|yes| later["Waits<br/>goes out on a later run"]
    later -.-> cap
    cap -->|no| e1["Email 1<br/>e.g. use-case guides"]
    e1 --> w{{"30 days:<br/>flag still there?"}}
    w -->|no| res(["Resolved"])
    w -->|yes| e2["Email 2, new angle<br/>e.g. customer story"]
    e2 --> stop(["Stop<br/>Self-service report"])

    other["Churn or expansion<br/>on Self-service"] --> rep(["Self-service report<br/>no email: analyse across accounts"])

    classDef auto fill:#E1F5EE,stroke:#0F6E56,color:#04342C
    classDef fin fill:#F1EFE8,stroke:#5F5E5A,color:#2C2C2A
    class e1,e2,later auto
    class ho,res,stop,rep fin
```

## 4. Scale: a shared queue, with ownership that sticks

Anyone in the regional pool can pick a task up, which spreads the load and
avoids peaks and troughs. Once someone has taken it, the follow-up comes back
to them, so the customer hears from the same person. If that person can't pick
it up (holiday, left, swamped), the manager decides: a person, not a timer.

```mermaid
flowchart TB
    f["Flag on a<br/>Scale account"] --> q["Card in the<br/>shared queue"]
    q --> c{"Claimed?"}
    c -->|"I'll take it"| own["One owner<br/>works it"]
    own --> call["Logs the call<br/>in Slack"] --> closed(["Done"])
    c -->|"Act, no meeting<br/>in 30 days"| esc["Manager<br/>escalation"]

    own --> w{{"30 days:<br/>flag still there?"}}
    w -->|no| res(["Resolved"])
    w -->|yes| fu["Follow-up to<br/>the same person"]
    fu --> p{{"Picked up<br/>within 5 days?"}}
    p -->|yes| closed
    p -->|no| app["Manager approves<br/>hand-back"]
    app --> pool["Follow-up to<br/>the whole pool, once"]

    classDef human fill:#EEEDFE,stroke:#534AB7,color:#26215C
    classDef mgr fill:#FAEEDA,stroke:#854F0B,color:#412402
    classDef fin fill:#F1EFE8,stroke:#5F5E5A,color:#2C2C2A
    class q,own,call,fu,pool human
    class esc,app mgr
    class closed,res fin
```

## 5. Enterprise: QBR first, but only once

A named CSM owns the account. If a QBR is booked in the next 30 days, the
churn or expansion flag goes into the QBR brief instead of becoming a separate
task, and the QBR counts as the action. If the QBR doesn't happen, the flag
comes back as a task, and it is never handed to a QBR again: a customer who
keeps moving the QBR can't park a problem for months.

```mermaid
flowchart TB
    f["Churn or expansion<br/>flag"] --> qbr{"QBR in the<br/>next 30 days?"}
    qbr -->|yes| fold["Folded into the<br/>QBR task and brief"]
    fold --> held{{"QBR held<br/>by its date?"}}
    held -->|yes| dq(["Done<br/>the QBR counts"])
    held -->|no| back["Back as a task<br/>never folded again"]
    qbr -->|no| task["Task for the<br/>named CSM"]
    back --> task
    task --> m{{"Churn call or QBR<br/>within 30 days?"}}
    m -->|yes| closed(["Done"])
    m -->|no| esc["Manager<br/>escalation"]

    classDef human fill:#EEEDFE,stroke:#534AB7,color:#26215C
    classDef mgr fill:#FAEEDA,stroke:#854F0B,color:#412402
    classDef fin fill:#F1EFE8,stroke:#5F5E5A,color:#2C2C2A
    class fold,back,task human
    class esc mgr
    class dq,closed fin
```

A routine check-in never closes a churn task: the problem has to be raised in
a churn call or the QBR.

## 6. What "done" means: action status

The action log records that a task was sent. The status says what happened
since, worked out from the data, so nobody has to tick a box.

```mermaid
stateDiagram-v2
    [*] --> Open: task sent
    Open --> Claimed: I'll take it (Slack)
    Open --> Done: the right meeting
    Claimed --> Done: the right meeting
    Open --> Resolved: the flag went away
    Claimed --> Resolved: the flag went away
    Done --> [*]
    Resolved --> [*]
```

| Flag | Counts as Done |
| --- | --- |
| Churn | A churn call or the QBR (not a check-in) |
| Expansion | A growth call or the QBR |
| QBR | The QBR |
| Activation, escalations | Any meeting |
| CS ops (handoff gap, no CSM) | Only Resolved: the CRM record is fixed |
| Manager hand-back approval | The manager clicks "Approve hand-back" |

All the numbers above (14, 30 and 5 days) live in `settings.toml`.
