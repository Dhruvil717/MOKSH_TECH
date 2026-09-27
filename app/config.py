"""
Centralized configuration, loaded from environment variables / .env file.
Keeping this in one place makes it obvious what's configurable and avoids
scattering os.getenv() calls throughout the codebase.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic").lower()

    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")

    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

    MAX_WORKERS: int = _int("MAX_WORKERS", 3)
    MAX_PAGES_PER_LEAD: int = _int("MAX_PAGES_PER_LEAD", 3)

    REQUEST_TIMEOUT: int = _int("REQUEST_TIMEOUT", 30)
    MAX_CONTENT_CHARS: int = _int("MAX_CONTENT_CHARS", 12000)
    BROWSER_TIMEOUT_MS: int = _int("BROWSER_TIMEOUT_MS", 45000)

    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/leads.db")

    USER_AGENT: str = (
        "Mozilla/5.0 (compatible; SalesIntelligenceBot/1.0; "
        "+https://example.com/bot-info)"
    )


settings = Settings()
