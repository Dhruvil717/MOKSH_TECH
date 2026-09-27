"""
When a lead comes in as a bare company name ("Joe's Backyard Landscaping -
Phoenix AZ") instead of a URL, we need to find its website before we can
scrape anything. We use DuckDuckGo's no-JS HTML endpoint because it requires
no API key/billing, which matches the assignment's "no paid API required"
constraint. This is a best-effort heuristic, not a guarantee -- see README
"Limitations".
"""
import base64
import logging
import re
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup

from app import browser_fetch
from app.config import settings

logger = logging.getLogger("search")

_BLOCKED_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "yelp.com",
    "yellowpages.com", "bbb.org", "mapquest.com", "google.com",
    "maps.google.com", "twitter.com", "x.com", "tiktok.com",
    "indeed.com", "glassdoor.com", "angi.com", "homeadvisor.com",
    "thumbtack.com", "youtube.com", "wikipedia.org", "bing.com",
    "microsoft.com", "msn.com",
}


def _looks_like_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text.strip(), re.IGNORECASE)) or bool(
        re.match(r"^(www\.)?[a-zA-Z0-9-]+\.[a-zA-Z]{2,}(/.*)?$", text.strip())
    )


def normalize_to_url(raw: str) -> Optional[str]:
    """If the raw lead is already a URL (with or without scheme), normalize it."""
    raw = raw.strip()
    if _looks_like_url(raw) and " " not in raw:
        if not raw.lower().startswith("http"):
            raw = "https://" + raw
        return raw
    return None


def verify_content_matches(query: str, content: str) -> bool:
    """
    Final sanity check for leads resolved from a bare name: does the actual
    scraped page text reference the company at all? Used as a pipeline-level
    safety net so a resolver mistake (parked domain, coincidental slug match)
    produces an honest error instead of a confidently wrong sales brief.
    """
    tokens = _significant_tokens(query)
    if not tokens:
        return True
    haystack = content.lower()
    match_count = sum(1 for t in tokens if t in haystack)
    required = min(2, len(tokens))
    return match_count >= required


def find_company_website(query: str) -> Optional[str]:
    """
    Tries to find the most plausible official website for a company name
    (optionally with a city/state). Tries DuckDuckGo, then Bing, then Google,
    then a dependency-free domain-guess as a last resort (useful when all
    three search engines are rate-limited/bot-blocked, which is the common
    case in a short burst of automated queries from one IP).
    """
    return (
        _search_duckduckgo(query)
        or _search_bing(query)
        or _search_google(query)
        or _guess_domain(query)
    )


def _pick_from_candidates(query: str, candidates: list, engine: str) -> Optional[str]:
    """
    Accepts the first candidate whose domain or result title/snippet
    contains enough of the company's distinctive words (see
    _matches_company). Deliberately does NOT fall back to "just trust the
    top result" when nothing matches -- tested that against a live batch and
    it resolved leads to a GitHub repo, a color-theory blog, a movie-ticket
    booking page, and a sunrise/sunset calculator, because a blocked/bot-
    detected search engine can return content that has nothing to do with
    the query. A wrong lead is worse than a missing one for a sales tool, so
    an honest "couldn't resolve" is the right failure mode here -- the
    domain-guess fallback (_guess_domain) is the safer way to recover these.
    """
    for url, context in candidates:
        if _matches_company(query, url, context):
            return url

    logger.info("%s returned no relevance-matched result for: %s", engine, query)
    return None

def _guess_domain(query: str) -> Optional[str]:
    """
    Last-resort, search-engine-independent fallback: build a handful of
    plausible domains directly from the company name and check whether any
    of them actually resolve AND look like the right business. Doesn't
    depend on DuckDuckGo/Bing/Google being reachable at all, so it still
    works when all three are blocking us.

    Unlike a search-engine top-result, this starts from the company's own
    name, so a resolving domain is a much stronger signal -- but it's still
    verified against the fetched page's own text before being trusted, in
    case the exact slug happens to be squatted/parked/reused by an unrelated
    site.
    """
    tokens = _significant_tokens(query)
    words = _all_name_tokens(query)
    if not words:
        return None

    slugs = []
    joined = "".join(words)
    if joined not in slugs:
        slugs.append(joined)
    if len(words) > 1:
        slugs.append("-".join(words))
    if len(words) > 2:
        
        trimmed = "".join(words[:2])
        if trimmed not in slugs:
            slugs.append(trimmed)

    headers = {"User-Agent": settings.USER_AGENT}
    for slug in slugs:
        for tld in ("com", "net"):
            candidate = f"https://www.{slug}.{tld}"
            try:
                resp = httpx.get(
                    candidate, headers=headers, timeout=8, follow_redirects=True
                )
            except httpx.HTTPError:
                continue
            if resp.status_code >= 400 or not resp.text:
                continue
      
            page_text = resp.text.lower()
            match_count = sum(1 for t in tokens if t in page_text) if tokens else 1
            required = min(2, len(tokens)) if tokens else 0

            if len(tokens) < 2:
               
                phrase = " ".join(words[:3])
                if phrase not in page_text:
                    match_count = 0

            if match_count >= required:
                final_url = str(resp.url)
                logger.info("Domain guess verified for %s -> %s", query, final_url)
                return final_url
            logger.info(
                "Domain guess %s resolved but page content didn't match %s -- rejected",
                candidate, query,
            )
    logger.info("Domain guessing found nothing verifiable for: %s", query)
    return None


def _significant_tokens(query: str) -> list:
    """
    Pulls out distinctive words from a company name to sanity-check a
    resolved URL against. Drops the city/state suffix (after a " - ") and
    short/common words, since those produce false-positive matches (e.g.
    "blue" matching "bluedart.com" for an unrelated company).
    """
    name_part = re.split(r"\s*-\s*", query, maxsplit=1)[0]
    words = re.findall(r"[a-zA-Z]+", name_part.lower())
    stopwords = {"the", "and", "of", "a", "an", "for", "to", "in", "on", "llc", "inc", "co", "corp"}
    return [w for w in words if len(w) >= 5 and w not in stopwords]


def _all_name_tokens(query: str) -> list:
    """
    Like _significant_tokens but keeps short words too (e.g. "tree", "care",
    "auto"), for domain-guessing where dropping them would produce a slug
    that doesn't look like the real business name at all.
    """
    name_part = re.split(r"\s*-\s*", query, maxsplit=1)[0]
    words = re.findall(r"[a-zA-Z]+", name_part.lower())
    stopwords = {"the", "and", "of", "a", "an", "for", "to", "in", "on", "llc", "inc", "co", "corp"}
    return [w for w in words if w not in stopwords]


def _matches_company(query: str, url: str, context_text: str = "") -> bool:
    tokens = _significant_tokens(query)
    if not tokens:
        return True
    domain = urlparse(url).netloc.lower()
    haystack = domain + " " + context_text.lower()
    match_count = sum(1 for token in tokens if token in haystack)

    if len(tokens) >= 2:
        return match_count >= 2

 
    name_words = _all_name_tokens(query)
    phrase = " ".join(name_words[:3])
    slug = "".join(name_words[:3])
    return match_count >= 1 and (slug in domain.replace("-", "") or phrase in context_text.lower())


def _get_search_html(url: str, headers: dict) -> Optional[str]:
    """
    Tries a real headless browser first if Playwright is installed (more
    likely to get past bot-detection on search engines), falling back to a
    plain httpx request otherwise. Either way, the returned HTML is parsed
    identically afterwards.
    """
    if browser_fetch.is_available():
        html = browser_fetch.fetch_rendered_html(url)
        if html:
            return html
    try:
        resp = httpx.get(url, headers=headers, timeout=settings.REQUEST_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPError:
        return None


def _search_duckduckgo(query: str) -> Optional[str]:
    search_url = "https://duckduckgo.com/html/?" + urlencode({"q": f"{query} official website"})
    headers = {"User-Agent": settings.USER_AGENT}

    html = _get_search_html(search_url, headers)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    candidates = [] 
    for result in soup.select(".result, .web-result"):
        link = result.select_one("a.result__a")
        if not link:
            continue
        href = link.get("href", "")
        url = _unwrap_ddg_redirect(href)
        if not url:
            continue
        domain = urlparse(url).netloc.lower().replace("www.", "")
        if any(blocked in domain for blocked in _BLOCKED_DOMAINS):
            continue
        snippet = result.select_one(".result__snippet")
        context = f"{link.get_text()} {snippet.get_text() if snippet else ''}"
        candidates.append((url, context))

    return _pick_from_candidates(query, candidates, "DuckDuckGo")


def _search_bing(query: str) -> Optional[str]:
    search_url = "https://www.bing.com/search?" + urlencode({"q": f"{query} official website"})
    headers = {
        "User-Agent": settings.USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }

    html = _get_search_html(search_url, headers)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    candidates = []  
    for result in soup.select("li.b_algo"):
        link = result.select_one("h2 a")
        if not link:
            continue
        href = link.get("href", "")
        url = _unwrap_bing_redirect(href)
        if not url:
            continue
        domain = urlparse(url).netloc.lower().replace("www.", "")
        if any(blocked in domain for blocked in _BLOCKED_DOMAINS):
            continue
        caption = result.select_one(".b_caption")
        context = f"{link.get_text()} {caption.get_text() if caption else ''}"
        candidates.append((url, context))

    return _pick_from_candidates(query, candidates, "Bing")


def _search_google(query: str) -> Optional[str]:
    search_url = "https://www.google.com/search?" + urlencode(
        {"q": f"{query} official website", "num": "10"}
    )
    headers = {
        "User-Agent": settings.USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }

    html = _get_search_html(search_url, headers)
    if not html:
        return None

    if "unusual traffic" in html.lower() or "captcha" in html.lower():
        logger.info("Google served a bot-check page for: %s", query)
        return None

    soup = BeautifulSoup(html, "html.parser")
    candidates = []  # list of (url, context_text)
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        url = _unwrap_google_redirect(href)
        if not url or url in seen_urls:
            continue
        domain = urlparse(url).netloc.lower().replace("www.", "")
        if any(blocked in domain for blocked in _BLOCKED_DOMAINS):
            continue
        seen_urls.add(url)
   
        parent_text = a.find_parent().get_text(" ", strip=True) if a.find_parent() else ""
        context = f"{a.get_text()} {parent_text}"[:300]
        candidates.append((url, context))

    return _pick_from_candidates(query, candidates, "Google")


def _unwrap_google_redirect(href: str) -> Optional[str]:
    """Google's non-JS SERP wraps organic result links as /url?q=<target>&...
    Direct http(s) links (less common in this format) are passed through."""
    if href.startswith("/url?"):
        parsed = urlparse("https://www.google.com" + href)
        qs = parse_qs(parsed.query)
        return qs.get("q", [None])[0]
    if href.startswith("http") and "google.com" not in href:
        return href
    return None


def _unwrap_ddg_redirect(href: str) -> Optional[str]:
    """DuckDuckGo HTML results wrap links as /l/?uddg=<encoded target>."""
    if href.startswith("//duckduckgo.com/l/") or href.startswith("/l/"):
        parsed = urlparse(href if href.startswith("http") else "https:" + href)
        qs = parse_qs(parsed.query)
        target = qs.get("uddg", [None])[0]
        return target
    if href.startswith("http"):
        return href
    return None


def _unwrap_bing_redirect(href: str) -> Optional[str]:
    """
    Bing wraps BOTH organic and sponsored results in a click-tracking
    redirect: https://www.bing.com/ck/a?...&u=<encoded>&ntb=1
    The real destination is base64url-encoded in the 'u' query param,
    prefixed with a literal "a1" before the encoded bytes. Without
    unwrapping this, we'd resolve leads to bing.com itself (or whatever
    Bing's redirect page happens to render), not the company's real site.
    """
    if not href.startswith("http"):
        return None
    if "bing.com/ck/a" not in href:
        return href

    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    encoded = qs.get("u", [None])[0]
    if not encoded:
        return None
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
    except Exception:
        logger.warning("Failed to decode Bing redirect: %s", href)
        return None