"""
The one place that knows which AI provider writes the briefs: settings.toml
[llm] provider.

  anthropic  the Anthropic API, with ANTHROPIC_API_KEY from the git-ignored .env
  bedrock    Amazon Bedrock in your own AWS account, signed in with your AWS
             login (the profile in [aws], e.g. after `aws login --profile
             cairnpath`). No API key anywhere: AWS checks who you are.

Both speak the same Messages API, so brief.py and handover.py don't care which
one answers. Switching provider = one line in settings.toml, never a script.

Every model call goes through call(): it checks the feature's monthly budget
first ([llm.monthly_budget_usd.laptop] or .aws; over it = refused, no call), then records the
call in the AI spend ledger, data/ai_spend.csv ([llm] ledger_file): when, which
feature and account, model, tokens, cost at the prices in [llm], laptop or AWS.

    python llm.py --spend     this month's spend by feature, against its budget
"""

import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from settings import OUTPUTS, SETTINGS

LLM = SETTINGS["llm"]
AWS = SETTINGS.get("aws", {})
# CAIRNPATH_LLM_PROVIDER (set by the engine Lambda) wins: AWS can use a different provider than the laptop
PROVIDER = os.environ.get("CAIRNPATH_LLM_PROVIDER") or LLM.get("provider", "anthropic")
# Bedrock names the model by an inference profile ("eu." keeps the data in EU regions)
MODEL = LLM["bedrock_model"] if PROVIDER == "bedrock" else LLM["model"]


def load_api_key():
    """Read ANTHROPIC_API_KEY (and, optionally, ANTHROPIC_WORKSPACE_ID) from .env
    (git-ignored) without printing them. Only the anthropic provider needs it.

    The .env file is looked up next to this script, not in the folder the
    terminal happens to be in, so it works wherever you run it from.
    """
    env = Path(__file__).resolve().parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip() in ("ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID") and value.strip():
                os.environ[name.strip()] = value.strip().strip('"').strip("'")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(f"No API key: looked for ANTHROPIC_API_KEY in {env} "
                 f"({'file found' if env.exists() else 'file not found'})")


def client():
    """A ready Messages API client for the configured provider."""
    import anthropic                       # imported here so --dry-run needs nothing
    if PROVIDER == "bedrock":
        if not AWS.get("region"):
            sys.exit("settings.toml [aws] region is missing: Bedrock needs to know where to call")
        return anthropic.AnthropicBedrock(aws_profile=AWS.get("profile") or None, aws_region=AWS["region"])
    if PROVIDER != "anthropic":
        sys.exit(f"settings.toml [llm] provider = {PROVIDER!r}: use 'anthropic' or 'bedrock'")
    load_api_key()
    # A key scoped to one workspace needs nothing more. An organisation-wide key
    # must say which workspace each request is for, via this header.
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace} if workspace else None)


def describe():
    """One line for the run log: who wrote this, where."""
    if PROVIDER == "bedrock":
        return f"Amazon Bedrock, {MODEL}, {AWS.get('region')}, profile {AWS.get('profile') or 'default'}"
    return f"Anthropic API, {MODEL}"


# --- the AI spend ledger and budgets ---
LEDGER = OUTPUTS / LLM.get("ledger_file", "ai_spend.csv")
_BUDGETS = LLM.get("monthly_budget_usd", {})
COLUMNS = ["time_utc", "month", "feature", "account_id", "provider", "model",
           "input_tokens", "output_tokens", "cost_usd", "where", "stop_reason"]


class OverBudget(Exception):
    """The feature's budget for this month is spent: no model call was made."""


def where():
    """Laptop or AWS: the engine Lambda has AWS_LAMBDA_FUNCTION_NAME set."""
    return "aws" if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else "laptop"


def budgets_here(place=None):
    """This place's budgets: [llm.monthly_budget_usd.laptop] or [....aws]. The two ledgers can't
    see each other, so each place gets its own share of the total (audit 6 Nov 2026). An old flat
    section (feature = amount) still works, for both places."""
    if _BUDGETS and all(isinstance(v, (int, float)) for v in _BUDGETS.values()):
        return dict(_BUDGETS)
    return dict(_BUDGETS.get(place or where(), {}))


BUDGETS = budgets_here()


def this_month():
    # The real calendar month, not the data's as-of date: money is spent today, in real time.
    return datetime.now(timezone.utc).strftime("%Y-%m")


def cost(input_tokens, output_tokens):
    """USD at the list prices in [llm]: our estimate, not Anthropic's bill."""
    return (input_tokens * LLM["price_input_per_million"] + output_tokens * LLM["price_output_per_million"]) / 1e6


def spent(feature=None, month=None):
    """USD recorded in the ledger this month, for one feature or all."""
    if not LEDGER.exists():
        return 0.0
    month = month or this_month()
    with LEDGER.open(encoding="utf-8", newline="") as f:
        # only this place's rows: a ledger copied from the other place can't use up our budget
        return sum(float(r["cost_usd"]) for r in csv.DictReader(f)
                   if r["month"] == month and r.get("where", where()) == where()
                   and (feature is None or r["feature"] == feature))


def record(feature, account_id, response):
    """One row per call. Header once; the file only grows (like the action log)."""
    usage = response.usage
    row = {"time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "month": this_month(),
           "feature": feature, "account_id": account_id, "provider": PROVIDER, "model": MODEL,
           "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
           "cost_usd": f"{cost(usage.input_tokens, usage.output_tokens):.6f}", "where": where(),
           "stop_reason": getattr(response, "stop_reason", "") or ""}
    new = not LEDGER.exists()
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)
    return row


def call(client, feature, account_id, **request):
    """The only way the scripts call the model: budget first, then the call, then the ledger.
    A feature with no budget in settings is refused too: every feature must have a limit."""
    if feature not in BUDGETS:
        raise OverBudget(f"no budget for '{feature}' in settings.toml [llm.monthly_budget_usd.{where()}]")
    so_far = spent(feature)
    if so_far >= BUDGETS[feature]:
        raise OverBudget(f"{feature}: ${so_far:.2f} spent in {this_month()}, budget ${BUDGETS[feature]:.2f} "
                         f"(settings.toml [llm.monthly_budget_usd.{where()}])")
    response = client.messages.create(**request)
    record(feature, account_id, response)
    return response


def spend_report(month=None):
    month = month or this_month()
    lines = [f"AI spend in {month} ({where()} ledger {LEDGER.name}, {where()} budgets; estimate at the [llm] prices)"]
    for feature, budget in sorted(BUDGETS.items()):
        s = spent(feature, month)
        lines.append(f"  {feature:<12} ${s:7.3f} of ${budget:.2f}  ({s / budget:.0%})" if budget else f"  {feature}: no budget")
    lines.append(f"  {'total':<12} ${spent(None, month):7.3f}")
    return "\n".join(lines)


if __name__ == "__main__" and "--spend" in sys.argv:
    sys.stdout.reconfigure(encoding="utf-8")
    print(spend_report())

