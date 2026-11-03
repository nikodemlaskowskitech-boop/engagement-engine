"""
Gathers the check Lambda's code into one folder (infra/build/check_inputs, git-ignored):
its handler plus the few repo files it runs. Plain Python, no CDK, so the tests can
build exactly what gets deployed.
"""

import shutil
from pathlib import Path

INFRA = Path(__file__).resolve().parent
REPO = INFRA.parent
# What the check Lambda needs from the repo, and nothing else (never settings.local.toml)
CHECK_FILES = ["check_inputs.py", "settings.py", "settings.toml"]


def bundle_check_inputs(out=None):
    out = Path(out) if out else INFRA / "build" / "check_inputs"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copy(INFRA / "lambdas" / "check_inputs" / "handler.py", out)
    for name in CHECK_FILES:
        shutil.copy(REPO / name, out)
    return out
