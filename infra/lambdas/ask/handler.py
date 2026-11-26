"""
Lambda: the Ask box (26 Nov 2026, step 2 of 4). A signed-in CS person asks one question
about one account on their page; the answer comes from the engine's facts and the account's
redacted documents (ask.ask_redacted), cited and checked.

Who: API Gateway checks the sign-in (the CS team's user pool) before this runs, and passes
its claims. custom:person on the login says whose page it is (an owner_id from the CS team,
or the leader), set by the admin who made the login. A question about an account that isn't
on that person's page is refused: the same cut as the cockpit and the MCP server
(roles.slice_export).

What it can see: the engine's export (outputs/views_data.json, read only), the redacted
documents bundled with it (the public search's file), the API key (one parameter), and its
own small state under state/ask/: the month's question counts and its own AI ledger. Never
a raw document, so it can't leak a customer's name: it never holds one (PRIVACY.md §8).

Money: [ask] daily_questions_per_person per login per day (UTC), then the monthly budget
[llm.monthly_budget_usd.aws] ask_account for everyone, checked before every call. One run
at a time (reserved concurrency 1), so the counts and the ledger never race.

What it never does: write the question or the answer anywhere. No log line holds them; an
error is reported by its type only. Only counts are kept.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
BUCKET = os.environ.get("BUCKET", "")
EXPORT_KEY = os.environ.get("EXPORT_KEY", "outputs/views_data.json")
KEY_PARAMETER = os.environ.get("KEY_PARAMETER", "")
OWNER = os.environ.get("ACCOUNT_ID", "")         # every bucket call says whose bucket it expects
ACCOUNT_RE = re.compile(r"acc_\d{3,5}")

_state = {}              # kept while warm: the export (by its ETag), the search index, the clients


def _s3():
    if "s3" not in _state:
        import boto3
        _state["s3"] = boto3.client("s3")
    return _state["s3"]


def _answer(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, ensure_ascii=False)}


def _read(s3, key, default=None):
    """An object's bytes, or default when it isn't there yet."""
    try:
        return s3.get_object(Bucket=BUCKET, Key=key, ExpectedBucketOwner=OWNER)["Body"].read()
    except Exception as e:     # botocore's ClientError; NoSuchKey because the role may list state/ask/
        if (getattr(e, "response", None) or {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return default
        raise


def _export(s3):
    """The engine's export, read again only when the engine has written a new one."""
    etag = s3.head_object(Bucket=BUCKET, Key=EXPORT_KEY, ExpectedBucketOwner=OWNER)["ETag"]
    if _state.get("export_etag") != etag:
        _state["export"] = json.loads(_read(s3, EXPORT_KEY))
        _state["export_etag"] = etag
    return _state["export"]


def _index():
    """The redacted documents and their search index, built once per cold start."""
    if "rows" not in _state:
        import duckdb
        import search
        rows = [json.loads(x) for x in (HERE / "documents.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        con = duckdb.connect()
        search.add_views(con, rows=rows)
        _state["rows"], _state["con"] = rows, con
    return _state["rows"], _state["con"]


def _api_key(ssm=None):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        if ssm is None:
            import boto3
            ssm = boto3.client("ssm")
        os.environ["ANTHROPIC_API_KEY"] = ssm.get_parameter(Name=KEY_PARAMETER, WithDecryption=True)["Parameter"]["Value"]


def whose_page(export, person_id):
    """The accounts on this person's page, or None when the login names nobody we know."""
    import roles
    everyone = {roles.ROLES["leader_id"]: {"role": "leader", "name": roles.ROLES["leader_name"], "manager": None},
                **roles.people(export["team"])}
    if person_id not in everyone:
        return None
    _, meta = roles.slice_export(export, person_id, everyone[person_id])
    return set(meta["accounts"])


def handler(event, context, s3=None, client=None, ssm=None, now=None):
    import ask
    import llm
    from settings import SETTINGS
    conf = SETTINGS["ask"]
    s3 = s3 or _s3()
    claims = (((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    login, person = claims.get("sub"), claims.get("custom:person")
    if not login or not person:                    # the gateway lets only signed-in calls through: belt and braces
        return _answer(401, {"error": "Sign in first."})
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return _answer(400, {"error": "Send JSON: {\"account_id\": ..., \"question\": ...}"})
    account_id, question = str(body.get("account_id") or ""), str(body.get("question") or "").strip()
    if not ACCOUNT_RE.fullmatch(account_id):
        return _answer(400, {"error": "Not an account ID."})
    if not question:
        return _answer(400, {"error": "Ask a question."})
    if len(question) > int(conf["question_chars"]):
        return _answer(400, {"error": f"Keep the question under {conf['question_chars']} characters."})

    export = _export(s3)
    page = whose_page(export, person)
    if page is None:
        return _answer(403, {"error": "This login isn't linked to anyone in the CS team."})
    if account_id not in page:
        return _answer(403, {"error": "That account isn't on your page."})

    # the daily cap: counts only, by the real day (it limits spending, not facts)
    now = now or datetime.now(timezone.utc)
    usage_key = conf["usage_key"].format(month=now.strftime("%Y-%m"))
    usage = json.loads(_read(s3, usage_key, b"{}"))
    day = now.strftime("%Y-%m-%d")
    mine = usage.setdefault(login, {})
    cap = int(conf["daily_questions_per_person"])
    if mine.get(day, 0) >= cap:
        return _answer(429, {"error": f"That's today's {cap} questions. More tomorrow."})

    # its own ledger, so the monthly budget counts every Ask question in AWS
    ledger = Path(os.environ.get("ASK_LEDGER", "/tmp/ask_ai_spend.csv"))      # /tmp: the only place a Lambda writes
    old = _read(s3, conf["ledger_key"])
    if old is None:
        ledger.unlink(missing_ok=True)
    else:
        ledger.write_bytes(old)
    llm.LEDGER = ledger
    llm.BUDGETS = llm.budgets_here("aws")

    rows, con = _index()
    try:
        if client is None:
            _api_key(ssm)
            client = llm.client()
        result = ask.ask_redacted(account_id, question, export, rows, con=con, client=client)
    except ask.Refused as e:
        return _answer(422, {"error": str(e)})
    except llm.OverBudget:
        return _answer(429, {"error": "The Ask box has used this month's AI budget. It's back next month."})
    except Exception as e:                         # the type only: a message could quote the question
        print(f"ask failed: {type(e).__name__}")
        return _answer(502, {"error": "No answer this time. Try again in a minute."})
    finally:
        if ledger.exists():                        # what was spent stays counted, answer or not
            s3.put_object(Bucket=BUCKET, Key=conf["ledger_key"], Body=ledger.read_bytes(), ExpectedBucketOwner=OWNER)

    mine[day] = mine.get(day, 0) + 1
    s3.put_object(Bucket=BUCKET, Key=usage_key, Body=json.dumps(usage, separators=(",", ":")).encode(),
                  ExpectedBucketOwner=OWNER)
    left = cap - mine[day]
    return _answer(200, {"account_id": account_id, "as_of": export["as_of"], "answer": result["answer"],
                         "sources": result["sources"], "checks": result["checks"], "questions_left_today": left})
