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

# ---- Anthropic ----
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL_EXTRACT = os.getenv("MODEL_EXTRACT", "claude-haiku-4-5-20251001")  # cheap, high volume
MODEL_WRITE = os.getenv("MODEL_WRITE", "claude-sonnet-5")               # tailored notes

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


def profile() -> dict:
    with open(ROOT / "profile.yaml") as f:
        return yaml.safe_load(f)


def require(*names):
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(f"Missing config: {', '.join(missing)}. Check your .env file.")
