"""
The one place that knows which AI provider writes the briefs: settings.toml
[llm] provider.

  anthropic  the Anthropic API, with ANTHROPIC_API_KEY from the git-ignored .env
  bedrock    Amazon Bedrock in your own AWS account, signed in with your AWS
             login (the profile in [aws], e.g. after `aws login --profile
             cairnpath`). No API key anywhere: AWS checks who you are.

Both speak the same Messages API, so brief.py and handover.py don't care which
one answers. Switching provider = one line in settings.toml, never a script.
"""

import os
import sys
from pathlib import Path

from settings import SETTINGS

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
