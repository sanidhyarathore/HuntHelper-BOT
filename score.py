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
import llm

log = logging.getLogger("score")

ENGINEERING = re.compile(
    r"\b(sde|software engineer|backend|front[- ]?end|full[- ]?stack|devops|sre|"
    r"ml engineer|data engineer|data scientist|android|ios developer|qa engineer|"
    r"test engineer|smart contract engineer|solidity developer|rust developer|"
    r"protocol engineer|quant researcher|quant developer|research scientist)\b", re.I)

JUNIOR = re.compile(
    r"\b(intern|internship|fresher|trainee|graduate trainee|entry[- ]level|"
    r"telecaller|tele[- ]?sales|field sales executive|bdr|sdr)\b", re.I)

# Languages that, if genuinely required, rule the role out.
BLOCKED_LANGUAGES = re.compile(
    r"\b(japanese|mandarin|chinese|cantonese|korean|russian|german|french|"
    r"spanish|portuguese|italian|dutch|turkish|vietnamese|thai|indonesian|"
    r"arabic|hebrew|polish|swedish)\b", re.I)

# Phrases meaning it's only a bonus — don't reject on these.
LANG_OPTIONAL = re.compile(
    r"\b(nice to have|plus|bonus|preferred|advantage|desirable|optional|"
    r"a plus|would be)\b", re.I)


def _flatten(v) -> list:
    """profile.yaml lists may be flat or grouped into named sub-lists."""
    if isinstance(v, dict):
        out = []
        for sub in v.values():
            out.extend(_flatten(sub))
        return out
    if isinstance(v, list):
        out = []
        for i in v:
            out.extend(_flatten(i))
        return out
    return [v] if v else []


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

    lang = (job.get("language_required") or "").strip()
    if lang and BLOCKED_LANGUAGES.search(lang) and not LANG_OPTIONAL.search(lang):
        return f"requires {lang.split(',')[0].strip()}"

    yrs = job.get("min_years", -1)
    if isinstance(yrs, (int, float)) and 0 <= yrs <= 1:
        return f"requires only {yrs:g} yrs experience"
    if isinstance(yrs, (int, float)) and yrs > prof.get("years_experience", 4) + 7:
        return f"requires {yrs:g} yrs experience"

    targets = [str(t).lower() for t in _flatten(prof.get("target_locations", []))]
    if loc.strip() and targets:
        if not any(t in loc for t in targets) and "remote" not in loc:
            return f"location outside targets ({job.get('location') or job.get('country')})"
    return None


SYSTEM = """You are screening job posts for one specific candidate. Score fit 0-100.

The candidate's own profile is supplied below, including their target functions,
seniority bands, geographic tiers and scoring weights. Use their weights and
tiers as your guide rather than your own priors.

Anchors — spread scores across the whole range, do not cluster around 60:
  85-100  Excellent: Tier-1 function, right seniority, Tier-1 or Tier-2 geography,
          real business ownership.
  70-84   Strong: clearly worth applying. Two of {function, seniority, geography}
          are strong and nothing disqualifies it.
  55-69   Worth a look: adjacent function, or right function in a lower-tier
          geography, or scope is unclear but the company is interesting.
  35-54   Marginal: real job, wrong shape for this candidate.
  0-34    Poor: wrong function or wrong level entirely.

Judgement notes:
- Be generous where the posting is vague but the TITLE clearly matches a target
  function. Terse posts are the norm on these channels; missing detail is not
  evidence of a bad role.
- A missing location is not a rejection. Score it as if remote-ambiguous.
- The candidate has roughly 3-4 years of experience but operates at Senior
  Manager scope. Roles asking 5-7 years are still in range; do not penalise
  heavily for a modest stretch.
- Web3 and crypto roles count when they are business, growth, operations,
  strategy, partnerships, ecosystem or commercial. Engineering and quant
  research do not.

Give one short, concrete sentence of reasoning naming the deciding factor.
"""

FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "reason": {"type": "string", "description": "One short sentence."},
    },
    "required": ["score", "reason"],
}


_profile_block_cache = {}

# Personal details the scorer has no use for. Everything else goes in, because
# once the block is cached it costs ~10% and more context scores better. The
# block must also clear the 4,096-token cache floor, so trimming it is actively
# counterproductive.
PII_KEYS = {"name", "email", "phone", "linkedin"}


def _profile_block(prof: dict) -> str:
    """The candidate profile, rendered once and reused as a cached prefix.

    Must stay byte-identical across calls or the cache misses — hence sort_keys
    and the memo.
    """
    key = id(prof)
    if key not in _profile_block_cache:
        subset = {k: v for k, v in prof.items() if k not in PII_KEYS}
        _profile_block_cache[key] = (
            "CANDIDATE PROFILE (applies to every job you score — read it once, "
            "apply it to each job that follows):\n"
            + json.dumps(subset, ensure_ascii=False, indent=1, sort_keys=True))
    return _profile_block_cache[key]


def llm_score(job: dict, prof: dict) -> tuple[int, str]:
    job_only = {k: job.get(k) for k in
                ("company", "title", "location", "country", "seniority",
                 "function", "min_years", "comp", "key_skills")}
    data = llm.structured(
        [SYSTEM, _profile_block(prof)],
        "JOB TO SCORE:\n" + json.dumps(job_only, ensure_ascii=False),
        FIT_SCHEMA, name="emit_fit", cache_system=True)
    if not data:
        return 50, "scoring unavailable — review manually"
    try:
        return max(0, min(100, int(data["score"]))), str(data.get("reason", ""))
    except (KeyError, TypeError, ValueError):
        return 50, "scoring returned an unexpected shape — review manually"


def score(job: dict, prof: dict) -> tuple[int, str, bool]:
    """Returns (score, reason, auto_rejected)."""
    reason = hard_filter(job, prof)
    if reason:
        return 0, reason, True
    s, r = llm_score(job, prof)
    return s, r, False
