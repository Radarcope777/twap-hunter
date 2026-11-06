"""Run V1/V2 share-balance cases in a clean subprocess.

The engine binds ``backend.storage`` at import. Restoring only
``sys.modules['backend.storage']`` leaves those references in place, so the
cases execute in a separate interpreter that stubs packages before import.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "tests" / "isolated" / "position_balance_cases.py"


def test_position_balance_cases_in_isolated_process():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("GOODFALLEN_DB", None)
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", str(CASES),
         "-q", "-p", "no:cacheprovider", "--tb=short"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
