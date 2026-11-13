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


# --- The files one step hands to the next (ARCHITECTURE.md fix B, 13 Nov 2026) ---
# Every step asks for a file by name, never by a path typed into its SQL, so a
# file is renamed or moved in one place: settings.toml [outputs].
def output_file(name):
    """The full path of one file the engine writes, by its name in [outputs]."""
    return OUTPUTS / SETTINGS["outputs"][name]


def out_sql(name):
    """The same path, spelled for DuckDB (forward slashes, even on Windows)."""
    return str(output_file(name)).replace("\\", "/")


# --- One pipeline list (fix B): run_all.ps1 and the engine Lambda read the same steps ---
def pipeline(place, regenerate=False, actions=False):
    """The steps for one place ("laptop" or "aws"), in order, from [pipeline] steps."""
    out = []
    for s in SETTINGS["pipeline"]["steps"]:
        if s.get("where", "both") not in ("both", place):
            continue
        if (s.get("when") == "regenerate" and not regenerate) or (s.get("when") == "actions" and not actions):
            continue
        out.append(s["step"])
    return out


def optional_steps():
    """Steps whose failure is a warning, not a stop (an outside API, the leak check)."""
    return [s["step"] for s in SETTINGS["pipeline"]["steps"] if s.get("optional")]


# The Lambda's lists come from the same pipeline, so the laptop and AWS can't drift apart
# (they had: retention.py was in one list only).
SETTINGS["aws"]["engine_steps"] = pipeline("aws")
SETTINGS["aws"]["engine_optional_steps"] = optional_steps() + [SETTINGS["aws"]["briefs_step"]]


if __name__ == "__main__":
    import sys
    # run_all.ps1 asks for its steps: python settings.py --steps laptop [regenerate] [actions]
    # one per line; an optional step starts with "?"
    if len(sys.argv) > 2 and sys.argv[1] == "--steps":
        opt = set(optional_steps())
        for step in pipeline(sys.argv[2], "regenerate" in sys.argv, "actions" in sys.argv):
            print(("?" if step in opt else "") + step)
