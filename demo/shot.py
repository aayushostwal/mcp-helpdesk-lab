"""Screenshot HTML pages with Playwright. Runs under the interpreter that has Playwright.

Usage: ``python shot.py jobs.json`` where jobs is ``[{"html": ..., "png": ...}, ...]``. Kept free of
project imports and of syntax newer than Python 3.9 because the Playwright install lives in a
separate conda environment.
"""

from __future__ import annotations

import json
import sys


def main(jobs_path: str) -> int:
    from playwright.sync_api import sync_playwright

    with open(jobs_path) as fh:
        jobs = json.load(fh)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for job in jobs:
            page = browser.new_page(viewport={"width": 1200, "height": 200}, device_scale_factor=2)
            page.goto("file://" + job["html"])
            page.screenshot(path=job["png"], full_page=True)
            page.close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
