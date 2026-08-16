"""Turn a messy channel post into a structured job record.

Cost control: a regex prefilter kills ~70% of traffic (memes, "good morning",
course ads) before anything reaches the API. Only survivors hit Haiku.
"""
import json
import logging
import re

from anthropic import Anthropic

import config

log = logging.getLogger("extract")
_client = None

JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "is_job_post": {"type": "boolean",
                        "description": "True only if this advertises a specific open role."},
        "company": {"type": "string", "description": "Hiring company. '' if not stated."},
        "title": {"type": "string"},
        "location": {"type": "string", "description": "City/region as written. '' if absent."},
        "country": {"type": "string", "description": "Best guess: India, UAE, Remote, etc."},
        "seniority": {"type": "string",
                      "enum": ["Intern", "Entry", "Associate", "Manager", "Senior Manager",
                               "Director", "VP+", "Unknown"]},
        "function": {"type": "string",
                     "description": "e.g. Sales, Growth, Operations, Strategy, Engineering, Product"},
        "min_years": {"type": "number", "description": "Minimum years of experience. -1 if absent."},
        "comp": {"type": "string", "description": "Salary/CTC as written. '' if absent."},
        "apply_type": {"type": "string",
                       "enum": ["url", "email", "telegram_bot", "dm", "unknown"]},
        "apply_url": {"type": "string"},
        "apply_email": {"type": "string"},
        "scam": {"type": "boolean",
                 "description": "True for fee requests, CV harvesting, personal WhatsApp numbers, "
                                "no named employer, or unrealistic earnings claims."},
        "scam_reason": {"type": "string"},
    },
    "required": ["is_job_post", "company", "title", "location", "country", "seniority",
                 "function", "min_years", "comp", "apply_type", "apply_url", "apply_email",
                 "scam", "scam_reason"],
}

SYSTEM = """You extract job postings from Telegram channel messages.

Rules:
- Extract only what is literally present. Never invent a company, salary or location.
- Use "" for missing strings and -1 for a missing min_years.
- apply_type: "url" if there is a link or an inline button link; "email" if it says to
  mail a CV to an address; "telegram_bot" if it says to apply via a bot or a callback
  button; "dm" if it says to DM a person; otherwise "unknown".
- If several roles are listed in one message, extract the FIRST one only.
- If the message is not a specific job advert (news, memes, courses, "we help you get
  placed", general career advice), set is_job_post to false and leave the rest blank.
- Be aggressive on scam detection. Indian job channels are full of CV-harvesting.
"""


def anthropic() -> Anthropic:
    global _client
    if _client is None:
        config.require("ANTHROPIC_API_KEY")
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def looks_like_job(text: str) -> bool:
    """Cheap gate before spending a token."""
    low = (text or "").lower()
    if len(low) < 40:
        return False
    return any(k in low for k in config.JOB_KEYWORDS)


def regex_scam(text: str) -> str | None:
    low = (text or "").lower()
    for pat in config.SCAM_PATTERNS:
        if re.search(pat, low):
            return f"matched pattern: {pat}"
    return None


def extract(text: str, buttons: list) -> dict | None:
    """Return a structured dict, or None if this isn't a job post."""
    if not looks_like_job(text):
        return None

    btn_desc = "\n".join(
        f"- [{b['text']}] -> {b['value'] or '(bot callback, no URL)'}" for b in (buttons or [])
    )
    content = f"MESSAGE:\n{text[:4000]}"
    if btn_desc:
        content += f"\n\nINLINE BUTTONS:\n{btn_desc}"

    try:
        resp = anthropic().messages.create(
            model=config.MODEL_EXTRACT,
            max_tokens=1024,
            system=SYSTEM,
            tools=[{"name": "emit_job",
                    "description": "Emit the structured job posting.",
                    "input_schema": JOB_SCHEMA}],
            tool_choice={"type": "tool", "name": "emit_job"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception:
        log.exception("extraction API call failed")
        return None

    for block in resp.content:
        if block.type == "tool_use":
            data = dict(block.input)
            if not data.get("is_job_post"):
                return None
            # Belt and braces: regex scam check on top of the model's judgement.
            if not data.get("scam"):
                r = regex_scam(text)
                if r:
                    data["scam"], data["scam_reason"] = True, r
            # Fall back to the first URL button if the model missed the link.
            if not data.get("apply_url"):
                for b in (buttons or []):
                    if b.get("kind") == "url" and b.get("value"):
                        data["apply_url"] = b["value"]
                        if data.get("apply_type") in ("unknown", ""):
                            data["apply_type"] = "url"
                        break
            return data
    return None
