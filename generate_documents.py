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
import uuid
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

# Two more wordings per topic and channel (18 Nov 2026). Calls keep their number of lines,
# so the random draws (times, gaps) stay exactly as before.
MORE_WORDINGS = {
    "competitor": {
        "email": [("Before we sign again",
                   "Hi {s_first},\n\nOur new head of data is pushing us to look at other analytics vendors "
                   "before the renewal. Two of them have already sent proposals. Can you help me make the case "
                   "for staying?"),
                  ("Alternatives on the table",
                   "{s_first},\n\nI should be open with you: we've had a demo from Northbeam Insights and the "
                   "team liked it. Nothing is decided, but price will matter when the contract comes up.")],
        "call": [[("c", "Procurement wants three quotes this year, so we're talking to Lumen Analytics too."),
                  ("s", "What would they need to see from us to stop the search?"),
                  ("c", "A lower price, or proof the add-ons pay for themselves.")],
                 [("c", "One of our product leads keeps comparing your funnels with another tool he trialled."),
                  ("s", "Which parts of the comparison worry you most?"),
                  ("c", "Speed of the dashboards, and the cost per event.")]],
        "ticket": [("API limits for a data migration",
                    "We may move our analytics to another platform and need to pull everything out through "
                    "the API. What rate limits apply?",
                    "Shared the API limits and the export endpoints."),
                   ("Feature comparison request",
                    "Our evaluation team is comparing analytics vendors. Can you confirm what is included in our "
                    "plan versus the higher tiers?",
                    "Sent the plan comparison; flagged to the account owner.")],
    },
    "budget_cut": {
        "email": [("Our renewal and the budget",
                   "{s_first},\n\nWe've just come out of budget planning and analytics took a hit. I'll need a "
                   "cheaper option for next year, or a much smaller volume."),
                  ("Cost savings exercise",
                   "Hi {s_first},\n\nThe board has asked for savings across all vendors this quarter. Could you "
                   "send me options to bring the annual cost down?")],
        "call": [[("c", "Every vendor has to justify its cost to the finance committee next month."),
                  ("c", "We're cutting anything that isn't clearly essential."),
                  ("s", "Let's show them which decisions your team made with the data this year.")],
                 [("c", "Our spending is under a lot of scrutiny since the funding round slipped."),
                  ("c", "I've been asked to reduce the contract at renewal."),
                  ("s", "Which teams depend on it most? We'll protect those first.")]],
        "ticket": [("Downgrade options",
                    "Management wants us to lower our costs. Can we move to a cheaper plan mid-contract?",
                    "Explained the contract terms; passed to the account owner."),
                   ("Reduce the committed volume",
                    "What would our price be if we cut our committed events in half at the next renewal?",
                    "Sent the price bands; the account owner will follow up.")],
    },
    "champion_leaving": {
        "email": [("Handing over",
                   "Hi {s_first},\n\nQuick note: I've resigned and leave at the end of next month. {c2_first} "
                   "will be your contact, but she's new to analytics, so she'll need help."),
                  ("Change of contact",
                   "{s_first},\n\nI'm stepping away from my role here soon. Please copy {c2_first} on "
                   "everything from now on. I'm not sure the new team will keep the weekly review going.")],
        "call": [[("c", "I've accepted an offer elsewhere, so this is probably our last call together."),
                  ("s", "Who should we work with once you've gone?"),
                  ("c", "Probably {c2_first}, but nothing is confirmed.")],
                 [("c", "The reorganisation means I'm leaving the product team in a few weeks."),
                  ("s", "Who will own the dashboards after that?"),
                  ("c", "Nobody has been named yet.")]],
        "ticket": [("Make a new admin",
                    "I'm leaving the company soon. Please give {c2} admin rights so the account isn't locked.",
                    "{c2} is now an admin."),
                   ("Change the main contact",
                    "Please change the primary contact on our account to {c2}. I'm leaving shortly.",
                    "Main contact updated to {c2}.")],
    },
    "data_missing": {
        "email": [("Can we trust these numbers?",
                   "{s_first},\n\nOur conversion figures in Cairnpath have been off since the last release, and "
                   "our managers have noticed. We need to find out why before the board pack."),
                  ("Duplicate events",
                   "Hi {s_first},\n\nSome of our sign-up events are counted twice, so the funnel looks better than "
                   "it is. The team doesn't know which numbers to believe.")],
        "call": [[("c", "Our weekly report showed zero purchases on two days last month."),
                  ("s", "Were the events sent and dropped, or never sent?"),
                  ("c", "We don't know. That's the problem.")],
                 [("c", "The dashboard and our finance figures disagree by about a fifth."),
                  ("s", "Is it all events or one platform?"),
                  ("c", "Mostly the web app, since the redesign.")]],
        "ticket": [("Incorrect user counts",
                    "Our active user count dropped by 30% overnight, but our traffic didn't. Is the data wrong?",
                    "Found a filter on the project; investigating with the customer."),
                   ("Ingestion errors on our project",
                    "We see a lot of rejected events in the debugger since Tuesday. Can you check what is failing?",
                    "Schema mismatch found; fix shared.")],
    },
    "not_rolled_out": {
        "email": [("Getting more teams on board",
                   "Hi {s_first},\n\nWe've barely used Cairnpath outside the analytics team. Sales and support "
                   "never picked it up. Have other customers solved this?"),
                  ("Low adoption",
                   "{s_first},\n\nI'll be honest: most of the licences we planned for are unused. The rollout "
                   "stalled after the pilot. Can we talk about a plan?")],
        "call": [[("c", "We only ever finished the pilot with one team."),
                  ("c", "Everyone else still works from spreadsheets."),
                  ("s", "Who sponsored the pilot, and are they still there?")],
                 [("c", "We haven't touched the experiments add-on since we bought it."),
                  ("c", "Nobody had time to set it up."),
                  ("s", "Let's pick one test we could run this month.")]],
        "ticket": [("Training materials",
                    "Do you have training videos? Most of our teams haven't started using the tool yet.",
                    "Shared the academy links."),
                   ("How to set up the experiments add-on",
                    "We paid for experiments but never set them up. Where do we start?",
                    "Sent the setup guide; offered a session.")],
    },
    "more_volume": {
        "email": [("Running out of events",
                   "Hi {s_first},\n\nOur traffic is growing faster than planned and we're close to our yearly "
                   "event limit. What are our options for more volume?"),
                  ("Expanding to new markets",
                   "{s_first},\n\nWe're rolling out to three more countries this spring, so we'll need far more "
                   "events than our current plan. Could you send a quote?")],
        "call": [[("c", "Our usage has grown much faster than we expected this year."),
                  ("s", "Is that new users or new tracking?"),
                  ("c", "New users, mostly from the mobile launch.")],
                 [("c", "We're adding two more product lines to the tracking next quarter."),
                  ("s", "Do you know roughly how many events that adds?"),
                  ("c", "Our engineers think about 40% more.")]],
        "ticket": [("Over our event limit",
                    "We've passed 95% of our annual volume. Will data stop coming in when we hit 100%?",
                    "No data loss; explained overage and passed to the account owner."),
                   ("Increase our plan",
                    "How do we add more events to our contract before we run out?",
                    "Passed to the account owner for a quote.")],
    },
    "billing_dispute": {
        "email": [("Query on {inv}",
                   "Hi {s_first},\n\nWe don't agree with invoice {inv} (EUR {amt} in overage). The usage "
                   "alerts never reached us. Please put it on hold while we review."),
                  ("Invoice {inv} on hold",
                   "{s_first},\n\nFinance has put invoice {inv} on hold. EUR {amt} for overage wasn't in our "
                   "budget and we were never told we were close to the limit.")],
        "call": [[("c", "Finance is refusing to pay the overage invoice."),
                  ("c", "They say nobody told them about the limit."),
                  ("s", "I'll send the monthly usage and the alerts we sent, and we'll review it together.")],
                 [("c", "We have a disagreement about the last bill."),
                  ("c", "The amount doesn't match what we thought we'd used."),
                  ("s", "Let's go through the usage line by line.")]],
        "ticket": [("Wrong amount on invoice",
                    "Our latest invoice is higher than our contract. Please explain the extra charge.",
                    "Explained the overage; passed to billing."),
                   ("Credit note request",
                    "We were billed for a plan we didn't order. Please send a credit note.",
                    "Passed to billing.")],
    },
    "security_review": {
        "email": [("Compliance documents",
                   "Hi {s_first},\n\nAs part of our annual vendor review we need your ISO certificate, the "
                   "latest pen test summary and the data processing agreement."),
                  ("GDPR questions",
                   "{s_first},\n\nOur DPO has questions about where our data is stored and how long you keep it. "
                   "We can't add new users until this is answered.")],
        "call": [[("c", "Our auditors are asking for your SOC 2 report and the sub-processor list."),
                  ("s", "I'll send both under NDA this week.")],
                 [("c", "We need data residency in the EU confirmed in writing."),
                  ("s", "It's all hosted in the EU. I'll get you the written confirmation.")]],
        "ticket": [("Enforce SSO for all users",
                    "Our security policy says every user must log in through single sign-on. How do we enforce it?",
                    "Shared the SSO enforcement setting."),
                   ("Data retention settings",
                    "Compliance needs us to delete user-level data after 12 months. Where is this set?",
                    "Shared the retention settings.")],
    },
    "onboarding_stuck": {
        "email": [("Still not live",
                   "{s_first},\n\nWe're further behind than planned. The tracking plan is still with our "
                   "developers and they won't get to it before next month."),
                  ("Help with the setup",
                   "Hi {s_first},\n\nWe signed a while ago but still haven't sent any data. Could someone help us "
                   "with the SDK installation?")],
        "call": [[("c", "Nothing has been implemented yet; the developers are on another project."),
                  ("s", "Could we help with a working session?"),
                  ("c", "That would help. We don't know where to start.")],
                 [("c", "We're stuck on the tracking plan. Nobody agrees on the event names."),
                  ("s", "Shall I share a starter plan from a similar customer?"),
                  ("c", "Yes please, anything to get going.")]],
        "ticket": [("No events arriving",
                    "We installed the SDK but nothing shows up in the project. Are we missing a step?",
                    "Wrong API key in the config; fixed."),
                   ("Help with the tracking plan",
                    "We're new and don't know which events to track first. Is there a template?",
                    "Sent the starter tracking plan.")],
    },
}
# Each topic's three wordings per channel, taken in turn across the corpus (no random draw, so
# nothing else moves): with one wording, accounts on the same topic had word-for-word the same
# email, which a demo viewer spotted (18 Nov 2026)
WORDINGS = {t: {k: [v, *MORE_WORDINGS[t][k]] for k, v in kinds.items()} for t, kinds in TEMPLATES.items()}


# Every call note is also a meeting in the engagement data (14 Nov 2026: "ask about this account"
# found call notes the engine didn't know about). Its topic is the call's purpose, in the
# engagement schema's words: a call about a churn worry is a churn call, and so on.
CALL_TOPIC = {"budget_cut": "churn_call", "competitor": "churn_call", "champion_leaving": "churn_call",
              "data_missing": "churn_call", "not_rolled_out": "churn_call", "billing_dispute": "churn_call",
              "more_volume": "growth_call", "onboarding_stuck": "activation_call",
              "security_review": "check_in", "routine": "check_in", "trap": "check_in"}
MEETINGS_FILE = "document_calls.jsonl"     # in data/engagement/, rewritten whole each run (never doubled)

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

# Cases (23 Nov 2026): a real problem is a thread, not one message. The CS owner answers the
# customer's email (a reply: In-Reply-To, "Re:"), linking the ticket when there is one; an open
# one gets a "any news?" from the customer; a call about it is logged against the ticket. New
# customers stuck at the start get the implementation lead too, on the thread, so the CSM's
# reply is logged twice in the CRM (once by each): documents.py keeps it once.
# {ref} = "I've linked this to ticket XX-5012. " when the topic has a ticket.
REPLIES = {
    "competitor": "Thanks for the heads-up. Before the comparison, let me show you what the "
                  "experiments add-on has done for teams like yours. {ref}Could we take 30 minutes this week?",
    "budget_cut": "Understood. Let's put a number on what the dashboards save your product team each month. "
                  "{ref}I'll send a one-page summary before your review.",
    "champion_leaving": "Thank you for telling me first, and congratulations. Could you introduce me "
                        "to {c2_first} before you go? {ref}I'll set up a handover session.",
    "data_missing": "I've raised this with our support engineers. {ref}They're comparing your SDK "
                    "versions now; I'll update you tomorrow.",
    "not_rolled_out": "Let's get the rollout moving. {ref}I can run a kick-off for the remaining "
                      "teams next week.",
    "billing_dispute": "Sorry for the surprise. {ref}I'm checking the invoice with our billing team and will "
                       "come back to you by Friday.",
    "more_volume": "Great news. {ref}I'll prepare options for a bigger commitment before your renewal.",
    "onboarding_stuck": "I've asked {i_first} from our implementation team to join us and unblock "
                        "the setup. {ref}You'll hear from them today.",
    "security_review": "Our security team is on it. {ref}You'll have the answers by the end of "
                       "next week.",
}
FOLLOW_UP = "Thanks {s_first}. Any news on this? {ref}We'd like to close it before the end of the month."
IMPLEMENTATION = ("Hi {c_first},\n\nI'm {i_first}, I lead implementation for your account. Let's install the "
                  "SDK together: I've booked Tuesday at 10:00 for a working session. {ref}")
IMPL_ROLES = ["Implementation Lead", "Implementation Consultant"]

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


CALL_TITLE = {"churn_call": "account review", "growth_call": "growth planning",
              "activation_call": "onboarding check", "check_in": "check-in"}


def same_invoice(parts):
    """One document, one invoice number (23 Nov 2026: a subject said INV-36164, its body
    INV-34132, because fill() draws one per field). The draws stay as they were, so no other
    document moves: the later fields just take the first field's number."""
    import re
    first = next((m.group(0) for p in parts for m in [re.search(r"INV-\d+", p)] if m), None)
    return [re.sub(r"INV-\d+", first, p) if first else p for p in parts]


def fill(text, people, rng):
    """Put the people (and an invoice) into a template."""
    inv = f"INV-{rng.randrange(30000, 39999)}"
    amt = f"{rng.randrange(20, 140) * 100:,}"
    return text.format(inv=inv, amt=amt, **people)


def threads(made, a, people, org, email_of, as_of, tz, rx, contacts, used_names, cases):
    """One account's cases (23 Nov 2026): replies to each topic's email, the ticket linked in
    them, a call logged against the ticket, and the implementation lead on a stuck start. Its
    own random draws (rx), so the documents written before don't change. Records each case's
    files in cases (the answer key) for tests/corpus_test.py."""
    acc, s = a["account_id"], a["owner"]["owner_name"]
    cs_mail = lambda n: email_of(n, "cairnpath.example")
    for topic, got in made.items():
        if topic not in REPLIES:                   # routine documents and traps: no case to follow
            continue
        tid = got.get("ticket")
        ref = f"I've linked this to ticket {tid}. " if tid else ""
        # a call joins the case only through the ticket it's logged against: no ticket, no certain link
        files = [f"ticket {tid}", *([f"call {got['call'].name}"] if "call" in got else [])] if tid else []
        if tid and "call" in got:                 # the call, logged against the ticket in the CRM
            text = got["call"].read_text(encoding="utf-8")
            got["call"].write_text(text.replace(f"CRM account: {acc}\n", f"CRM account: {acc}\nRelated case: {tid}\n", 1),
                                   encoding="utf-8")
        if "email" not in got:
            if len(files) > 1:
                cases.append({"account_id": acc, "files": files})
            continue
        name, subject, day, writer = got["email"]
        root = f"<{name}.eml@demo.example>"
        files.append(f"email {root}")
        impl = None
        if topic == "onboarding_stuck":            # Implementation joins: a person of our own, labelled by the redaction
            while impl is None or impl in used_names:
                impl = f"{rx.choice(FIRST)} {rx.choice(LAST)}"
            used_names.add(impl)
            contacts.append({"account_id": acc, "name": impl, "email": cs_mail(impl), "side": "cs"})
        p = {**people, "i_first": impl.split()[0] if impl else ""}
        sent = []

        def send(sender, to, body, when, cc=None, team="CS", logged=("CS",), refs=()):
            m_id = f"<{name}-re{len(sent) + 1}.eml@demo.example>"
            for n, who in enumerate(logged):
                m = EmailMessage()
                m["From"], m["To"] = f"{sender} <{(cs_mail if sender in (s, impl) else email_of)(sender)}>", to
                if cc:
                    m["Cc"] = cc
                m["Date"] = format_datetime(datetime(when.year, when.month, when.day, rx.randrange(9, 18),
                                                     rx.choice([0, 15, 30, 45]), tzinfo=tz))
                m["Subject"] = "Re: " + subject
                m["Message-ID"], m["In-Reply-To"], m["References"] = m_id, refs[-1], " ".join(refs)
                m["X-Team"], m["X-Logged-By"] = team, who
                m.set_content(body)
                (OUT / "emails" / f"{when}_{slug(a['account_name'])}_{topic.replace('_', '-')}-re{len(sent) + 1}"
                                  f"{'' if n == 0 else '-copy'}.eml").write_bytes(bytes(m))
            sent.append(m_id)
            files.append(f"email {m_id}")
            return m_id

        def later(d, lo, hi):                      # a working day, never after the data's last day
            d = d + timedelta(days=rx.randrange(lo, hi))
            d += timedelta(days={5: 2, 6: 1}.get(d.weekday(), 0))
            return min(as_of, d)
        customer = f"{writer} <{email_of(writer)}>"
        d1 = later(day, 1, 4)
        body = f"Hi {writer.split()[0]},\n\n" + REPLIES[topic].format(ref=ref, **p) + f"\n\n{s}"
        r1 = send(s, customer, body, d1, cc=f"{impl} <{cs_mail(impl)}>" if impl else None,
                  logged=("CS", "Implementation") if impl else ("CS",), refs=(root,))
        if impl:                                   # the implementation lead writes on the same thread
            send(impl, customer, IMPLEMENTATION.format(ref=ref, **p) + f"\n\n{impl}", later(d1, 1, 3),
                 cc=f"{s} <{cs_mail(s)}>", team="Implementation", logged=("Implementation",), refs=(root, r1))
        if rx.random() < 0.5:                      # still open: the customer asks again
            send(writer, f"{s} <{cs_mail(s)}>", FOLLOW_UP.format(ref=f"(ticket {tid}) " if tid else "", **p)
                 + f"\n\n{writer}", later(d1, 3, 8), team="CS", logged=("CS",), refs=(root, *sent))
        cases.append({"account_id": acc, "files": files})


def main():
    if not DEMO:
        print("Search corpus: skipped (your own data: its documents come from your systems, not a generator)")
        return
    rng = random.Random(SEED)
    rx = random.Random(SEED * 1000 + 1)        # the cases' own draws: every older document stays as it was
    as_of = date.fromisoformat(max(p.stem for p in (OUTPUTS / "events").glob("*.jsonl")))
    accounts, personality = pick_accounts(rng)
    for sub in ("emails", "calls"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
        for old in (OUT / sub).iterdir():
            old.unlink()                       # rewritten whole each time: no stale files
    directory, contacts, tickets, key = [], [], [], {q: set() for q in QUESTIONS}
    traps = {q: set() for q in TRAPS}
    cases = []                                 # the answer key's cases: which files make one case
    used_names = set()
    meetings = []                              # every call note, as the meeting it was
    ticket_no = 5000
    healthy_seen = 0
    turn = {}                                  # how many of each personality so far
    used = {}                                  # how many times each topic's wording was used

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
        made = {}                              # each topic's documents: its case (23 Nov 2026)
        for topic in topics:
            key[topic].add(acc)
            for kind in rng.sample(["ticket", "email", "call"], 2):     # two of the three ways
                n_used = used.get((topic, kind), 0)
                used[(topic, kind)] = n_used + 1
                docs.append((topic, kind, WORDINGS[topic][kind][n_used % len(WORDINGS[topic][kind])]))
        for kind, tpl in rng.sample(ROUTINE, 2):
            docs.append(("routine", kind, tpl))
        if personality[acc] == "healthy":
            trap = list(TRAPS)[healthy_seen % len(TRAPS)]               # the traps spread over the healthy ones
            healthy_seen += 1
            if trap not in topics:
                traps[trap].add(acc)
                docs.append(("trap", *TRAPS[trap]))

        # nothing before the customer signed (16 Nov 2026: four call notes came before it, so
        # their meetings did too); the same random draws as before, so other accounts don't move
        since = date.fromisoformat(a["customer_since"] or a["contract_start"])
        for n, (topic, kind, tpl) in enumerate(docs):
            back = rng.randrange(5, 120)
            day = as_of - timedelta(days=back)
            if day <= since:                   # a young account: spread over its own days instead
                day = since + timedelta(days=1 + back % max(1, (as_of - since).days - 1))
            if kind == "call":                 # meetings never at weekends (generate_events' rule)
                day += timedelta(days={5: -1, 6: 1}.get(day.weekday(), 0))
                if day <= since:               # a Saturday just after signing: the Monday instead
                    day += timedelta(days=2)
            when = datetime(day.year, day.month, day.day, rng.randrange(9, 17), rng.choice([0, 15, 30, 45]), tzinfo=tz)
            name = f"{day}_{slug(company)}_{topic.replace('_', '-')}-{n}"
            writer = f if topic == "billing_dispute" or (topic == "budget_cut" and kind == "email") else c
            w_role = f_role if writer == f else c_role
            if kind == "ticket":
                ticket_no += 1
                subject, desc, comment = same_invoice([fill(t, people, rng) for t in tpl])
                made.setdefault(topic, {})["ticket"] = f"{''.join(w[0] for w in company.split())[:2].upper()}-{ticket_no}"
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
                subject, body = same_invoice([fill(tpl[0], people, rng), fill(tpl[1], people, rng)])
                m["Subject"] = subject
                m["Message-ID"] = f"<{name}.eml@demo.example>"
                m.set_content(body + f"\n\n{writer}\n{w_role}, {org}\n{c_phone}\n")
                (OUT / "emails" / f"{name}.eml").write_bytes(bytes(m))
                made.setdefault(topic, {})["email"] = (name, str(m["Subject"]), day, writer)
            else:
                start, lines = 30, []
                for who, text in tpl:
                    speaker = s if who == "s" else c
                    lines.append(f"[00:{start // 60:02d}:{start % 60:02d}] {speaker}: {fill(text, people, rng)}")
                    start += rng.randrange(20, 60)
                # the call's purpose in its title, like a calendar invite (23 Nov 2026: "the customer -
                # call" said nothing once the redaction took the company out)
                title = {"routine": "check-in", "trap": "check-in"}.get(topic, CALL_TITLE[CALL_TOPIC[topic]])
                meetings.append({"event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"cairnpath-call/{name}")),
                                 "event_name": "meeting_held", "event_type": "engagement",
                                 "timestamp": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 "account_id": acc, "user_id": f"{acc}_usr_01",
                                 "properties": {"owner_id": a["owner"]["owner_id"], "topic": CALL_TOPIC[topic]},
                                 "context": {"source": "cs_history"}})
                made.setdefault(topic, {})["call"] = OUT / "calls" / f"{name}.txt"
                (OUT / "calls" / f"{name}.txt").write_text(
                    f"Title: {company} - {title}\nDate: {when:%Y-%m-%d %H:%M}\nCRM account: {acc}\n"
                    f"Participants: {s} (Cairnpath, {'CSM' if a['owner']['owner_type'] == 'csm' else 'Scale team'}), "
                    f"{c} ({company}, {c_role})\n\n" + "\n".join(lines) + "\n", encoding="utf-8")

        threads(made, a, people, org, email_of, as_of, tz, rx, contacts, used_names, cases)

    (OUTPUTS / "engagement" / MEETINGS_FILE).write_text(
        "".join(json.dumps(m) + "\n" for m in sorted(meetings, key=lambda m: (m["timestamp"], m["account_id"]))),
        encoding="utf-8")

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
                      for q, text in QUESTIONS.items()],
        "cases": cases}, indent=2) + "\n", encoding="utf-8")
    n_docs = len(tickets) + sum(1 for _ in (OUT / "emails").iterdir()) + sum(1 for _ in (OUT / "calls").iterdir())
    print(f"Search corpus: {len(accounts)} accounts, {n_docs} files in {OUT} (an email logged twice is two files); "
          f"{len(meetings)} calls logged as meetings in data/engagement/{MEETINGS_FILE}")
    for q, text in QUESTIONS.items():
        print(f"  {len(key[q]):>2} accounts  {text}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
