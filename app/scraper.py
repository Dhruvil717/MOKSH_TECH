"""
Fetches a company's website and returns clean, LLM-ready text.

Strategy:
1. Fetch the homepage.
2. Run it through trafilatura, which is purpose-built for stripping nav bars,
   footers, cookie banners, and boilerplate while keeping the main content
   (much more reliable than hand-rolled BeautifulSoup heuristics).
3. Fall back to a manual BeautifulSoup extraction if trafilatura finds
   nothing (some small business sites are template-heavy / JS-rendered).
4. Follow a small number of same-domain internal links whose anchor text
   suggests useful pages (About, Services, Products) so the brief isn't
   based on the homepage tagline alone.
5. If a plain HTTP request gets blocked, fall back to a real headless
   browser via Playwright (optional -- see app/browser_fetch.py).
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup

from app import browser_fetch
from app.config import settings

_RELEVANT_LINK_WORDS = (
    "about", "service", "product", "solution", "who-we-are", "company",
    "what-we-do", "industries", "customers", "clients",
)

_NOISE_SELECTORS = [
    "nav", "footer", "header", "script", "style", "noscript", "iframe",
    "[class*=cookie]", "[id*=cookie]", "[class*=banner]", "[class*=popup]",
    "[class*=modal]", "[class*=nav]", "[class*=menu]", "[class*=footer]",
]


@dataclass
class ScrapeResult:
    pages: List[str] = field(default_factory=list)   
    text: str = ""                                    
    error: Optional[str] = None


def _fetch(url: str, client: httpx.Client) -> Optional[str]:
    try:
        resp = client.get(url, timeout=settings.REQUEST_TIMEOUT, follow_redirects=True)
        if resp.status_code >= 400 or not resp.text:
     
            return browser_fetch.fetch_rendered_html(url)
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return None
        return resp.text
    except httpx.HTTPError:
        
        return browser_fetch.fetch_rendered_html(url)


def _clean_with_trafilatura(html: str) -> str:
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    return (extracted or "").strip()


def _clean_with_bs4_fallback(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector in _NOISE_SELECTORS:
        for tag in soup.select(selector):
            tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and len(ln) > 1]
    return "\n".join(lines)


def _find_internal_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(base_url).netloc.lower()
    candidates = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        anchor_text = (a.get_text() or "").strip().lower()
        combined = f"{href} {anchor_text}".lower()
        if not any(word in combined for word in _RELEVANT_LINK_WORDS):
            continue
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.netloc.lower() != base_domain:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        clean_url = full_url.split("#")[0]
        if clean_url in seen or clean_url == base_url:
            continue
        seen.add(clean_url)
        candidates.append(clean_url)
    return candidates


def scrape_company(url: str) -> ScrapeResult:
    result = ScrapeResult()
    headers = {
        "User-Agent": settings.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }

    with httpx.Client(headers=headers) as client:
        homepage_html = _fetch(url, client)
        if not homepage_html:
            result.error = f"Could not fetch {url} (unreachable, blocked, or non-HTML response)."
            return result

        result.pages.append(url)
        home_text = _clean_with_trafilatura(homepage_html)
        if len(home_text) < 200:
            home_text = _clean_with_bs4_fallback(homepage_html)
        chunks = [f"=== Homepage ({url}) ===\n{home_text}"] if home_text else []

        internal_links = _find_internal_links(homepage_html, url)
        for link in internal_links[: settings.MAX_PAGES_PER_LEAD]:
            page_html = _fetch(link, client)
            if not page_html:
                continue
            page_text = _clean_with_trafilatura(page_html)
            if len(page_text) < 150:
                page_text = _clean_with_bs4_fallback(page_html)
            if page_text:
                result.pages.append(link)
                chunks.append(f"=== {link} ===\n{page_text}")

    combined = "\n\n".join(chunks).strip()
    combined = re.sub(r"\n{3,}", "\n\n", combined)
    
    result.text = combined[: settings.MAX_CONTENT_CHARS]

    if not result.text:
        result.error = f"Fetched {url} but found no extractable text content."

    return result