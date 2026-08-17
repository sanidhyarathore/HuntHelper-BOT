"""Central config. Everything tunable lives here or in .env / profile.yaml."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "assets").mkdir(exist_ok=True)

# ---- Telegram (user account, for READING channels) ----
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION = str(ROOT / "data" / "user.session")

# ---- Telegram (bot, for the triage DM) ----
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_SESSION = str(ROOT / "data" / "bot.session")
MY_USER_ID = int(os.getenv("MY_USER_ID", "0"))  # your own numeric Telegram id

# ---- LLM provider ----
# anthropic (default). Swap to gemini/groq for a free tier if you ever want to;
# see README. Only LLM_PROVIDER, LLM_API_KEY and the models need to change.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "") or ANTHROPIC_API_KEY

_DEFAULT_MODELS = {
    "anthropic":  ("claude-haiku-4-5-20251001", "claude-sonnet-5"),
    "gemini":     ("gemini-2.5-flash-lite", "gemini-2.5-flash"),
    "groq":       ("llama-3.3-70b-versatile", "llama-3.3-70b-versatile"),
    "openai":     ("gpt-4.1-mini", "gpt-4.1"),
    "openrouter": ("google/gemini-2.5-flash-lite", "google/gemini-2.5-flash"),
}
_d = _DEFAULT_MODELS.get(LLM_PROVIDER, _DEFAULT_MODELS["anthropic"])
MODEL_EXTRACT = os.getenv("MODEL_EXTRACT", "") or _d[0]   # cheap, high volume
MODEL_WRITE = os.getenv("MODEL_WRITE", "") or _d[1]       # tailored notes

# Seconds between API calls. A paid Anthropic key needs almost no spacing;
# free tiers elsewhere cap around 15/minute, hence the 4s fallback.
_default_interval = "0.4" if LLM_PROVIDER == "anthropic" else "4"
LLM_MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL", _default_interval))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "5"))
LLM_BACKOFF_BASE = float(os.getenv("LLM_BACKOFF_BASE", "8"))

# ---- Storage ----
DB_PATH = str(ROOT / "data" / "jobs.db")
CV_PATH = os.getenv("CV_PATH", str(ROOT / "assets" / "cv.pdf"))

# ---- Pipeline tunables ----
NOTIFY_THRESHOLD = int(os.getenv("NOTIFY_THRESHOLD", "60"))  # fit score 0-100
BACKFILL_LIMIT = int(os.getenv("BACKFILL_LIMIT", "200"))     # msgs per channel on first run
MAX_NOTIFY_PER_RUN = int(os.getenv("MAX_NOTIFY_PER_RUN", "12"))
FOLLOWUP_DAYS = (5, 12)

# Channels to watch. Populate with `python run.py channels` output.
# Accepts @usernames or numeric ids.
CHANNELS = [c.strip() for c in os.getenv("CHANNELS", "").split(",") if c.strip()]

# Messages lacking ALL of these are dropped before hitting the LLM (saves ~70% of cost).
JOB_KEYWORDS = {
    "hiring", "hiring!", "we're hiring", "job", "jobs", "role", "roles", "vacancy",
    "vacancies", "opening", "openings", "opportunity", "apply", "position",
    "recruit", "recruiting", "ctc", "lpa", "experience", "yoe", "exp:", "salary",
    "jd", "join us", "career", "careers", "resume", "cv",
}

# Instant-kill patterns: obvious spam / CV-harvesting.
SCAM_PATTERNS = [
    r"registration\s+fee", r"pay\s*(rs\.?|₹|inr)\s*\d", r"security\s+deposit",
    r"unlimited\s+earning", r"work\s+from\s+home.*\b(daily|weekly)\s+payout",
    r"whats?app\s*(me|:|\+?\d{10})", r"telegram\s*:?\s*@\w+\s*for\s*cv",
    r"earn\s*(rs\.?|₹)\s*\d+.*per\s*(day|hour)",
]


_profile = None


def profile() -> dict:
    """Loaded once per process so the cached prompt prefix stays identical."""
    global _profile
    if _profile is None:
        with open(ROOT / "profile.yaml", encoding="utf-8") as f:
            _profile = yaml.safe_load(f)
    return _profile


def require(*names):
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(f"Missing config: {', '.join(missing)}. Check your .env file.")

# Prompt caching. Haiku 4.5 needs a 4,096-token prefix before caching engages;
# below that the API silently ignores it. Raise if you switch to a model with a
# higher floor.
CACHE_MIN_TOKENS = int(os.getenv("CACHE_MIN_TOKENS", "4096"))
