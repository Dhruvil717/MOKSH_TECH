"""
LLM analysis layer. Two things matter here more than anything else:

1. Provider-agnostic interface, so swapping Anthropic <-> OpenAI is a config
   change, not a code change.
2. Strict, validated JSON output. We do NOT trust the model's text response
   as-is: we parse it, validate it against the SalesBrief pydantic schema,
   and retry once with the validation error fed back to the model if it
   fails. This is what "reliable evaluation" means in practice for an LLM
   step embedded in a data pipeline.
"""
import json
import re
from abc import ABC, abstractmethod
from typing import Optional

from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_fixed

from app.config import settings
from app.models import SalesBrief

SYSTEM_PROMPT = """You are a B2B sales research analyst. You will be given raw text \
scraped from a company's website. Your job is to produce a strict, structured \
sales brief for a sales rep who is about to research this company before a call.

Definitions you MUST apply consistently:
- "B2B qualified" means the company's PRIMARY customer base is other \
businesses/organizations (it sells to companies, not to individual consumers \
for personal/household use). A residential-focused contractor (e.g. a roofer \
who mainly re-roofs homeowners' houses) is NOT B2B qualified even though it is \
technically a business, UNLESS the site's own content shows it explicitly and \
substantially targets commercial, industrial, property-management, or other \
business clients. Be skeptical: do not default to "yes". If evidence is mixed \
or absent, prefer "no" and explain the ambiguity in qualification_reason.
- Never invent facts (pricing, client names, certifications, awards, years in \
business) that are not present or clearly implied in the provided text. If the \
text is too thin to know something with confidence, say so plainly rather than \
guessing, and set confidence to "low".
- The 3 sales_questions must be specific to what THIS company actually said \
about itself (not generic boilerplate questions that could apply to anyone).

Respond with ONLY a single JSON object -- no markdown fences, no commentary, no \
preamble or trailing text. The JSON object must have exactly these keys:
{
  "company_overview": string (2-4 sentences),
  "core_product_or_service": string,
  "target_customer": string,
  "is_b2b_qualified": boolean,
  "qualification_reason": string (1-2 sentences),
  "sales_questions": [string, string, string],
  "confidence": "low" | "medium" | "high"
}"""

USER_PROMPT_TEMPLATE = """Company identifier as given to us: {raw_input}
Resolved website: {url}

--- SCRAPED WEBSITE CONTENT (boilerplate/nav/footer already stripped) ---
{content}
--- END SCRAPED CONTENT ---

Produce the JSON sales brief now."""


class LLMProviderError(RuntimeError):
    pass


class BaseLLMClient(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the raw text completion from the model."""


class AnthropicClient(BaseLLMClient):
    def __init__(self):
        if not settings.ANTHROPIC_API_KEY:
            raise LLMProviderError(
                "ANTHROPIC_API_KEY is not set. Add it to your .env file."
            )
        import anthropic
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def complete(self, system: str, user: str) -> str:
        response = self._client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=1000,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")


class OpenAIClient(BaseLLMClient):
    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise LLMProviderError(
                "OPENAI_API_KEY is not set. Add it to your .env file."
            )
        from openai import OpenAI
        self._client = OpenAI(api_key=settings.OPENAI_API_KEY)

    def complete(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class GroqClient(BaseLLMClient):
    """
    Groq hosts open-weight models (e.g. Llama 3.3) behind an
    OpenAI-compatible API, so we reuse the openai SDK and just point it at
    Groq's base URL. We don't force response_format="json_object" here since
    support varies by model on Groq's side -- the strict system prompt plus
    _extract_json()/retry logic already handle stray markdown fences.
    """

    def __init__(self):
        if not settings.GROQ_API_KEY:
            raise LLMProviderError(
                "GROQ_API_KEY is not set. Add it to your .env file."
            )
        from openai import OpenAI
        self._client = OpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1",
        )

    def complete(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=settings.GROQ_MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


def _get_client() -> BaseLLMClient:
    if settings.LLM_PROVIDER == "openai":
        return OpenAIClient()
    if settings.LLM_PROVIDER == "groq":
        return GroqClient()
    return AnthropicClient()


def _extract_json(text: str) -> dict:
    """Models occasionally wrap JSON in markdown fences despite instructions;
    strip those defensively before parsing."""
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1:
        text = text[brace_start: brace_end + 1]
    return json.loads(text)


@retry(stop=stop_after_attempt(2), wait=wait_fixed(1), reraise=True)
def _call_and_validate(client: BaseLLMClient, system: str, user: str) -> SalesBrief:
    raw = client.complete(system, user)
    data = _extract_json(raw)
    return SalesBrief.model_validate(data)


def generate_sales_brief(raw_input: str, url: str, content: str) -> SalesBrief:
    """
    Calls the configured LLM and returns a validated SalesBrief.
    On a validation/parsing failure, retries once with the error appended
    to the prompt so the model can self-correct.
    """
    client = _get_client()
    user_prompt = USER_PROMPT_TEMPLATE.format(
        raw_input=raw_input, url=url, content=content or "(no content extracted)"
    )

    try:
        return _call_and_validate(client, SYSTEM_PROMPT, user_prompt)
    except (json.JSONDecodeError, ValidationError) as first_error:
        repair_prompt = (
            user_prompt
            + f"\n\nYour previous response was invalid ({first_error}). "
            "Return ONLY the corrected JSON object, matching the schema exactly."
        )
        raw = client.complete(SYSTEM_PROMPT, repair_prompt)
        data = _extract_json(raw)
        return SalesBrief.model_validate(data)
