"""Run the browser-side test suites as part of the normal test run.

The watermark page's two hardest guarantees - that concealed content is
genuinely destroyed, and that the hidden mark survives compression - live in
JavaScript. They are tested where they run rather than by porting the
algorithms to Python and hoping the two stay in step.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SUITES = ["stego.test.js", "imagefx.test.js"]
JS_DIR = Path(__file__).parent / "js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")


@pytest.mark.parametrize("suite", SUITES)
def test_javascript_suite(suite):
    result = subprocess.run(["node", str(JS_DIR / suite)], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"{suite} failed:\n{result.stdout}\n{result.stderr}")
