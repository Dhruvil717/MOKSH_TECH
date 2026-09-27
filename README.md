# Sales Intelligence Automator

A prototype that takes a list of leads (URLs, company names, or "Company - City ST"
strings), researches each one on the web, and produces a structured AI-generated
sales brief: company overview, core product/service, target customer, a B2B
qualification decision, and three discovery-call questions — all through a
simple web UI.

## Project Structure

```
sales-intelligence-automator/
├── app/
│   ├── main.py          # FastAPI app + routes (UI, /analyze, /api/leads)
│   ├── pipeline.py       # Orchestrates resolve URL -> scrape -> LLM -> save
│   ├── scraper.py        # Fetches pages, strips boilerplate, light crawling
│   ├── search.py          # Resolves a bare company name to a website (DuckDuckGo)
│   ├── llm.py             # Anthropic/OpenAI client + strict JSON validation
│   ├── storage.py         # SQLite persistence
│   ├── models.py          # Pydantic schemas (SalesBrief, LeadRecord)
│   ├── templates/index.html
│   └── static/style.css
├── tests/test_scraper.py  # Offline unit tests (no network/API key needed)
├── data/sample_leads.txt  # The leads from the assignment brief
├── requirements.txt
├── .env.example
└── README.md
```

## Dependencies

- Python 3.10+
- FastAPI + Uvicorn (web server/UI)
- httpx + BeautifulSoup4 + trafilatura (fetching & cleaning web content)
- anthropic / openai SDKs (LLM calls — you only need the one you configure)
- pydantic (schema validation), tenacity (retry logic)

All pinned in `requirements.txt`.

## Install & Run

```bash
git clone <your-repo-url>
cd sales-intelligence-automator
python -m venv venv && source venv/bin/activate     # optional but recommended
pip install -r requirements.txt

cp .env.example .env
# Edit .env: set LLM_PROVIDER=anthropic, openai and groq as oer  the matching API key.
# Anthropic: https://console.anthropic.com  |  OpenAI: https://platform.openai.com

# to test this prototype.

uvicorn app.main:app --reload
```

Then open **http://localhost:8000**.

### Running in GitHub Codespaces

Open the repo in a Codespace, run the same `pip install` + `uvicorn` commands
in the terminal, and accept the forwarded-port popup for port 8000 (make sure
it's set to **Public** or **Private with your account** so you can open it in
the browser).

### Running the tests

```bash
pytest tests/
```

These are fully offline (they test HTML cleaning, link discovery, and lead
normalization against static fixtures) so they run without any API key or
network access — useful for a quick sanity check or CI.

## Using the Web Interface

1. On the homepage, paste leads into the textarea, one per line — a URL, a
   bare company name, or `Company Name - City ST` — or upload a `.txt`
   file (see `data/sample_leads.txt` for a ready-made example, which is the
   exact input list from the assignment).
2. Click **Run Analysis**. This kicks off a background job; the page
   auto-refreshes every ~2.5s while any lead is still `pending` /
   `researching` / `analyzing`.
3. Each lead renders as a card once its research finishes: company overview,
   product/service, target customer, a green/red **B2B Qualified** badge with
   its reasoning, three tailored sales questions, and a confidence label.
   Failures (unreachable site, no resolvable URL) show an inline error
   instead of silently disappearing.
4. Results persist in `data/leads.db` (SQLite), so refreshing or restarting
   the server doesn't lose prior runs — `GET /api/leads` also returns them as
   JSON if you want to pull results into another tool.

---

## Design Notes

**Architecture.** The system is a straight pipeline behind a thin FastAPI
layer: `resolve URL → scrape → LLM analyze → persist`, run per-lead inside a
bounded `ThreadPoolExecutor` (`pipeline.py`) so a batch of leads is
researched concurrently without needing a task queue at this scale. The UI
submits leads via a form, gets an immediate redirect, and polls
`GET /api/leads` every 2.5s to render progress — this keeps the request/response
cycle simple (no websockets) while still feeling live. Every lead's status
(`pending → researching → analyzing → done/error`) is written to SQLite as it
progresses, so a slow or failing lead never blocks visibility into the rest of
the batch. This design maps cleanly onto a CRM integration: `process_lead()`
is already provider-agnostic and side-effect-isolated (it only touches the DB
through `storage.py`), so swapping the trigger from "form submit" to "new
CRM lead webhook" and the sink from SQLite to a CRM's API would touch two
files, not the core logic.

**Tool choices.** `trafilatura` does the heavy lifting for content extraction
because it's purpose-built to separate main content from
nav/footer/cookie-banner/ad boilerplate — far more reliable than hand-rolled
BeautifulSoup selectors, which I use only as a fallback for template-heavy
sites where trafilatura comes back too thin. `httpx` (not `requests`) was
chosen for built-in timeout handling and because it's a drop-in upgrade path
to async later. For name-only leads, I resolve a website via DuckDuckGo's
no-JS HTML search endpoint rather than a paid search API, matching the
assignment's "no paid API required" constraint, and filter out obvious
directory/social-media domains (Yelp, Facebook, LinkedIn, etc.) from the
results. The LLM layer wraps both Anthropic and OpenAI behind one interface
(`BaseLLMClient`) so the provider is a one-line env var change — useful since
different reviewers may have credits for different providers.

**Edge cases.** Leads arrive as URLs, bare company names, or "Name - City"
strings, so `search.py` first tries to normalize the string as a URL and only
falls back to a web search if that fails. Scraping tolerates missing/thin
pages: if trafilatura extracts under ~200 characters, it retries with the
BeautifulSoup fallback before giving up; if a site is entirely unreachable or
yields no text, the lead is marked `error` with a specific message rather
than silently producing a hallucinated brief. The crawler also follows a
handful of same-domain internal links whose anchor text suggests an About/
Services/Products page (capped at `MAX_PAGES_PER_LEAD`), since a homepage
alone is often too thin (a tagline and a nav) to qualify a lead accurately.

**Keeping the LLM evaluation strict.** Three things enforce this: (1) the
system prompt gives an explicit, narrow definition of "B2B qualified"
(primary customer base is other businesses, not the general public) and
explicitly instructs the model to default to "no" under ambiguous evidence
rather than assume yes — this matters a lot for this dataset, since most of
the sample leads are residential home-service businesses (roofers,
landscapers, plumbers) that are easy to over-classify as B2B just because
they're technically businesses. (2) The model must return only JSON matching
`SalesBrief` (a Pydantic model with `min_length=3` on `sales_questions`,
required fields, etc.); if parsing or validation fails, the pipeline retries
once with the validation error appended to the prompt so the model
self-corrects, rather than silently accepting malformed output. (3) The
prompt explicitly forbids inventing unverifiable specifics (pricing, client
names, awards) and requires a `confidence` field, so thin scrapes produce
visibly low-confidence briefs instead of confidently fabricated ones.

**What I'd improve with more time.** Headless-browser rendering (Playwright)
for JS-heavy sites that trafilatura/BeautifulSoup can't see, since a handful
of small-business sites render their content client-side. A caching layer so
re-analyzing an already-scraped domain doesn't re-fetch or re-call the LLM.
Structured CRM export (CSV/HubSpot/Salesforce push) instead of just an
in-app view. A feedback loop where a sales rep can mark a brief "wrong" and
that correction gets logged for prompt iteration. And swapping the DuckDuckGo
scrape-based company search for a proper search API once a paid tier is
acceptable, since HTML scraping of search results is the least robust part of
this pipeline.

## LLM Usage Summary (per assignment FAQ)

- **Where:** `app/llm.py`, called once per lead from `pipeline.py` after
  scraping, to turn cleaned website text into the structured `SalesBrief`.
- **Why this model/endpoint:** Defaults to Anthropic Claude
  (`claude-3-5-haiku`) for cost/speed on a short-context classification +
  summarization task; OpenAI (`gpt-4o-mini`) is a fully supported drop-in
  alternative via `LLM_PROVIDER=openai`. Both are cloud APIs (no local model)
  to keep the prototype's setup to "get an API key," which fits the 2-day
  scope; a local model (e.g. via Ollama) would be a reasonable swap behind
  the same `BaseLLMClient` interface if offline operation mattered.
- **Limitations:** Output quality is capped by what's extractable from the
  homepage + a few linked pages — sites that are mostly images, JS-rendered,
  or gated behind forms will produce thin, low-confidence briefs. The B2B
  qualification is a judgment call based on self-reported website content,
  not firmographic/verified data, so it should be treated as a research aid
  for the rep, not a ground-truth CRM field.
