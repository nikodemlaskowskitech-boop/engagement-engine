"""
The search corpus (Phase 9, 13 Nov 2026): support tickets, emails and call notes for
about 30 accounts of the demo book, written to data/<[search] corpus_folder>/ in the
same shape as stories/ (directory.csv, support_cases.csv, emails/*.eml, calls/*.txt,
contacts.csv), so documents.py reads both the same way.

Each account's documents fit its story in the data (generate_events.py's
personality): a declining account talks about budget cuts, a competitor or its
champion leaving; a power user about hitting its limit; shelfware about a rollout
that never happened. Each topic is written three ways (ticket, email, call) in
different words, like real people write, so a search has to find "we're looking
at Lumen Analytics" when asked about competitors. Every account also gets routine
documents (a password reset, a webinar thank-you), and a few healthy accounts get
traps: words a question uses, without its meaning ("budget approved").

Also writes search_key.json: each question a CS person would ask and the accounts
that answer it (and the traps). For testing the search only: search.py never reads it.

Reads the generator's answer key (personalities): a generator, like
generate_events.py, never an engine step. Seeded: same data, same documents.

    python generate_documents.py
"""
import csv
import json
import random
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

from settings import DEMO, OUTPUTS, SETTINGS

SEED = 7                                   # its own seed: the events don't move when this changes
CONF = SETTINGS["search"]
HAND = SETTINGS["handover"]
OUT = OUTPUTS / CONF["corpus_folder"]
STORY_ACCOUNTS = {"acc_024", "acc_012", "acc_156"}   # stories/ already covers these

# How many accounts of each personality get documents (Enterprise and Scale, named owner)
PICK = {"declining": 9, "slipping": 4, "shelfware": 4, "power_user": 5, "healthy": 4, "new_customer": 4}
# What each personality's documents talk about: a main topic (taken in turn when there are
# several, so every question has accounts to find) and, half the time, a second one.
# Healthy accounts mostly have only routine documents: the ones a search must not return.
TOPICS_BY_PERSONALITY = {
    "declining": (["budget_cut", "competitor", "champion_leaving"], ["budget_cut", "competitor", "champion_leaving"]),
    "slipping": (["data_missing"], ["billing_dispute"]),
    "shelfware": (["not_rolled_out"], ["budget_cut"]),
    "power_user": (["more_volume"], ["billing_dispute"]),
    "healthy": ([None], ["security_review"]),
    "new_customer": (["onboarding_stuck"], ["security_review"]),
}

# The questions a CS person asks, in their words (not the documents' words)
QUESTIONS = {
    "competitor": "Which accounts are evaluating a competitor?",
    "budget_cut": "Which customers are under budget pressure?",
    "champion_leaving": "Where is our main contact leaving the company?",
    "data_missing": "Which accounts have data quality problems?",
    "not_rolled_out": "Which accounts aren't really using what they bought?",
    "more_volume": "Which customers need more capacity?",
    "billing_dispute": "Which accounts are disputing an invoice?",
    "security_review": "Which accounts have open security or compliance requirements?",
    "onboarding_stuck": "Which new customers are stuck getting started?",
}

# Each topic three ways. {c} customer, {c2} a colleague, {f} finance, {s} the CS owner;
# *_first = first name. Calls: (who, line), who "c" = customer, "s" = CS.
TEMPLATES = {
    "competitor": {
        "email": ("Contract review ahead of renewal",
                  "{s_first},\n\nHeads up: procurement has asked us to benchmark Lumen Analytics before we "
                  "renew. Their quote is about 30% lower than ours. I'd rather stay, but I need numbers to "
                  "defend it internally."),
        "call": [("c", "Our new CTO used Northbeam Insights at his last company and wants us to trial it."),
                 ("s", "What would make that trial unnecessary for him?"),
                 ("c", "Seeing the experiments add-on working on our checkout. He thinks we under-use the tool.")],
        "ticket": ("Full export of historical data",
                   "We need a full export of our raw events and dashboard definitions for a vendor comparison "
                   "(RFP) next month. What formats are supported?",
                   "Shared the bulk export guide and the schema reference."),
    },
    "budget_cut": {
        "email": ("Spend review",
                  "Hi {s_first},\n\nFinance has frozen all new software spend until Q2 and asked every team "
                  "to cut tooling costs by 20%. We may have to come down to a smaller commitment at renewal."),
        "call": [("c", "The CFO is going through every subscription line by line."),
                 ("c", "Anything without a clear owner and a number attached to it gets cancelled."),
                 ("s", "Then let's put a number on what the dashboards save your product team.")],
        "ticket": ("Cheapest plan that keeps our dashboards",
                   "We've been told to reduce costs. What is the smallest plan that keeps our existing "
                   "dashboards and the data from this year?",
                   "Explained the plans; suggested a call with the account owner."),
    },
    "champion_leaving": {
        "email": ("Some personal news",
                  "{s_first},\n\nI wanted you to hear it from me: the 30th is my last day here, I'm joining "
                  "another company. {c2_first} will pick up analytics, but she hasn't used the tool much."),
        "call": [("c", "I'm moving to a new role in the group from next month, so I won't run analytics anymore."),
                 ("s", "Who takes over the dashboards and the weekly review?"),
                 ("c", "Not decided yet. Possibly nobody for a while.")],
        "ticket": ("Transfer workspace ownership",
                   "Please make {c2} an admin and remove me as owner of the workspace. I'm leaving at the "
                   "end of the month.",
                   "Ownership transferred to {c2}."),
    },
    "data_missing": {
        "email": ("Numbers don't match our warehouse",
                  "Hi {s_first},\n\nSince mid-September our daily active users in Cairnpath are 25% lower than "
                  "in our own warehouse. Leadership has stopped trusting the dashboards."),
        "call": [("c", "There's a gap in our charts from the 3rd to the 9th. Nothing came through at all."),
                 ("s", "Did anything change in your release that week?"),
                 ("c", "We moved the tracking to a new server. Maybe that's it.")],
        "ticket": ("Events missing since SDK upgrade",
                   "After upgrading the Android SDK about half of our purchase events no longer arrive. Our "
                   "revenue dashboard is wrong.",
                   "Escalated to engineering; a fix is being tested."),
    },
    "not_rolled_out": {
        "email": ("Training for the marketing team?",
                  "Hi {s_first},\n\nWe bought Cairnpath for the whole company, but outside product nobody "
                  "logs in. Could you run a session for marketing?"),
        "call": [("c", "Honestly, only my team of three uses it."),
                 ("c", "The wider rollout was paused when the reorganisation started."),
                 ("s", "Which team would get the most out of it first?")],
        "ticket": ("Invite users in bulk",
                   "We want to give access to 40 people in sales and marketing. Right now only a handful "
                   "have accounts. Is there a CSV import?",
                   "Sent the bulk invite steps."),
    },
    "more_volume": {
        "email": ("Volume for next year",
                  "{s_first},\n\nWe're launching two new apps in Q1 and expect our event volume to double. "
                  "We'll go past our commitment well before renewal. Can we talk about a bigger contract?"),
        "call": [("c", "At this rate we'll hit the limit in about six weeks."),
                 ("s", "Is that the new markets or more tracking per user?"),
                 ("c", "New markets. Germany and Spain went live last month.")],
        "ticket": ("Usage warning email",
                   "We got an email saying we're at 92% of our annual events. What happens when we go over?",
                   "Explained overage pricing; the account owner will follow up."),
    },
    "billing_dispute": {
        "email": ("Invoice {inv}",
                  "{s_first},\n\nWe received invoice {inv} for an overage of EUR {amt}. Nobody warned us. "
                  "We won't pay it until it has been reviewed."),
        "call": [("c", "Our finance team is unhappy about the last invoice."),
                 ("c", "The overage came as a complete surprise to them."),
                 ("s", "I'll get you the usage by month so we can go through it together.")],
        "ticket": ("Charged twice for October",
                   "We were charged twice for October. Please issue a credit note.",
                   "Passed to billing."),
    },
    "security_review": {
        "email": ("Security questionnaire",
                  "Hi {s_first},\n\nOur infosec team needs your latest pen test summary and the DPA before "
                  "the end of the month. Their questionnaire is attached."),
        "call": [("c", "Legal wants to know where the data is hosted and who your sub-processors are."),
                 ("s", "All in the EU. I'll send the sub-processor list today.")],
        "ticket": ("SAML single sign-on with Okta",
                   "Our security team requires single sign-on through Okta before we add more users.",
                   "Shared the SAML setup guide."),
    },
    "onboarding_stuck": {
        "email": ("Delay on our side",
                  "Hi {s_first},\n\nOur engineers are tied up with a release, so the tracking setup will "
                  "slip by a few weeks. Sorry about that."),
        "call": [("c", "We still haven't got the tracking plan signed off, so nothing is live yet."),
                 ("s", "Who needs to sign it off?"),
                 ("c", "Our head of engineering, and he's been on leave.")],
        "ticket": ("SDK build fails",
                   "The iOS SDK fails to build with Xcode 17, so we can't start sending events.",
                   "Known issue; workaround sent."),
    },
}

# Routine documents every account has some of: they must never answer a question
ROUTINE = [
    ("ticket", ("Password reset email not arriving", "Two of our users never get the reset email.",
                "It was in their spam folder. Solved.")),
    ("ticket", ("Change billing address", "Our office moved; please update the billing address.", "Updated.")),
    ("ticket", ("Time zone in scheduled reports", "Scheduled reports arrive at 3am our time.",
                "Set the workspace time zone. Solved.")),
    ("email", ("Thanks for the webinar", "Hi {s_first},\n\nThanks for the funnels webinar yesterday. The "
               "team found the retention part really useful.")),
    ("email", ("Updated PO number", "Hi {s_first},\n\nPlease use PO 4471-B on the next invoice. Thanks.")),
    ("call", [("s", "Anything you'd like us to look at this quarter?"),
              ("c", "Not really. The team likes the new funnels and the weekly digest.")]),
]

# Traps: a question's words without its meaning, on healthy accounts
TRAPS = {
    "budget_cut": ("email", ("Next year", "Hi {s_first},\n\nGood news: our budget for next year is approved, "
                             "Cairnpath included. Let's plan the QBR.")),
    "competitor": ("call", [("c", "We looked at a competitor two years ago and stayed with you."),
                            ("c", "No plans to change. The team is happy.")]),
    "billing_dispute": ("ticket", ("Invoice copy", "Could you send a copy of the March invoice for our "
                                   "auditors?", "Sent. Solved.")),
}

FIRST = ["Ana", "Bruno", "Camille", "Dario", "Elin", "Fabian", "Greta", "Hugo", "Ilse", "Jonas", "Klara",
         "Lars", "Maren", "Nadia", "Oskar", "Paula", "Quentin", "Rhys", "Sanne", "Tobias", "Ulla", "Vera",
         "Wim", "Yara", "Zoe", "Aksel", "Brigid", "Cormac", "Delphine", "Emil", "Freya", "Gustav", "Helene",
         "Ivo", "Jana", "Kai", "Lotte", "Matteo", "Nina", "Ole", "Pia", "Renske", "Stig", "Thea"]
LAST = ["Ahlberg", "Baumann", "Carter", "Dubois", "Eriksen", "Fischer", "Gallagher", "Hansen", "Jansen",
        "Keane", "Lindqvist", "Meyer", "Novak", "Okafor", "Peeters", "Quinlan", "Richter", "Sorensen",
        "Thibault", "Ulrich", "Vos", "Walsh", "Zimmer", "Berg", "Costa", "Durand", "Engel", "Fournier",
        "Graf", "Holm", "Kowalski", "Lambert", "Moreau", "Nyberg", "Olsen", "Pohl", "Roux", "Strand"]
ROLES = ["Head of Product Analytics", "Growth Lead", "Product Analyst", "Head of Data", "Product Manager"]
ROLES2 = ["Marketing Manager", "Data Engineer", "Product Owner"]
FINANCE = ["Finance Manager", "Procurement Lead", "Finance Business Partner"]
SUFFIX = {"UK&I": "Ltd", "DACH": "GmbH", "France & Benelux": "SAS", "Nordics": "AB"}
PHONE = {"UK&I": "+44 20 5550 {:04d}", "DACH": "+49 30 5550 {:04d}",
         "France & Benelux": "+33 1 55 50 {:02d} {:02d}", "Nordics": "+46 8 555 0{:02d} {:02d}"}
TZ = {region: timezone(timedelta(hours=h)) for region, h in SETTINGS["regions"]["utc_offset"].items()}


def slug(text):
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")


def phone(rng, region):
    fmt = PHONE[region]
    return fmt.format(rng.randrange(10000)) if fmt.count("{") == 1 else fmt.format(rng.randrange(100), rng.randrange(100))


def read_csv(name):
    with (OUTPUTS / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def pick_accounts(rng):
    """About 30 accounts of the book with a named owner, by personality, in a stable order."""
    personality = {r["account_id"]: r["personality"] for r in read_csv("answer_key.csv")}
    team = {t["owner_id"]: t for t in read_csv("cs_team.csv")}
    book = {}
    for a in read_csv("crm_accounts.csv"):
        owner = a["csm_owner"] or a["pool_owner"]
        if (a["tier"] in ("Enterprise", "Scale") and a["status"] != "churned" and owner in team
                and team[owner]["owner_type"] in ("csm", "member") and a["account_id"] not in STORY_ACCOUNTS):
            book.setdefault(personality.get(a["account_id"]), []).append({**a, "owner": team[owner]})
    picked = []
    for kind, n in PICK.items():
        picked += rng.sample(sorted(book.get(kind, []), key=lambda a: a["account_id"]), n)
    return sorted(picked, key=lambda a: a["account_id"]), personality


def fill(text, people, rng):
    """Put the people (and an invoice) into a template."""
    inv = f"INV-{rng.randrange(30000, 39999)}"
    amt = f"{rng.randrange(20, 140) * 100:,}"
    return text.format(inv=inv, amt=amt, **people)


def main():
    if not DEMO:
        print("Search corpus: skipped (your own data: its documents come from your systems, not a generator)")
        return
    rng = random.Random(SEED)
    as_of = date.fromisoformat(max(p.stem for p in (OUTPUTS / "events").glob("*.jsonl")))
    accounts, personality = pick_accounts(rng)
    for sub in ("emails", "calls"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
        for old in (OUT / sub).iterdir():
            old.unlink()                       # rewritten whole each time: no stale files
    directory, contacts, tickets, key = [], [], [], {q: set() for q in QUESTIONS}
    traps = {q: set() for q in TRAPS}
    used_names = set()
    ticket_no = 5000
    healthy_seen = 0
    turn = {}                                  # how many of each personality so far

    for a in accounts:
        acc, region, company = a["account_id"], a["region"], a["account_name"]
        domain = slug(company) + ".example"
        org = f"{company} {SUFFIX[region]}"
        directory.append({"account_id": acc, "company": company, "email_domain": domain,
                          "helpdesk_org": org, "handover_from": "", "handover_to": ""})

        def person(roles):
            while True:
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                if name not in used_names:        # one person, one name across the corpus
                    used_names.add(name)
                    return name, rng.choice(roles)
        (c, c_role), (c2, c2_role), (f, f_role) = person(ROLES), person(ROLES2), person(FINANCE)
        s = a["owner"]["owner_name"]
        email_of = lambda n, d=domain: n.lower().replace(" ", ".") + "@" + d
        for name, role in ((c, c_role), (c2, c2_role), (f, f_role)):
            contacts.append({"account_id": acc, "name": name, "email": email_of(name), "side": "customer"})
        contacts.append({"account_id": acc, "name": s, "email": email_of(s, "cairnpath.example"), "side": "cs"})
        people = {"c": c, "c2": c2, "f": f, "s": s, **{k + "_first": v.split()[0] for k, v in
                                                        (("c", c), ("c2", c2), ("f", f), ("s", s))}}
        c_phone = phone(rng, region)
        tz = TZ[region]

        # What this account's documents talk about: one or two topics from its story
        main, extra = TOPICS_BY_PERSONALITY[personality[acc]]
        i = turn[personality[acc]] = turn.get(personality[acc], -1) + 1
        topics = [main[i % len(main)]]
        second = rng.choice(extra)
        if rng.random() < 0.5 and second not in topics:
            topics.append(second)
        topics = [x for x in topics if x]
        docs = []
        for topic in topics:
            key[topic].add(acc)
            for kind in rng.sample(["ticket", "email", "call"], 2):     # two of the three ways
                docs.append((topic, kind, TEMPLATES[topic][kind]))
        for kind, tpl in rng.sample(ROUTINE, 2):
            docs.append(("routine", kind, tpl))
        if personality[acc] == "healthy":
            trap = list(TRAPS)[healthy_seen % len(TRAPS)]               # the traps spread over the healthy ones
            healthy_seen += 1
            if trap not in topics:
                traps[trap].add(acc)
                docs.append(("trap", *TRAPS[trap]))

        for n, (topic, kind, tpl) in enumerate(docs):
            day = as_of - timedelta(days=rng.randrange(5, 120))
            when = datetime(day.year, day.month, day.day, rng.randrange(9, 17), rng.choice([0, 15, 30, 45]), tzinfo=tz)
            name = f"{day}_{slug(company)}_{topic.replace('_', '-')}-{n}"
            writer = f if topic == "billing_dispute" or (topic == "budget_cut" and kind == "email") else c
            w_role = f_role if writer == f else c_role
            if kind == "ticket":
                ticket_no += 1
                subject, desc, comment = (fill(t, people, rng) for t in tpl)
                tickets.append({"ticket_id": f"{''.join(w[0] for w in company.split())[:2].upper()}-{ticket_no}",
                                "organization": org, "requester_name": writer, "requester_email": email_of(writer),
                                "created_at": str(day), "updated_at": str(day + timedelta(days=rng.randrange(1, 6))),
                                "priority": rng.choice(["P2", "P3", "P4"]), "type": "question",
                                "status": rng.choice(["open", "solved"]), "subject": subject,
                                "description": desc, "latest_comment": f"{comment} (agent Sam Ortiz)"})
            elif kind == "email":
                m = EmailMessage()
                m["From"] = f"{writer} <{email_of(writer)}>"
                m["To"] = f"{s} <{email_of(s, 'cairnpath.example')}>"
                if writer == f:
                    m["Cc"] = f"{c} <{email_of(c)}>"
                m["Date"] = format_datetime(when)
                m["Subject"] = fill(tpl[0], people, rng)
                m["Message-ID"] = f"<{name}.eml@demo.example>"
                m.set_content(fill(tpl[1], people, rng) + f"\n\n{writer}\n{w_role}, {org}\n{c_phone}\n")
                (OUT / "emails" / f"{name}.eml").write_bytes(bytes(m))
            else:
                start, lines = 30, []
                for who, text in tpl:
                    speaker = s if who == "s" else c
                    lines.append(f"[00:{start // 60:02d}:{start % 60:02d}] {speaker}: {fill(text, people, rng)}")
                    start += rng.randrange(20, 60)
                title = {"routine": "check-in", "trap": "check-in"}.get(topic, "call")
                (OUT / "calls" / f"{name}.txt").write_text(
                    f"Title: {company} - {title}\nDate: {when:%Y-%m-%d %H:%M}\nCRM account: {acc}\n"
                    f"Participants: {s} (Cairnpath, {'CSM' if a['owner']['owner_type'] == 'csm' else 'Scale team'}), "
                    f"{c} ({company}, {c_role})\n\n" + "\n".join(lines) + "\n", encoding="utf-8")

    def write(name, rows):
        with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    write("directory.csv", directory)
    write(HAND["support_file"], sorted(tickets, key=lambda t: t["ticket_id"]))
    write(HAND["contacts_file"], contacts)
    (OUT / CONF["key_file"]).write_text(json.dumps({
        "as_of": str(as_of),
        "note": "For testing the search only: search.py never reads this file.",
        "questions": [{"id": q, "question": text, "accounts": sorted(key[q]), "traps": sorted(traps.get(q, []))}
                      for q, text in QUESTIONS.items()]}, indent=2) + "\n", encoding="utf-8")
    n_docs = len(tickets) + sum(1 for _ in (OUT / "emails").iterdir()) + sum(1 for _ in (OUT / "calls").iterdir())
    print(f"Search corpus: {len(accounts)} accounts, {n_docs} documents in {OUT}")
    for q, text in QUESTIONS.items():
        print(f"  {len(key[q]):>2} accounts  {text}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
