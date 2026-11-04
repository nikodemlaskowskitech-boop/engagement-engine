"""
Stores secrets from .env in AWS Parameter Store, for what runs in AWS.

    python put_key.py           the Anthropic API key (the briefs, in the engine Lambda)
    python put_key.py --slack   the Slack signing secret and bot token (the Slack listener Lambda)

Reads ANTHROPIC_API_KEY from your git-ignored .env (as brief.py does) and saves it
as an encrypted SecureString parameter, name in settings.toml [aws]
api_key_parameter. The key is never typed into a command, never printed and never
written anywhere else: the engine Lambda reads it at run time, for the briefs step
only. Run it again after you rotate the key.

--slack reads SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN from .env the same way and
stores them under [aws] slack_signing_secret_parameter and slack_bot_token_parameter.
Only the Slack listener Lambda may read them.

Sign in first: aws login --profile cairnpath.
"""

import os
import sys

import llm
from slack_loop import load_env
from settings import SETTINGS

AWS = SETTINGS["aws"]
NAME = AWS["api_key_parameter"]


def store(ssm):
    llm.load_api_key()                      # from .env, into this process only
    key = os.environ["ANTHROPIC_API_KEY"]
    result = ssm.put_parameter(Name=NAME, Value=key, Type="SecureString", Overwrite=True,
                               Description="Anthropic API key for the Cairnpath briefs (put_key.py)")
    # only the facts, never the value (not even its first characters)
    print(f"Stored in Parameter Store as {NAME} (SecureString, version {result['Version']}).")
    return result["Version"]


SLACK_SECRETS = {"SLACK_SIGNING_SECRET": AWS["slack_signing_secret_parameter"],
                 "SLACK_BOT_TOKEN": AWS["slack_bot_token_parameter"]}


def store_slack(ssm):
    """The two Slack secrets, each as a SecureString. Stops before storing either if one is missing."""
    values = dict(zip(SLACK_SECRETS, load_env(*SLACK_SECRETS)))
    missing = [name for name, value in values.items() if not value]
    if missing:
        sys.exit(f"Not in .env: {', '.join(missing)} (Slack app > Basic Information > Signing Secret; "
                 "OAuth & Permissions > Bot User OAuth Token)")
    versions = {}
    for name, parameter in SLACK_SECRETS.items():
        result = ssm.put_parameter(Name=parameter, Value=values[name], Type="SecureString", Overwrite=True,
                                   Description=f"{name} for the Cairnpath Slack listener (put_key.py --slack)")
        print(f"Stored {name} in Parameter Store as {parameter} (SecureString, version {result['Version']}).")
        versions[name] = result["Version"]
    return versions


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"])
    (store_slack if "--slack" in sys.argv[1:] else store)(session.client("ssm"))
