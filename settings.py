"""
Reads settings.toml (every threshold and time window) once, for every script.

    from settings import SETTINGS
    SCOPE_DAYS = SETTINGS["activation"]["scope_days"]

TOML is a plain-text settings format that Python reads without any extra
library. Edit settings.toml, not the scripts, to tune the engine.

Your own company's settings go in settings.local.toml (git-ignored, so a
pull never overwrites them): anything in it replaces the same key in
settings.toml, section by section. settings.local.example.toml shows how to
point the engine at your own exports.

    from settings import SETTINGS, input_path
    input_path("crm_file")      # the CRM export, wherever [data] says it is
"""

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent


def _merge(base, extra):
    """Deep-merge extra into base: a key in extra wins, sections merge."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


with (REPO / "settings.toml").open("rb") as f:
    SETTINGS = tomllib.load(f)
LOCAL = REPO / "settings.local.toml"
if LOCAL.exists():
    with LOCAL.open("rb") as f:
        _merge(SETTINGS, tomllib.load(f))

# Where the inputs live ([data] inputs). Relative paths are from the repo.
INPUTS = Path(SETTINGS["data"]["inputs"])
if not INPUTS.is_absolute():
    INPUTS = REPO / INPUTS
OUTPUTS = REPO / "data"             # what the engine writes always goes here
DEMO = INPUTS.resolve() == OUTPUTS.resolve()   # the generated demo data, or a company's own


def input_path(key):
    """The full path of one input named in [data] (crm_file, team_file, ...)."""
    return INPUTS / SETTINGS["data"][key]
