"""Capture full-page screenshots of the Session-15 front-end surfaces.

Usage:
    python scripts/s15/screenshot.py

Assumes the built FE is served at $FE_BASE (default http://127.0.0.1:8098)
and the live backend at the VITE_API_BASE the build was compiled with.
Full-page capture so bottom-row cards are never clipped.
"""
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

FE_BASE = os.environ.get("FE_BASE", "http://127.0.0.1:8098")
OUT = Path(os.environ.get("OUT_DIR", "/mnt/results/zetabridge_session15/screens"))
OUT.mkdir(parents=True, exist_ok=True)

TARGETS = [
    ("live_extraction_console", "/#/live", "btn-ega-files"),   # click to fetch live EGA
    ("opportunity_board", "/#/opportunities", None),
]


def capture(page, name, route, click_testid):
    url = FE_BASE + route
    print(f"[shot] {name}: {url}")
    page.goto(url, wait_until="networkidle", timeout=30000)
    time.sleep(1.5)
    if click_testid:
        try:
            btn = page.get_by_test_id(click_testid)
            btn.click(timeout=5000)
            print(f"       clicked {click_testid}, waiting for live fetch...")
            time.sleep(4.0)  # let the live EGA call return + render
        except Exception as e:
            print(f"       (no {click_testid} to click: {e})")
    page.wait_for_load_state("networkidle", timeout=15000)
    time.sleep(0.5)
    out = OUT / f"{name}.png"
    page.screenshot(path=str(out), full_page=True)
    print(f"       saved {out} ({out.stat().st_size} bytes)")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900},
                                device_scale_factor=2)
        for name, route, click in TARGETS:
            try:
                capture(page, name, route, click)
            except Exception as e:
                print(f"[ERR] {name}: {e}", file=sys.stderr)
        browser.close()


if __name__ == "__main__":
    main()
