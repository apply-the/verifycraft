"""Unit test verifying that the example script executes without error."""

import subprocess
import sys
from pathlib import Path


def test_example_script_runs() -> None:
    example_path = Path(__file__).parent.parent.parent / "examples" / "verify_regression.py"
    proc = subprocess.run(
        [sys.executable, str(example_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Verdict:      SUPPORTED" in proc.stdout
    assert '"verdict": "supported"' in proc.stdout
