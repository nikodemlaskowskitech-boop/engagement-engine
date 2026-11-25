"""
How the corpus's customers write (the persona matrix, 25 Nov 2026). Data for
generate_documents.py, not an engine step.

Real inboxes aren't written in one voice. A procurement lead writes "Dear ...,
kind regards"; an engineer writes three lines and no greeting; someone writing in
their second language says "I write you because"; an annoyed executive says it
straight. The search has to find a competitor or a budget cut in all of them, so
the corpus has six voices, and each topic's email is written in each voice: the
same facts, other words. Written by Claude (the model) on 25 Nov 2026, reviewed
and committed like any other code: nothing here is generated at run time.

Which customer writes in which voice is chosen so that every pair (voice and
topic, voice and region, voice and tier) shows up at least once across the corpus
where the corpus is big enough for it: the pairwise idea from test design (cover
every pair of factors, not every combination). generate_documents.pick_voice()
does it greedily, with no random draw.

Placeholders as in generate_documents.TEMPLATES: {s_first} the CS owner, {c_first}
the writer, {c2_first} a colleague, {inv} an invoice, {amt} an amount.
"""

# The six voices. "plain" is the corpus's own wording (generate_documents.WORDINGS).
VOICES = ["plain", "brisk", "formal", "warm", "blunt", "second_language"]

# Each voice's sign-off under its emails (the signature block follows it)
SIGN_OFF = {"plain": "", "brisk": "- {c_first}", "formal": "Kind regards,", "warm": "Cheers!",
            "blunt": "Regards,", "second_language": "Thank you in advance and best regards,"}

# Tickets keep their subject (Support triages on it) and gain the voice around the text
TICKET = {
    "plain": ("", "", ""),
    "brisk": ("", "", ""),
    "formal": ("", "Dear Support team, ", " Kind regards."),
    "warm": ("", "Hi team! ", " Thanks so much, you're always quick :)"),
    "blunt": ("URGENT: ", "", " This is blocking us. Please escalate."),
    "second_language": ("", "Hello, ", " Thank you in advance for your help."),
}

# Each topic's email in each voice but plain: (subject, body)
EMAILS = {
    "competitor": {
        "brisk": ("Renewal: benchmarking",
                  "Procurement wants a second quote before renewal. Lumen Analytics came in ~30% cheaper.\n"
                  "Need: usage numbers, what the add-ons got us. By Friday if possible."),
        "formal": ("Vendor assessment prior to contract renewal",
                   "Dear {s_first},\n\nIn line with our procurement policy, we are required to assess alternative "
                   "analytics providers before renewing any contract above a certain value. We have received a "
                   "proposal from Northbeam Insights. I would be grateful if you could provide a summary of the "
                   "value delivered this year to support our evaluation."),
        "warm": ("Quick heads-up on the renewal",
                 "Hi {s_first}! Hope your week is going well.\n\nSo, a bit awkward: our new VP is a big fan of "
                 "another analytics tool and asked us to get a demo before we sign again. Honestly we love working "
                 "with you, so I'd really like to give her a reason to stay. Can we brainstorm?"),
        "blunt": ("Your pricing",
                  "{s_first}, I'll be direct. We have an offer from Lumen Analytics at a much lower price, and "
                  "right now I can't justify the difference to my CFO. If you want to keep us, I need a better "
                  "answer before the renewal."),
        "second_language": ("About our renewal",
                            "Hello {s_first},\n\nI write you because our purchase department asks us to compare "
                            "with other providers before the renewal. We received an offer from another company, "
                            "it is cheaper. We prefer to stay with you but we need arguments. Can you help us?"),
    },
    "budget_cut": {
        "brisk": ("Budget",
                  "Tooling budget cut 20% for next year. Analytics is on the list.\n"
                  "Options for a smaller commitment at renewal? Need them before the 15th."),
        "formal": ("Revised budget for the coming financial year",
                   "Dear {s_first},\n\nFollowing our annual planning cycle, the company has reduced the budget "
                   "allocated to software subscriptions. Consequently, we will need to review the scope of our "
                   "agreement with Cairnpath at renewal. Could you kindly propose options at a lower annual cost?"),
        "warm": ("Ugh, budget season",
                 "Hi {s_first}! Hope you're well.\n\nNot the most fun email, sorry: finance came back from "
                 "planning and every team has to trim its tools. I really want to keep Cairnpath, so could we "
                 "find a smaller package that still keeps the dashboards everyone uses?"),
        "blunt": ("Costs",
                  "{s_first}, the board wants a 20% cut on every vendor this year, and you are one of the bigger "
                  "lines. Send me a cheaper option or I will have to cancel parts of the contract."),
        "second_language": ("Our budget for next year",
                            "Hello {s_first},\n\nUnfortunately our direction decided to reduce the budget for "
                            "software for next year. We must lower the cost of our contract. Is it possible to "
                            "have a smaller offer? It is important for us to keep the main dashboards."),
    },
    "champion_leaving": {
        "brisk": ("Leaving end of month",
                  "Heads-up: my last day is the 30th. {c2_first} takes over analytics.\n"
                  "She hasn't used the tool much. Worth a handover call."),
        "formal": ("Change in our team",
                   "Dear {s_first},\n\nI would like to inform you that I will be leaving the company at the end of "
                   "this month. {c2_first} will assume responsibility for our analytics. I would appreciate it if "
                   "you could arrange an introduction and a handover session with her."),
        "warm": ("Some news!",
                 "Hi {s_first}!\n\nI wanted you to hear it from me first: I've got a new job, and the 30th is my "
                 "last day here. It's been such a pleasure working with you! {c2_first} will look after "
                 "Cairnpath, but she's quite new to it, so please be nice to her :)"),
        "blunt": ("I'm out",
                  "{s_first}, I've resigned. Last day is the 30th. Nobody has been named to own the dashboards "
                  "after me. You should probably talk to {c2_first}, but I wouldn't count on her."),
        "second_language": ("I am leaving the company",
                            "Hello {s_first},\n\nI want to tell you that I leave the company at the end of the "
                            "month, I found a new position. My colleague {c2_first} will take my place for the "
                            "analytics, but she does not know the tool very well yet. Maybe you can organise a "
                            "session with her?"),
    },
    "data_missing": {
        "brisk": ("Data mismatch",
                  "DAU in Cairnpath is ~25% below our warehouse since mid-Sept.\n"
                  "Leadership noticed. Need a root cause this week."),
        "formal": ("Discrepancy in reported figures",
                   "Dear {s_first},\n\nWe have identified a significant discrepancy between the figures reported "
                   "in Cairnpath and those in our internal data warehouse. As our management relies on these "
                   "reports, I would be grateful if you could investigate this matter as a priority."),
        "warm": ("Something's off with our numbers",
                 "Hi {s_first}! Hope all's good.\n\nWe've spotted something odd: since the last release our "
                 "conversion numbers look a lot lower than what we see in our own database. The team is a bit "
                 "worried about showing the dashboards in the monthly review. Could someone take a look?"),
        "blunt": ("Your numbers are wrong",
                  "{s_first}, our dashboards have been wrong for two weeks and my managers have stopped trusting "
                  "them. I need to know what is broken and when it will be fixed."),
        "second_language": ("Problem with the data",
                            "Hello {s_first},\n\nWe see that some events are missing since the 3rd of the month. "
                            "The numbers in the dashboard are not the same as in our system, there is a "
                            "difference of around 25%. Can you check what is the problem please?"),
    },
    "not_rolled_out": {
        "brisk": ("Adoption",
                  "Only the product team uses it. Rollout stalled after the pilot.\n"
                  "Can you run a session for marketing + sales?"),
        "formal": ("Wider deployment of Cairnpath",
                   "Dear {s_first},\n\nAlthough the licence was purchased for the whole organisation, usage has so "
                   "far remained limited to our product department. We would welcome your support in planning "
                   "the deployment to the remaining teams."),
        "warm": ("Getting more people on board",
                 "Hi {s_first}!\n\nConfession time: we bought Cairnpath for everyone, but it's mostly just my "
                 "little team using it. The others never really got started. Do you have any tricks from "
                 "other customers to get people excited about it?"),
        "blunt": ("Are we using this?",
                  "{s_first}, we pay for the whole company and three people log in. I need a plan to change "
                  "that, or I'll have to explain why we're paying for it."),
        "second_language": ("Use of the tool",
                            "Hello {s_first},\n\nWe bought the licence for all the company but for the moment "
                            "only the product team uses Cairnpath. The other teams did not start. Can you propose "
                            "us a training for marketing and sales?"),
    },
    "more_volume": {
        "brisk": ("Event volume",
                  "Two app launches in Q1, volume will roughly double.\n"
                  "We'll pass our commitment before renewal. Quote for a bigger contract?"),
        "formal": ("Anticipated increase in event volume",
                   "Dear {s_first},\n\nOwing to the launch of two new applications next quarter, we anticipate "
                   "that our event volume will exceed the amount committed in our current agreement. Could you "
                   "please send us a proposal for an increased commitment?"),
        "warm": ("We're growing!",
                 "Hi {s_first}! Exciting times here.\n\nWe're launching in Germany and Spain, so traffic is "
                 "going through the roof. I think we'll run out of events long before our renewal. Can we "
                 "chat about a bigger plan?"),
        "blunt": ("Limit",
                  "{s_first}, we'll hit our event limit in about six weeks. I don't want surprises on the "
                  "invoice. Send me the options for more volume now."),
        "second_language": ("We need more events",
                            "Hello {s_first},\n\nOur usage grows a lot because we open new countries. We think "
                            "we will pass the limit of events of our contract before the renewal. Can you send us "
                            "an offer for more volume?"),
    },
    "billing_dispute": {
        "brisk": ("{inv}",
                  "Invoice {inv}: EUR {amt} overage. No warning beforehand.\n"
                  "On hold until reviewed."),
        "formal": ("Query regarding invoice {inv}",
                   "Dear {s_first},\n\nWe have received invoice {inv} for an overage of EUR {amt}. As we were not "
                   "notified that we were approaching our limit, we are unable to process this payment until the "
                   "invoice has been reviewed. I look forward to your clarification."),
        "warm": ("Invoice question",
                 "Hi {s_first}!\n\nHope you're well. Our finance team got a bit of a shock with invoice {inv}: "
                 "EUR {amt} for overage that nobody saw coming. Could we look at it together? I'm sure there's "
                 "an explanation."),
        "blunt": ("Invoice {inv} - not paying this",
                  "{s_first}, EUR {amt} in overage with zero warning is not acceptable. We won't pay invoice {inv} "
                  "until someone explains it."),
        "second_language": ("Question about invoice {inv}",
                            "Hello {s_first},\n\nWe received the invoice {inv} with EUR {amt} of overage. Nobody "
                            "informed us before that we were near the limit. Our finance does not want to pay it "
                            "before an explanation. Can you check it please?"),
    },
    "security_review": {
        "brisk": ("Security docs",
                  "Infosec needs: pen test summary, DPA, sub-processor list.\n"
                  "Deadline end of month. Questionnaire attached."),
        "formal": ("Annual vendor security assessment",
                   "Dear {s_first},\n\nAs part of our annual third-party risk assessment, our information security "
                   "team requires your most recent penetration test summary, your data processing agreement and "
                   "your list of sub-processors. Kindly provide these by the end of the month."),
        "warm": ("Security paperwork (sorry!)",
                 "Hi {s_first}!\n\nIt's that time of year again: our security team has sent me their famous "
                 "questionnaire. They need the pen test summary and the DPA. Sorry for the homework!"),
        "blunt": ("Compliance",
                  "{s_first}, I can't add any more users until our security team gets your pen test report and "
                  "the DPA. Please send them this week."),
        "second_language": ("Documents for security",
                            "Hello {s_first},\n\nOur security team needs some documents from you: the last "
                            "penetration test, the DPA and the list of sub-processors. Is it possible to send them "
                            "before the end of the month?"),
    },
    "onboarding_stuck": {
        "brisk": ("Setup delayed",
                  "Engineers are on a release. Tracking setup slips 2-3 weeks.\n"
                  "Nothing live yet."),
        "formal": ("Delay in implementation",
                   "Dear {s_first},\n\nI regret to inform you that, owing to competing priorities within our "
                   "engineering team, the implementation of the tracking plan will be delayed by several weeks. "
                   "We would welcome any assistance you can offer to accelerate the setup."),
        "warm": ("Running a bit late, sorry!",
                 "Hi {s_first}!\n\nSo sorry, we're behind: our developers got pulled into a big release and "
                 "haven't touched the tracking yet. We're really keen to get going though! Any chance someone "
                 "could help us with the setup?"),
        "blunt": ("Still not live",
                  "{s_first}, we signed weeks ago and we still have no data. The SDK setup is stuck and my team "
                  "doesn't know how to move it forward. I need help, not documentation links."),
        "second_language": ("The installation is late",
                            "Hello {s_first},\n\nOur developers are busy with another project, so the installation "
                            "of the SDK is late. We did not send any data yet. Is it possible to have help from "
                            "your team for the setup?"),
    },
}

# Names by region (25 Nov 2026: one list for all of Europe gave Ulrich Okafor at a Swedish
# company; a curated list does what a fake-name package would, with no new package)
NAMES = {
    "UK&I": (["Aoife", "Callum", "Ciara", "Declan", "Eilidh", "Fiona", "Gareth", "Hannah", "Imogen", "Jack",
              "Kieran", "Lucy", "Niamh", "Oliver", "Rhian", "Siobhan", "Tom", "Zara", "Owen", "Megan"],
             ["Byrne", "Campbell", "Davies", "Evans", "Fitzgerald", "Gallagher", "Hughes", "Kelly", "Lynch",
              "MacLeod", "Murphy", "O'Brien", "Patel", "Quinn", "Reid", "Shah", "Thomson", "Walsh", "Wright", "Young"]),
    "DACH": (["Anja", "Bastian", "Christoph", "Dana", "Elias", "Franziska", "Georg", "Hanna", "Jonas", "Katrin",
              "Lukas", "Mia", "Niklas", "Sabine", "Tobias", "Ursula", "Valentin", "Vreni", "Lea", "Moritz"],
             ["Bauer", "Becker", "Brunner", "Fischer", "Gruber", "Hofmann", "Huber", "Keller", "Koch", "Lehmann",
              "Meier", "Neumann", "Schmid", "Schneider", "Steiner", "Vogel", "Wagner", "Weber", "Wolf", "Zimmermann"]),
    "France & Benelux": (["Amélie", "Antoine", "Bram", "Camille", "Daan", "Élodie", "Fleur", "Guillaume", "Inès",
                          "Jeroen", "Lucas", "Manon", "Mathilde", "Noah", "Pieter", "Sophie", "Thomas", "Yasmine",
                          "Hugo", "Lotte"],
                         ["Bernard", "Bakker", "Claes", "De Vries", "Dubois", "Durand", "Fontaine", "Girard",
                          "Janssens", "Lambert", "Laurent", "Leroy", "Maes", "Martin", "Mertens", "Moreau",
                          "Peeters", "Petit", "Smit", "Visser"]),
    "Nordics": (["Anders", "Astrid", "Björn", "Elin", "Emil", "Freja", "Henrik", "Ingrid", "Johan", "Karin",
                 "Lars", "Linnea", "Magnus", "Maja", "Mikael", "Nora", "Oskar", "Sigrid", "Tuomas", "Aino"],
                ["Andersson", "Berg", "Dahl", "Eriksson", "Hansen", "Haugen", "Holm", "Johansson", "Karlsson",
                 "Korhonen", "Larsen", "Lindberg", "Lundqvist", "Mäkinen", "Nielsen", "Nilsson", "Olsen",
                 "Pedersen", "Sandberg", "Virtanen"]),
}
