"""Deterministic proof that the 999.0 rate-ratio sentinel is rendered HONESTLY
on the Opportunities board — by reading the actual rendered DOM text, not a
screenshot viewport.

Passes iff:
  - at least one opportunity card corresponds to a Panitumumab control-absent AE
  - NO visible card text contains the bare token "999" as a metric value
  - the honest sentinel language ("Ctrl arm = 0" / "exp-only") is present
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

FE_BASE = os.environ.get("FE_BASE", "http://127.0.0.1:8098")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 3200})
        page.goto(FE_BASE + "/#/opportunities", wait_until="networkidle", timeout=30000)
        time.sleep(2.5)
        # full rendered text of the opportunities region
        body = page.inner_text("body")
        browser.close()

    has_panitumumab = "PANITUMUMAB" in body.upper()
    has_honest = ("ctrl arm = 0" in body.lower()) or ("exp-only" in body.lower())
    # A bare "999" appearing as a displayed metric would be the failure.
    has_bare_999 = "999" in body

    print(f"Panitumumab control-absent AE card present : {has_panitumumab}")
    print(f"honest sentinel language present           : {has_honest}")
    print(f"bare '999' anywhere in rendered text        : {has_bare_999}")

    # show the lines mentioning the sentinel for the record
    for line in body.splitlines():
        low = line.lower()
        if "ctrl arm" in low or "exp-only" in low or "999" in line or "control" in low and "absent" in low:
            print("   >>", line.strip()[:160])

    ok = has_panitumumab and has_honest and not has_bare_999
    print("\nRESULT:", "PASS — sentinel rendered honestly, no bare 999" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
