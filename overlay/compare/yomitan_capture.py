"""Render the REAL Yomitan popup (the engine SubMiner embeds) for a word, via Playwright.

Loads SubMiner's bundled Yomitan extension in a Playwright Chromium, reusing a COPY of SubMiner's
profile (its imported dictionaries live in IndexedDB, keyed to the extension's fixed ID), and
screenshots the entry Yomitan's search page renders. Lets us regenerate the "reference" side for any
word instead of hand-capturing screenshots.

Prereqs: `uv add --dev playwright && uv run playwright install chromium`, and a profile copy at
$YOMITAN_PROFILE (default /tmp/yomitan-profile) — see compare/README. SubMiner's live profile is
locked, so copy it while it's fine to read:
    EXT=jbjehhccmhejadgafflkalefjpnepkle
    mkdir -p /tmp/yomitan-profile/Default/IndexedDB "/tmp/yomitan-profile/Default/Local Extension Settings"
    cp -R ~/.config/SubMiner/IndexedDB/chrome-extension_${EXT}_0.indexeddb.* /tmp/yomitan-profile/Default/IndexedDB/
    cp -R ~/.config/SubMiner/"Local Extension Settings"/$EXT "/tmp/yomitan-profile/Default/Local Extension Settings/"

    cd overlay && uv run python compare/yomitan_capture.py 本命 聞こえる 預ける
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

EXT_PATH = os.environ.get("YOMITAN_EXT_PATH", "/path/to/yomitan-extension")
PROFILE = os.environ.get("YOMITAN_PROFILE", "/tmp/yomitan-profile")
OUT = Path(__file__).resolve().parent / "out" / "yomitan"


def _ext_id(context) -> str:
    for _ in range(50):
        for sw in context.service_workers:
            if sw.url.startswith("chrome-extension://"):
                return sw.url.split("/")[2]
        context.wait_for_event("serviceworker", timeout=2000)
    raise RuntimeError("Yomitan service worker never appeared")


def capture(words: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE, headless=False, viewport={"width": 900, "height": 1400},
            args=[f"--disable-extensions-except={EXT_PATH}", f"--load-extension={EXT_PATH}"],
        )
        try:
            ext = _ext_id(context)
            print("extension id:", ext)
            page = context.new_page()
            for word in words:
                page.goto(f"chrome-extension://{ext}/search.html?query={word}", wait_until="load")
                try:
                    page.wait_for_selector(".entry, .definition-item, [data-sc-content='glossary']",
                                           timeout=10000)
                except Exception:
                    print(f"  {word}: no entry rendered (dicts not loaded?)")
                    continue
                # wait for a FULL render: network idle + the entry height stable (async freq/pitch/
                # media/harmonisation can arrive after the first paint — don't screenshot mid-render)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                prev, stable = -1, 0
                for _ in range(60):
                    h = page.evaluate(
                        "() => { const e = document.querySelector('.entry');"
                        " return e ? Math.round(e.scrollHeight) : 0; }")
                    stable = stable + 1 if (h == prev and h > 0) else 0
                    if stable >= 2:            # unchanged across two polls → settled
                        break
                    prev = h
                    page.wait_for_timeout(150)
                entry = page.query_selector(".entry") or page.query_selector("#dictionary-entries")
                dst = OUT / f"{word}.png"
                (entry or page).screenshot(path=str(dst))
                print(f"  wrote {dst.relative_to(OUT.parent.parent)}  (h={prev}px)")
        finally:
            context.close()


if __name__ == "__main__":
    words = sys.argv[1:] or ["本命", "聞こえる", "預ける"]
    if not Path(EXT_PATH).exists():
        print("bundled Yomitan not found at", EXT_PATH)
        raise SystemExit(2)
    capture(words)
