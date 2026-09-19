"""
Every page of the dashboard runs without raising.

This is a thin test and it is here for one reason: a Streamlit page fails at
render time, in a browser, with a traceback nobody sees until they click the
tab. A page that throws on the third tab is a page that is broken for a week.

It runs the app the way the app runs: through the entrypoint, then switching
pages, so the navigation itself is exercised too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest   # noqa: E402

ENTRY = ROOT / "app" / "dashboard.py"
PAGES = [f"views/{p.name}" for p in sorted((ROOT / "app" / "views").glob("*.py"))]


def test_entrypoint_runs():
    at = AppTest.from_file(str(ENTRY), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("page", PAGES)
def test_page_runs(page):
    at = AppTest.from_file(str(ENTRY), default_timeout=120).run()
    at.switch_page(page).run()
    assert not at.exception, [e.value for e in at.exception]
