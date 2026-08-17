"""Tracker + filter diagnostics + market intelligence. No API calls, no cost.

    .venv\\Scripts\\python insights.py            everything
    .venv\\Scripts\\python insights.py tracker    what you applied to / skipped
    .venv\\Scripts\\python insights.py funnel     where jobs are being lost
    .venv\\Scripts\\python insights.py scores     score distribution + threshold advice
    .venv\\Scripts\\python insights.py near       good jobs sitting below your threshold
    .venv\\Scripts\\python insights.py market     what employers are actually asking for
"""
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "data" / "jobs.db"


def con():
    if not DB.exists():
        sys.exit("No database yet — run runme.bat first.")
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def head(t):
    print(f"\n{'=' * 66}\n  {t}\n{'=' * 66}")


def bar(n, total, width=34):
    return "#" * int(width * n / max(total, 1))


# ---------------------------------------------------------------- tracker
def tracker(c):
    head("TRACKER")
    counts = dict(c.execute(
        "SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    for s in ("new", "notified", "applied", "skipped", "later", "autorejected"):
        print(f"  {s:<14} {counts.get(s, 0)}")

    rows = c.execute(
        """SELECT j.company, j.title, j.location, a.applied_at, a.method,
                  a.followup_1, a.followup_2
           FROM applications a JOIN jobs j ON j.id = a.job_id
           ORDER BY a.applied_at DESC""").fetchall()
    if rows:
        print(f"\n  Applied ({len(rows)}):")
        for r in rows:
            flags = "".join(["1" if r["followup_1"] else "-",
                             "2" if r["followup_2"] else "-"])
            print(f"    {r['applied_at'][:10]}  [{flags}]  "
                  f"{(r['company'] or '?')[:22]:<22} {(r['title'] or '')[:38]}")
        print("    ([12] = follow-up nudges already sent)")

    later = c.execute(
        "SELECT company, title, fit_score FROM jobs WHERE status='later' "
        "ORDER BY fit_score DESC").fetchall()
    if later:
        print(f"\n  Saved for later ({len(later)}):")
        for r in later:
            print(f"    {r['fit_score']:>3}  {(r['company'] or '?')[:22]:<22} "
                  f"{(r['title'] or '')[:38]}")


# ---------------------------------------------------------------- funnel
def funnel(c):
    head("FUNNEL — where things are being lost")
    msgs = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    done = c.execute("SELECT COUNT(*) FROM messages WHERE processed=1").fetchone()[0]
    jobs = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    rej = c.execute("SELECT COUNT(*) FROM jobs WHERE status='autorejected'").fetchone()[0]
    scam = c.execute("SELECT COUNT(*) FROM jobs WHERE scam=1").fetchone()[0]
    surfaced = c.execute(
        "SELECT COUNT(*) FROM jobs WHERE status NOT IN ('autorejected')").fetchone()[0]

    print(f"  messages stored           {msgs}")
    print(f"  messages analysed         {done}")
    print(f"  -> survived prefilter+LLM {jobs}   (rest weren't job posts)")
    print(f"     of those, scam-flagged {scam}")
    print(f"     hard-rejected          {rej}")
    print(f"     surfaced for scoring   {surfaced}")

    if rej:
        head("WHY JOBS WERE HARD-REJECTED")
        reasons = collections.Counter()
        for (r,) in c.execute(
                "SELECT fit_reason FROM jobs WHERE status='autorejected'").fetchall():
            key = re.sub(r"\(.*?\)", "", r or "?").strip()
            key = re.sub(r"requires only [\d.]+ yrs", "requires only N yrs", key)
            key = re.sub(r"requires [\d.]+ yrs", "requires N yrs", key)
            reasons[key] += 1
        for r, n in reasons.most_common(15):
            print(f"  {n:>4}  {bar(n, rej):<34} {r}")
        print("\n  Each line above is a rule in score.py hard_filter().")
        print("  If a line looks wrong, that rule is what to loosen.")


# ---------------------------------------------------------------- scores
def scores(c):
    head("SCORE DISTRIBUTION — pick your threshold")
    rows = c.execute(
        "SELECT fit_score FROM jobs WHERE status != 'autorejected' "
        "AND fit_score IS NOT NULL").fetchall()
    if not rows:
        return print("  Nothing scored yet.")
    vals = [r[0] for r in rows]
    buckets = collections.Counter((v // 10) * 10 for v in vals)
    for lo in range(0, 100, 10):
        n = buckets.get(lo, 0)
        print(f"  {lo:>3}-{lo+9:<3} {n:>4}  {bar(n, max(buckets.values()))}")

    print(f"\n  Total scored: {len(vals)}")
    for t in (40, 45, 50, 55, 60, 65, 70):
        n = sum(1 for v in vals if v >= t)
        print(f"  threshold {t}: {n} jobs would reach you")
    print("\n  Set NOTIFY_THRESHOLD in .env to whatever gives you 15-30 per week.")


# ---------------------------------------------------------------- near misses
def near(c, lo=35, hi=None):
    import os
    hi = hi or int(os.getenv("NOTIFY_THRESHOLD", "60"))
    head(f"NEAR MISSES — scored {lo}-{hi - 1}, never shown to you")
    rows = c.execute(
        """SELECT fit_score, company, title, location, country, fit_reason
           FROM jobs WHERE status='new' AND scam=0
           AND fit_score >= ? AND fit_score < ?
           ORDER BY fit_score DESC LIMIT 40""", (lo, hi)).fetchall()
    if not rows:
        return print("  None.")
    for r in rows:
        loc = (r["location"] or r["country"] or "?")[:18]
        print(f"  {r['fit_score']:>3}  {(r['company'] or '?')[:20]:<20} "
              f"{(r['title'] or '')[:34]:<34} {loc}")
        print(f"       {(r['fit_reason'] or '')[:90]}")
    print("\n  If several of these look good, your threshold is too high.")
    print("  If they all look bad, the threshold is right and the channels are the problem.")


# ---------------------------------------------------------------- market
SKILLS = [
    "sql", "python", "excel", "tableau", "looker", "power bi", "dbt", "snowflake",
    "salesforce", "hubspot", "crm", "gtm", "go-to-market", "p&l", "pricing",
    "monetization", "forecasting", "a/b test", "experimentation", "analytics",
    "okr", "kpi", "saas", "b2b", "b2c", "marketplace", "supply chain",
    "partnerships", "business development", "account management", "revops",
    "revenue operations", "strategy", "consulting", "mba", "defi", "tokenomics",
    "solidity", "smart contract", "web3", "blockchain", "trading", "market making",
    "compliance", "kyc", "aml", "fintech", "payments", "stablecoin",
    "community", "ecosystem", "growth", "product management", "operations",
]


def market(c):
    head("MARKET INTEL — what these channels are actually hiring for")
    rows = c.execute(
        """SELECT j.title, j.company, j.location, j.country, j.function,
                  j.seniority, j.min_years, j.raw, m.text
           FROM jobs j LEFT JOIN messages m ON m.id = j.message_id
           WHERE j.scam = 0""").fetchall()
    if not rows:
        return print("  Nothing to analyse yet.")
    print(f"  Based on {len(rows)} job posts.\n")

    for label, key in (("Top functions", "function"),
                       ("Top seniority", "seniority")):
        cnt = collections.Counter((r[key] or "?") for r in rows)
        print(f"  {label}:")
        for v, n in cnt.most_common(8):
            print(f"    {n:>4}  {v}")
        print()

    locs = collections.Counter()
    for r in rows:
        v = (r["location"] or r["country"] or "").strip()
        if v:
            locs[v.split(",")[0].strip()[:24]] += 1
    print("  Top locations:")
    for v, n in locs.most_common(12):
        print(f"    {n:>4}  {v}")

    # Prefer the model's own extracted skills where present (newer records),
    # and fall back to word-frequency over the raw text for older ones.
    tally = collections.Counter()
    for r in rows:
        try:
            ks = json.loads(r["raw"] or "{}").get("key_skills") or []
            for s in ks:
                if isinstance(s, str) and s.strip():
                    tally[s.strip().lower()[:34]] += 1
        except Exception:
            pass

    if tally:
        print("\n  Skills employers asked for (extracted per posting):")
        for s, n in tally.most_common(30):
            print(f"    {n:>5}  {s}")

    text = " ".join((r["text"] or "").lower() for r in rows)
    hits = sorted(((s, text.count(s)) for s in SKILLS), key=lambda x: -x[1])
    hits = [h for h in hits if h[1] > 0][:30]
    if hits:
        print("\n  Keyword frequency across all posting text:")
        for s, n in hits:
            print(f"    {n:>5}  {s}")

    print("\n  Read this two ways: high-frequency skills you lack are worth adding,")
    print("  and high-frequency skills you HAVE are worth naming explicitly on your CV.")


def cache(_c=None):
    """Will prompt caching actually engage? Free to check, silent if it won't."""
    head("PROMPT CACHE READINESS")
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import config as cfg
        import score as sc
        block = sc._profile_block(cfg.profile())
    except Exception as e:
        return print(f"  Could not load profile: {e}")

    tokens = len(block) // 3
    floor = getattr(cfg, "CACHE_MIN_TOKENS", 4096)
    print(f"  Profile block: {len(block):,} chars, roughly {tokens:,} tokens")
    print(f"  Cache floor for your model: {floor:,} tokens")
    if tokens >= floor:
        print(f"\n  OK — caching will engage. After the first call of each run,")
        print(f"  those ~{tokens:,} tokens bill at about 10%.")
    else:
        short = floor - tokens
        print(f"\n  TOO SMALL by ~{short:,} tokens. Caching will be silently")
        print(f"  ignored and you will pay full price on every scoring call.")
        print(f"  Fix: add more detail to profile.yaml — achievements, role")
        print(f"  signals, priority rules. Bigger costs you almost nothing once")
        print(f"  cached, and scores better.")


def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if which == "cache":
        return cache()
    c = con()
    fns = {"tracker": tracker, "funnel": funnel, "scores": scores,
           "near": near, "market": market}
    if which == "all":
        for f in (tracker, funnel, scores, near, market):
            f(c)
        cache()
    elif which in fns:
        fns[which](c)
    else:
        sys.exit(__doc__)
    print()


if __name__ == "__main__":
    main()
