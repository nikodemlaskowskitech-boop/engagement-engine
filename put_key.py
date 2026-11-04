"""
Stores the Anthropic API key in AWS Parameter Store, for the engine in AWS.

    python put_key.py

Reads ANTHROPIC_API_KEY from your git-ignored .env (as brief.py does) and saves it
as an encrypted SecureString parameter, name in settings.toml [aws]
api_key_parameter. The key is never typed into a command, never printed and never
written anywhere else: the engine Lambda reads it at run time, for the briefs step
only. Run it again after you rotate the key.

Sign in first: aws login --profile cairnpath.
"""

import os
import sys

import llm
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


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    import boto3
    session = boto3.Session(profile_name=AWS.get("profile") or None, region_name=AWS["region"])
    store(session.client("ssm"))
