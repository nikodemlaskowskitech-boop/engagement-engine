"""
Reads settings.toml (every threshold and time window) once, for every script.

    from settings import SETTINGS
    SCOPE_DAYS = SETTINGS["activation"]["scope_days"]

TOML is a plain-text settings format that Python reads without any extra
library. Edit settings.toml, not the scripts, to tune the engine.
"""

import tomllib
from pathlib import Path

with (Path(__file__).resolve().parent / "settings.toml").open("rb") as f:
    SETTINGS = tomllib.load(f)
