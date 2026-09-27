"""
Lightweight persistence using stdlib sqlite3 (no ORM needed at this scale).
Each lead is one row; the LLM brief and page list are stored as JSON text
columns, which keeps the schema simple while still being queryable by the
handful of top-level fields (status, url) we actually filter on.

Swapping this for Postgres later (e.g. if this became a CRM-integrated
service) would only mean changing the connection + a couple of query params,
since the shape of the data is already relational and JSON-friendly.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional

from app.config import settings
from app.models import LeadRecord, LeadStatus, SalesBrief

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_input TEXT NOT NULL,
    resolved_url TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    pages_scraped TEXT NOT NULL DEFAULT '[]',
    brief_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    os.makedirs(os.path.dirname(settings.DATABASE_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(settings.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(_SCHEMA)


def create_lead(raw_input: str) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO leads (raw_input, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (raw_input, LeadStatus.PENDING.value, _now(), _now()),
        )
        return cur.lastrowid


def clear_leads() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM leads")


def update_lead(
    lead_id: int,
    status: Optional[LeadStatus] = None,
    resolved_url: Optional[str] = None,
    error_message: Optional[str] = None,
    pages_scraped: Optional[List[str]] = None,
    brief: Optional[SalesBrief] = None,
) -> None:
    fields, values = [], []
    if status is not None:
        fields.append("status = ?")
        values.append(status.value)
    if resolved_url is not None:
        fields.append("resolved_url = ?")
        values.append(resolved_url)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    if pages_scraped is not None:
        fields.append("pages_scraped = ?")
        values.append(json.dumps(pages_scraped))
    if brief is not None:
        fields.append("brief_json = ?")
        values.append(brief.model_dump_json())
    fields.append("updated_at = ?")
    values.append(_now())
    values.append(lead_id)

    with _connect() as conn:
        conn.execute(f"UPDATE leads SET {', '.join(fields)} WHERE id = ?", values)


def _row_to_record(row: sqlite3.Row) -> LeadRecord:
    return LeadRecord(
        id=row["id"],
        raw_input=row["raw_input"],
        resolved_url=row["resolved_url"],
        status=LeadStatus(row["status"]),
        error_message=row["error_message"],
        pages_scraped=json.loads(row["pages_scraped"] or "[]"),
        brief=SalesBrief.model_validate_json(row["brief_json"]) if row["brief_json"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_lead(lead_id: int) -> Optional[LeadRecord]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return _row_to_record(row) if row else None


def get_all_leads() -> List[LeadRecord]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY id DESC").fetchall()
        return [_row_to_record(r) for r in rows]
