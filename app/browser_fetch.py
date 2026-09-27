"""
Optional headless-browser fetch, used as a fallback when a plain HTTP
request (httpx) gets blocked (403, connection refused, etc.) by a site that
specifically detects non-browser traffic -- no JS execution, no real TLS/
browser fingerprint, no session. A real (headless) Chromium browser looks
much closer to genuine traffic and can get past simpler bot-blocking rules.

This is entirely OPTIONAL. If Playwright (and its Chromium binary) aren't
installed, every function here just returns None and every caller falls
back to the existing httpx-only behavior -- nothing breaks if you don't
set this up.

Setup (only needed to enable this fallback):
    pip install playwright
    playwright install chromium

Note: search engines (Bing/DuckDuckGo) often have stronger, IP-reputation-
and fingerprint-based bot detection that a plain headless browser does not
reliably defeat -- this is most useful for individual company websites,
less guaranteed for search result pages. See README "Limitations".
"""
import logging
import time
from typing import Optional

from app.config import settings

logger = logging.getLogger("browser_fetch")

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def is_available() -> bool:
    return _PLAYWRIGHT_AVAILABLE


def fetch_rendered_html(url: str, timeout_ms: Optional[int] = None, retries: int = 1) -> Optional[str]:
    """
    Loads `url` in a real headless Chromium browser and returns the fully
    rendered HTML. Returns None if Playwright isn't installed, or if the
    page still fails to load after retrying once with a longer timeout and
    a looser wait condition (some slow small-business sites never fire
    "domcontentloaded" cleanly but do finish loading given more time).
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return None

    timeout_ms = timeout_ms or settings.BROWSER_TIMEOUT_MS
    attempts = [
        ("domcontentloaded", timeout_ms),
        ("load", timeout_ms + 15000),
    ][: max(1, retries + 1)]

    last_exc: Optional[Exception] = None
    for wait_until, attempt_timeout in attempts:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context(
                        user_agent=_BROWSER_UA,
                        viewport={"width": 1280, "height": 800},
                    )
                    page = context.new_page()
                    page.goto(url, timeout=attempt_timeout, wait_until=wait_until)
                    page.wait_for_timeout(1200)  # let lazy-loaded content settle
                    return page.content()
                finally:
                    browser.close()
        except Exception as exc:
            last_exc = exc
            time.sleep(1)
            continue

    logger.warning("Playwright fetch failed for %s after retries: %s", url, last_exc)
    return None