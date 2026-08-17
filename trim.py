"""Skip old messages so you don't pay to analyse dead job posts.

    .venv\\Scripts\\python trim.py            <- report only, changes nothing
    .venv\\Scripts\\python trim.py 21         <- skip anything older than 21 days

Marks old messages as already-processed. They stay in the database (so nothing
is lost and nothing gets re-downloaded), they just never reach the API.
"""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "data" / "jobs.db"

# Rough per-message cost: one extraction call, plus a scoring call for the
# ones that turn out to be real jobs. Haiku, ballpark.
COST_PER_MSG = 0.0018


def main():
    if not DB.exists():
        sys.exit("No database yet — run runme.bat at least once first.")

    days = None
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            sys.exit("Usage: python trim.py [days]   e.g.  python trim.py 21")

    c = sqlite3.connect(DB)
    pending = c.execute("SELECT COUNT(*) FROM messages WHERE processed = 0").fetchone()[0]

    print(f"\n{pending} messages waiting to be analysed.")
    print(f"Roughly {pending * 2.2 / 60:.0f} minutes and ~${pending * COST_PER_MSG:.2f} "
          f"if you process all of them.\n")

    if pending == 0:
        return

    print("Age breakdown of what's pending:")
    for label, d in (("last 7 days", 7), ("last 14 days", 14),
                     ("last 21 days", 21), ("last 30 days", 30)):
        n = c.execute(
            "SELECT COUNT(*) FROM messages WHERE processed = 0 "
            "AND posted_at >= datetime('now', ?)", (f"-{d} days",)).fetchone()[0]
        print(f"  {label:<14} {n:>5} messages   ~${n * COST_PER_MSG:>5.2f}")

    if days is None:
        print("\nTo skip everything older than 21 days, run:")
        print("  .venv\\Scripts\\python trim.py 21")
        return

    n = c.execute(
        "UPDATE messages SET processed = 1 WHERE processed = 0 "
        "AND (posted_at IS NULL OR posted_at < datetime('now', ?))",
        (f"-{days} days",)).rowcount
    c.commit()
    left = c.execute("SELECT COUNT(*) FROM messages WHERE processed = 0").fetchone()[0]
    print(f"\nSkipped {n} messages older than {days} days.")
    print(f"{left} left to analyse — about {left * 2.2 / 60:.0f} minutes, "
          f"~${left * COST_PER_MSG:.2f}.")
    print("\nNow run runme.bat.")


if __name__ == "__main__":
    main()
