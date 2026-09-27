import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

"""
FastAPI web app: a minimal UI for pasting/uploading leads, kicking off the
research pipeline as a background task, and polling for results.
"""
import logging
import logging.handlers
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.models import LeadRecord
from app.pipeline import process_leads_batch
from app.storage import clear_leads, create_lead, get_all_leads, get_lead, init_db

#logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        ),
    ],
)
app = FastAPI(title="Sales Intelligence Automator")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _parse_leads_text(text: str) -> List[str]:
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    leads = get_all_leads()
    return templates.TemplateResponse("index.html", {"request": request, "leads": leads})


@app.post("/analyze")
async def analyze(
    background_tasks: BackgroundTasks,
    leads_text: str = Form(default=""),
    leads_file: Optional[UploadFile] = File(default=None),
):
    raw_lines: List[str] = []

    if leads_text:
        raw_lines.extend(_parse_leads_text(leads_text))

    if leads_file is not None and leads_file.filename:
        content = (await leads_file.read()).decode("utf-8", errors="ignore")
        raw_lines.extend(_parse_leads_text(content))

    # De-duplicate while preserving order
    seen = set()
    deduped = []
    for line in raw_lines:
        if line not in seen:
            seen.add(line)
            deduped.append(line)

    if deduped:
        clear_leads()

    lead_ids_and_inputs = [(create_lead(line), line) for line in deduped]

    if lead_ids_and_inputs:
        background_tasks.add_task(process_leads_batch, lead_ids_and_inputs)

    return RedirectResponse(url="/", status_code=303)


@app.get("/api/leads")
def api_leads():
    leads = get_all_leads()
    return {"leads": [l.model_dump(mode="json") for l in leads]}


@app.get("/api/leads/{lead_id}")
def api_lead(lead_id: int):
    lead = get_lead(lead_id)
    if not lead:
        return {"error": "not found"}
    return lead.model_dump(mode="json")
