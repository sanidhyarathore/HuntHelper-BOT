"""Turn a messy channel post into a structured job record.

Cost control: a regex prefilter kills ~70% of traffic (memes, "good morning",
course ads) before anything reaches the API. Only survivors hit Haiku.
"""
import hashlib
import json
import logging
import re

import config
import llm

log = logging.getLogger("extract")

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
        "language_required": {"type": "string",
                              "description": "Any NON-ENGLISH language the posting requires or "
                                             "strongly prefers (e.g. Japanese, Mandarin, Korean, "
                                             "Arabic, German). Empty string if English-only or "
                                             "no language mentioned."},
        "key_skills": {"type": "array", "items": {"type": "string"},
                       "description": "Up to 8 concrete skills, tools or qualifications the "
                                      "posting asks for."},
        "scam": {"type": "boolean",
                 "description": "True for fee requests, CV harvesting, personal WhatsApp numbers, "
                                "no named employer, or unrealistic earnings claims."},
        "scam_reason": {"type": "string"},
    },
    "required": ["is_job_post", "company", "title", "location", "country", "seniority",
                 "function", "min_years", "comp", "apply_type", "apply_url", "apply_email",
                 "language_required", "key_skills", "scam", "scam_reason"],
}

SYSTEM = """You extract job postings from Telegram channel messages.

Rules:
- Extract only what is literally present. Never invent a company, salary or location.
- Use "" for missing strings and -1 for a missing min_years.
- apply_type: "url" if there is a link or an inline button link; "email" if it says to
  mail a CV to an address; "telegram_bot" if it says to apply via a bot or a callback
  button; "dm" if it says to DM a person; otherwise "unknown".
- If several roles are listed in one message, extract the FIRST one only.
- language_required: only fill this if the posting genuinely requires or strongly
  prefers a non-English language (business-level Japanese, native Mandarin, etc).
  Leave it empty for "nice to have" mentions and for English.
- If the message is not a specific job advert (news, memes, courses, "we help you get
  placed", general career advice), set is_job_post to false and leave the rest blank.
- Be aggressive on scam detection. Indian job channels are full of CV-harvesting.
"""


def looks_like_job(text: str) -> bool:
    """Cheap gate before spending a token."""
    low = (text or "").lower()
    if len(low) < 40:
        return False
    return any(k in low for k in config.JOB_KEYWORDS)


# Titles that hard_filter would reject anyway. Catching them here saves the
# extraction call entirely. Deliberately conservative: only fires on the first
# line (which is nearly always the title) of a SHORT, single-role post, so a
# digest listing several jobs is never dropped by mistake.
NEGATIVE_TITLE = re.compile(
    r"\b(solidity|rust|golang|smart[- ]contract|protocol|backend|front[- ]?end|"
    r"full[- ]?stack|software|blockchain|devops|sre|qa|android|ios|mobile|"
    r"security|infrastructure|platform)\s+(engineer|developer|dev)\b"
    r"|\b(sde|sre)\s*[-–:]?\s*(i{1,3}|[123])?\b"
    r"|\b(software|backend|frontend|fullstack|full[- ]stack)\s+engineer\b"
    r"|\bdata\s+(scientist|engineer)\b"
    r"|\bquant\s+(researcher|developer|trader)\b"
    r"|\b(smart[- ]contract|protocol|security)\s+auditor\b", re.I)

MULTI_ROLE = re.compile(r"\b(\d\.|•|\u2022)\s*\w+.*\n.*\b(\d\.|•|\u2022)\s*\w+", re.S)


def negative_prefilter(text: str) -> str | None:
    """Return a reason to skip without calling the API, or None."""
    t = (text or "").strip()
    if len(t) > 900 or MULTI_ROLE.search(t[:1200]):
        return None  # probably a digest of several roles — let the model read it
    first = "\n".join(t.splitlines()[:2])[:160]
    m = NEGATIVE_TITLE.search(first)
    return f"engineering title in headline: {m.group(0)}" if m else None


_URL_RE = re.compile(r"https?://\S+")
_NONWORD_RE = re.compile(r"[^a-z0-9 ]+")


def content_hash(text: str) -> str:
    """Fingerprint of a posting's wording, ignoring links and decoration.

    Two channels reposting the same job produce the same hash, so the second
    copy never reaches the API.
    """
    t = (text or "").lower()
    t = _URL_RE.sub(" ", t)
    t = _NONWORD_RE.sub(" ", t)
    t = " ".join(t.split())[:600]
    return hashlib.sha1(t.encode()).hexdigest()[:20] if len(t) >= 40 else ""


def regex_scam(text: str) -> str | None:
    low = (text or "").lower()
    for pat in config.SCAM_PATTERNS:
        if re.search(pat, low):
            return f"matched pattern: {pat}"
    return None


def extract(text: str, buttons: list) -> dict | None:
    """Return a structured dict, or None if this genuinely isn't a job post.

    Raises llm.LLMCallFailed / llm.DailyQuotaExceeded on a real API failure —
    it does NOT swallow those into None. A None here means "the model looked
    at this and decided it's not a job post," which the pipeline is allowed to
    mark processed and forget. A raised exception means "we don't know yet,"
    which the pipeline must NOT mark processed, so it gets retried next run.
    """
    if not looks_like_job(text):
        return None

    btn_desc = "\n".join(
        f"- [{b['text']}] -> {b['value'] or '(bot callback, no URL)'}" for b in (buttons or [])
    )
    content = f"MESSAGE:\n{text[:4000]}"
    if btn_desc:
        content += f"\n\nINLINE BUTTONS:\n{btn_desc}"

    data = llm.structured(SYSTEM, content, JOB_SCHEMA, name="emit_job",
                          provider=config.PROVIDER_EXTRACT, model=config.MODEL_EXTRACT)

    if not data.get("is_job_post"):
        return None

    # Normalise: non-Anthropic providers sometimes omit optional keys.
    for k, default in (("company", ""), ("title", ""), ("location", ""), ("country", ""),
                       ("seniority", "Unknown"), ("function", ""), ("comp", ""),
                       ("apply_type", "unknown"), ("apply_url", ""), ("apply_email", ""),
                       ("scam_reason", "")):
        data.setdefault(k, default)
    data.setdefault("min_years", -1)
    try:
        data["min_years"] = float(data["min_years"])
    except (TypeError, ValueError):
        data["min_years"] = -1

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
