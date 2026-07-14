"""Browser-level smoke test: drive the real UI in headless Chromium.

Covers the layer nothing else tests — the actual rendered page:
splash, How to Play, starting a game, naming the firm, reaching the
port menu, the market log, and the scores modal. Saves a screenshot
of each stage to screenshots/ (gitignored).

Setup (once):  uv run --with playwright playwright install chromium
Run:           uv run --with playwright python scripts/browser_smoke.py
               (against a server already running on --server, default
               http://127.0.0.1:8000)
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOTS = Path(__file__).resolve().parent.parent / "screenshots"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    SHOTS.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1000, "height": 900})
        page.goto(args.server, wait_until="networkidle")

        # --- splash ---
        assert page.locator("#splash").is_visible(), "no splash screen"
        assert "Press ANY key to start" in page.locator(
            "#splash .blink").inner_text()
        page.screenshot(path=str(SHOTS / "01-splash.png"))

        # --- How to Play opens and closes ---
        page.click("#splash-help")
        assert page.locator("#help-overlay").is_visible()
        assert "Hong Kong is home" in page.locator("#help-body").inner_text()
        page.screenshot(path=str(SHOTS / "02-how-to-play.png"))
        page.keyboard.press("Escape")
        assert not page.locator("#help-overlay").is_visible()

        # --- start a game: any key -> firm name prompt ---
        page.keyboard.press("Enter")
        page.wait_for_selector("#game", state="visible")
        page.wait_for_selector("#prompt-entry", state="visible",
                               timeout=15000)
        page.fill("#prompt-input", "Headless Trading Co.")
        page.click("#prompt-enter")

        # --- mode choice -> classic; start choice -> cash ---
        page.wait_for_function(
            "document.getElementById('prompt-text')"
            ".textContent.includes('How will you sail')",
            timeout=15000)
        page.screenshot(path=str(SHOTS / "03-mode-choice.png"))
        page.get_by_role("button", name="Classic", exact=False).click()
        page.wait_for_function(
            "document.getElementById('prompt-text')"
            ".textContent.includes('Do you want to start')",
            timeout=15000)
        page.get_by_role("button", name="cash", exact=False).first.click()

        # --- the port: survive arrival events until the port menu ---
        # (Li Yuen / Wu questions may come first; answer No to each.)
        for _ in range(6):
            page.wait_for_function(
                "(t => t.includes('Shall I') || t.includes('Will you pay')"
                " || t.includes('Elder Brother Wu'))"
                "(document.getElementById('prompt-text').textContent)",
                timeout=30000)
            text = page.locator("#prompt-text").inner_text()
            if "Shall I" in text:
                break
            page.get_by_role("button", name="No", exact=True).click()
        else:
            sys.exit("never reached the port menu")

        assert "Headless Trading Co." in page.locator("#firm").inner_text()
        assert page.locator("#port-board").is_visible()
        page.screenshot(path=str(SHOTS / "04-port-menu.png"))

        # --- market log toggles ---
        page.click("#market-toggle")
        assert page.locator("#market-table").is_visible()
        page.screenshot(path=str(SHOTS / "05-market-log.png"))
        page.click("#market-toggle")

        # --- scores modal (openScores() awaits a fetch before unhiding) ---
        page.click("#scores-btn")
        page.wait_for_selector("#scores-overlay", state="visible", timeout=15000)
        page.screenshot(path=str(SHOTS / "06-scores.png"))
        page.keyboard.press("Escape")

        browser.close()

    print(f"BROWSER SMOKE PASSED - screenshots in {SHOTS}")


if __name__ == "__main__":
    main()
