"""The cockpit's source, in parts (25 Nov 2026, ARCHITECTURE.md fix N).

The cockpit is one page: one HTML file with its styles and one script inside, so it
opens from a file, a Lambda or the public site with nothing else to fetch. Its source
grew to almost 5,000 lines in one file, so it lives in cockpit/ in parts, one per tab
or shared piece, and template() joins them, in this order, into the very same page
(byte for byte what views_template.html was). The parts are cut at whole lines and are
not separate modules: the script is one function, so a part may use what an earlier
part defined. Add a part by putting its name in PARTS where it belongs in the page.
"""
from pathlib import Path

FOLDER = Path(__file__).resolve().parent / "cockpit"

# (file in cockpit/, what it holds), in the page's order
PARTS = [
    ("page_head.html", "the page's head: title, fonts, the opening <style>"),
    ("style.css", "every style: colours (dark and light), layout, charts, phone"),
    ("page_body.html", "the markup: header, tabs, every panel; the data slots; the opening <script>"),
    ("js/core.js", "the data, roles and tabs, phone layout, the decisions people save"),
    ("js/segment.js", "the segment bar: who is looked at, and an owner's work"),
    ("js/account.js", "the Account tab: timeline, why now, to-dos, growth state, next step, forecast, research, handover"),
    ("js/onboarding.js", "Programmes: onboarding"),
    ("js/team.js", "the CS team"),
    ("js/book.js", "the book of business and capacity"),
    ("js/lists.js", "shared: the clickable account list"),
    ("js/overview.js", "Operational: the sieve and renewals"),
    ("js/outcomes.js", "Did it work? (the backtest)"),
    ("js/tasks.js", "Tasks, the funnel, signals fired, coverage, promises"),
    ("js/mywork.js", "My work, My week, the Slack cards, the sandbox's saved decisions"),
    ("js/leadership.js", "Leadership: trends, performance (GRR, NRR), exposure, signals, growth, the one-pager"),
    ("js/shared.js", "shared: My book's constants, the stacked bar chart, sparklines, sortable tables, data missing"),
    ("js/mybook.js", "My book"),
    ("js/search_tour.js", "account search, the demo site's tour, the end of the script"),
]


def template():
    """The whole page's template: every part, in order, exactly as written."""
    return "".join((FOLDER / name).read_text(encoding="utf-8") for name, _ in PARTS)


if __name__ == "__main__":
    for name, what in PARTS:
        print(f"{(FOLDER / name).read_text(encoding='utf-8').count(chr(10)):>5}  {name:<20} {what}")
