"""
Offline unit tests -- no network calls, no API keys required. Focused on the
parts of the pipeline that are pure functions: HTML boilerplate stripping,
internal link discovery, and lead-string normalization.

Run with:  pytest tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scraper import _clean_with_bs4_fallback, _find_internal_links
from app.search import normalize_to_url, _looks_like_url

SAMPLE_HTML = """
<html>
<head><title>Acme Co</title></head>
<body>
  <div class="cookie-banner">We use cookies. Accept?</div>
  <nav><ul><li><a href="/">Home</a></li><li><a href="/about">About</a></li></ul></nav>
  <header>Acme Co Header</header>
  <main>
    <h1>Welcome to Acme Co</h1>
    <p>We install custom roofing for homeowners across the metro area.</p>
    <a href="/services">Our Services</a>
    <a href="https://facebook.com/acmeco">Facebook</a>
  </main>
  <footer>Copyright 2024 Acme Co. All rights reserved.</footer>
</body>
</html>
"""


def test_cleaning_removes_nav_footer_and_cookie_banner():
    cleaned = _clean_with_bs4_fallback(SAMPLE_HTML)
    assert "cookies" not in cleaned.lower()
    assert "Copyright" not in cleaned
    assert "Welcome to Acme Co" in cleaned
    assert "custom roofing" in cleaned


def test_find_internal_links_only_same_domain_relevant():
    links = _find_internal_links(SAMPLE_HTML, "https://www.acmeco.com")
    assert any("services" in l for l in links)
    assert all("facebook.com" not in l for l in links)


def test_normalize_to_url_handles_bare_domain():
    assert normalize_to_url("www.example.com") == "https://www.example.com"
    assert normalize_to_url("https://example.com/page") == "https://example.com/page"
    assert normalize_to_url("Joe's Backyard Landscaping - Phoenix AZ") is None


def test_looks_like_url():
    assert _looks_like_url("example.com")
    assert not _looks_like_url("Joe's Backyard Landscaping")
