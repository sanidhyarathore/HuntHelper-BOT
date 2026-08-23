#!/usr/bin/env python3
"""Entry point.

  python run.py cycle        THE EVERYDAY ONE: fetch, score, notify, then triage
  python run.py channels     list your Telegram channels so you can pick which to watch
  python run.py stats        pipeline counts
  python run.py export       dump jobs to CSV

Less common:
  python run.py backfill     force a deep history pull
  python run.py process      extract + score only
  python run.py digest       push existing matches only
  python run.py serve        always-on mode (for a server, not a laptop)
"""
import argparse
import asyncio
import csv
import logging
import sys

# Windows consoles default to cp1252 and crash on emoji in channel titles.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config
import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run")

# These log every HTTP call and every internal SDK retry at INFO level, which
# buries our own warnings (rate limits, quota, failed messages) in noise.
# WARNING still shows anything that actually matters.
for _noisy in ("httpx", "httpx2", "httpcore", "openai", "openai._base_client"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

PROCESS_EVERY = 15 * 60   # seconds
FOLLOWUP_EVERY = 6 * 3600


async def cmd_channels(_):
    import ingest
    await ingest.list_channels()


async def cmd_backfill(args):
    import ingest
    n = await ingest.backfill(args.limit)
    log.info("stored %s new messages", n)


async def cmd_process(_):
    import pipeline
    log.info("%s", pipeline.process(limit=1000))


async def cmd_digest(_):
    import bot as botmod
    bot = botmod.bot_client()
    await bot.start(bot_token=config.BOT_TOKEN)
    n = await botmod.push_new(bot)
    log.info("pushed %s jobs", n)
    await bot.disconnect()


async def cmd_cycle(_):
    """Fetch -> process -> notify -> follow-ups, then exit.

    This is the everyday command. Safe to run any time; it picks up wherever
    it left off. Double-click runme.command (Mac) or runme.bat (Windows).
    """
    import bot as botmod
    import ingest
    import pipeline

    await ingest.catch_up()
    log.info("%s", pipeline.process(limit=2000))

    bot = botmod.bot_client()
    botmod.register(bot)
    await bot.start(bot_token=config.BOT_TOKEN)
    sent = await botmod.push_new(bot)
    nudges = await pipeline.send_followups(bot)
    log.info("pushed %s jobs, %s follow-up nudges", sent, nudges)

    print("\n" + "=" * 60)
    print(f"  {sent} new job(s) sent to your Telegram." if sent
          else "  Nothing new above threshold.")
    print("  Buttons work while this window stays open.")
    print("  Close this window (or Ctrl-C) when you're done triaging.")
    print("=" * 60 + "\n")

    await bot.run_until_disconnected()


async def cmd_stats(_):
    for k, v in db.stats().items():
        print(f"{k:>14}: {v}")


async def cmd_export(args):
    with db.conn() as c:
        rows = c.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
    if not rows:
        return print("nothing to export")
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        for r in rows:
            w.writerow(dict(r))
    print(f"wrote {len(rows)} rows to {args.out}")


async def cmd_serve(_):
    """Everything at once. This is what runs on the VPS."""
    import bot as botmod
    import ingest
    import pipeline

    user = ingest.client()
    ingest.attach_listener(user)
    await user.start()
    log.info("user client up — listening on %s channels", len(config.CHANNELS))

    bot = botmod.bot_client()
    botmod.register(bot)
    await bot.start(bot_token=config.BOT_TOKEN)
    log.info("bot up")

    async def periodic():
        elapsed = 0
        while True:
            await asyncio.sleep(PROCESS_EVERY)
            elapsed += PROCESS_EVERY
            try:
                counts = pipeline.process(limit=500)
                log.info("processed %s", counts)
                sent = await botmod.push_new(bot)
                if sent:
                    log.info("pushed %s jobs", sent)
                if elapsed % FOLLOWUP_EVERY < PROCESS_EVERY:
                    await pipeline.send_followups(bot)
            except Exception:
                log.exception("periodic loop error")

    asyncio.create_task(periodic())
    await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected())


COMMANDS = {
    "cycle": cmd_cycle, "channels": cmd_channels, "backfill": cmd_backfill,
    "process": cmd_process, "digest": cmd_digest, "stats": cmd_stats,
    "serve": cmd_serve, "export": cmd_export,
}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=COMMANDS)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", default="jobs.csv")
    args = p.parse_args()

    db.init()
    try:
        asyncio.run(COMMANDS[args.command](args))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
