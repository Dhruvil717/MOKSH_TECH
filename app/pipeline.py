"""
Orchestrates the per-lead pipeline and fans work out across a small thread
pool (scraping and LLM calls are I/O-bound, so threads are sufficient --
no need for async/celery/queues at this scale). Each lead's status is
updated in the DB as it moves through stages so the UI can poll and show
live progress instead of a single opaque spinner.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List

from app.config import settings
from app.llm import generate_sales_brief
from app.models import LeadStatus
from app.scraper import scrape_company
from app.search import find_company_website, normalize_to_url, verify_content_matches
from app.storage import update_lead

logger = logging.getLogger("pipeline")


def process_lead(lead_id: int, raw_input: str) -> None:
    try:
        # 1. Resolve to a URL
        url = normalize_to_url(raw_input)
        resolved_via_search = url is None
        if not url:
            update_lead(lead_id, status=LeadStatus.RESEARCHING)
            url = find_company_website(raw_input)
        if not url:
            update_lead(
                lead_id,
                status=LeadStatus.ERROR,
                error_message=(
                    "Could not resolve a website for this lead from the name alone. "
                    "Try providing a direct URL instead."
                ),
            )
            return

        update_lead(lead_id, status=LeadStatus.RESEARCHING, resolved_url=url)

        # 2. Scrape
        scrape_result = scrape_company(url)
        update_lead(lead_id, pages_scraped=scrape_result.pages)

        if scrape_result.error and not scrape_result.text:
            update_lead(lead_id, status=LeadStatus.ERROR, error_message=scrape_result.error)
            return

     
        if resolved_via_search and not verify_content_matches(raw_input, scrape_result.text):
            update_lead(
                lead_id,
                status=LeadStatus.ERROR,
                error_message=(
                    f"Resolved to {url} but its content doesn't appear to match "
                    f"\"{raw_input}\" -- flagging instead of guessing. Try providing "
                    "a direct URL instead."
                ),
            )
            return

        # 3. LLM analysis
        update_lead(lead_id, status=LeadStatus.ANALYZING)
        brief = generate_sales_brief(raw_input=raw_input, url=url, content=scrape_result.text)

        # 4. Persist final result
        update_lead(lead_id, status=LeadStatus.DONE, brief=brief)

    except Exception as exc: 
        logger.exception("Lead %s failed", lead_id)
        update_lead(lead_id, status=LeadStatus.ERROR, error_message=f"{type(exc).__name__}: {exc}")


def process_leads_batch(lead_ids_and_inputs: List[tuple]) -> None:
    """Runs process_lead for a batch of (lead_id, raw_input) pairs with bounded concurrency."""
    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as pool:
        futures = [
            pool.submit(process_lead, lead_id, raw_input)
            for lead_id, raw_input in lead_ids_and_inputs
        ]
        for f in futures:
            f.result()  
