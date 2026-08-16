"""Score a job 0-100 against profile.yaml.

Two stages on purpose:
  1. Hard rules (free, instant, auditable) reject the obvious no's.
  2. Only survivors get an LLM judgement call on the genuinely ambiguous ones.
This keeps cost low AND keeps the rejection reasons explainable, which matters
when you're tuning the filter in week one.
"""
import json
import logging
import re

import config
import extract

log = logging.getLogger("score")

ENGINEERING = re.compile(
    r"\b(sde|software engineer|backend|front[- ]?end|full[- ]?stack|devops|sre|"
    r"ml engineer|data engineer|android|ios developer|qa engineer|test engineer)\b", re.I)

JUNIOR = re.compile(
    r"\b(intern|internship|fresher|trainee|graduate trainee|entry[- ]level|"
    r"telecaller|tele[- ]?sales|field sales executive|bdr|sdr)\b", re.I)


def hard_filter(job: dict, prof: dict) -> str | None:
    """Return a rejection reason, or None to pass through."""
    title = job.get("title", "") or ""
    loc = f"{job.get('location','')} {job.get('country','')}".lower()

    if job.get("scam"):
        return "flagged as scam"
    if JUNIOR.search(title):
        return "junior / IC sales title"
    if ENGINEERING.search(title):
        return "software engineering role"
    if job.get("seniority") in ("Intern", "Entry"):
        return "entry-level seniority"

    yrs = job.get("min_years", -1)
    if isinstance(yrs, (int, float)) and 0 <= yrs <= 2:
        return f"requires only {yrs} yrs experience"
    if isinstance(yrs, (int, float)) and yrs > prof.get("years_experience", 6) + 6:
        return f"requires {yrs} yrs experience"

    targets = [t.lower() for t in prof.get("target_locations", [])]
    if loc.strip() and targets:
        if not any(t in loc for t in targets) and "remote" not in loc:
            return f"location outside targets ({job.get('location') or job.get('country')})"
    return None


SYSTEM = """You are screening job posts for one specific candidate. Score fit 0-100.

Anchors:
  85-100  Strong: right function, right seniority, and in a target location
          (UAE strongly preferred), at a credible company.
  65-84   Worth applying: two of the three above hold.
  40-64   Marginal: adjacent function or a step sideways in India.
  0-39    Poor: wrong function, wrong level, or unattractive location.

Weight location heavily — UAE relocation is the candidate's primary goal.
Penalise vague posts with no named company. Be decisive; do not cluster everything
around 60. Give one short sentence of reasoning, concrete and specific.
"""

FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string", "description": "One short sentence."},
    },
    "required": ["score", "reason"],
}


def llm_score(job: dict, prof: dict) -> tuple[int, str]:
    payload = {
        "candidate": {
            "headline": prof.get("headline"),
            "years_experience": prof.get("years_experience"),
            "target_functions": prof.get("target_functions"),
            "target_seniority": prof.get("target_seniority"),
            "location_notes": prof.get("location_notes"),
            "bonus_signals": prof.get("bonus_signals"),
            "exclusions": prof.get("exclusions"),
        },
        "job": {k: job.get(k) for k in
                ("company", "title", "location", "country", "seniority",
                 "function", "min_years", "comp")},
    }
    try:
        resp = extract.anthropic().messages.create(
            model=config.MODEL_EXTRACT,
            max_tokens=512,
            system=SYSTEM,
            tools=[{"name": "emit_fit", "description": "Emit the fit score.",
                    "input_schema": FIT_SCHEMA}],
            tool_choice={"type": "tool", "name": "emit_fit"},
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        for b in resp.content:
            if b.type == "tool_use":
                return int(b.input["score"]), b.input["reason"]
    except Exception:
        log.exception("scoring failed")
    return 50, "scoring unavailable — review manually"


def score(job: dict, prof: dict) -> tuple[int, str, bool]:
    """Returns (score, reason, auto_rejected)."""
    reason = hard_filter(job, prof)
    if reason:
        return 0, reason, True
    s, r = llm_score(job, prof)
    return s, r, False
