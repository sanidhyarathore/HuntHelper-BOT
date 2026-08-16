"""The triage surface: a bot that DMs you ranked jobs with tap-to-act buttons.

Built on Telethon so the whole project has one Telegram dependency. The bot
only ever talks to MY_USER_ID — anyone else who finds it gets ignored.
"""
import html
import logging

from telethon import TelegramClient, events, Button

import config
import db
import drafts

log = logging.getLogger("bot")

STATUS_EMOJI = {"applied": "✅", "skipped": "🗑", "later": "🔖"}


def bot_client() -> TelegramClient:
    config.require("BOT_TOKEN", "MY_USER_ID")
    return TelegramClient(config.BOT_SESSION, config.TG_API_ID, config.TG_API_HASH)


def _card(j) -> str:
    bits = [f"<b>{html.escape(j['title'] or 'Untitled role')}</b>"]
    if j["company"]:
        bits.append(f"🏢 {html.escape(j['company'])}")
    loc = j["location"] or j["country"]
    if loc:
        bits.append(f"📍 {html.escape(loc)}")
    if j["comp"]:
        bits.append(f"💰 {html.escape(j['comp'])}")
    if j["min_years"] is not None and j["min_years"] >= 0:
        bits.append(f"⏳ {j['min_years']:g}+ yrs")
    bits.append(f"\n<b>{j['fit_score']}/100</b> — {html.escape(j['fit_reason'] or '')}")
    if j["apply_type"] == "email" and j["apply_email"]:
        bits.append(f"\n📧 apply via {html.escape(j['apply_email'])}")
    elif j["apply_type"] == "telegram_bot":
        bits.append("\n🤖 applies through a Telegram bot — tap through manually")
    elif j["apply_type"] == "dm":
        bits.append("\n⚠️ asks you to DM a person — verify before sending anything")
    return "\n".join(bits)


def _buttons(j):
    row1 = []
    if j["apply_url"]:
        row1.append(Button.url("🔗 Open", j["apply_url"]))
    with db.conn() as c:
        m = c.execute("SELECT permalink FROM messages WHERE id = ?",
                      (j["message_id"],)).fetchone()
    if m and m["permalink"]:
        row1.append(Button.url("💬 Source", m["permalink"]))

    row2 = [Button.inline("✅ Applied", f"applied:{j['id']}".encode()),
            Button.inline("🗑 Skip", f"skip:{j['id']}".encode()),
            Button.inline("🔖 Later", f"later:{j['id']}".encode())]
    row3 = []
    if j["apply_type"] == "email" and j["apply_email"]:
        row3.append(Button.inline("📝 Draft email", f"draft:{j['id']}".encode()))
    row3.append(Button.inline("✍️ Write note", f"note:{j['id']}".encode()))

    return [r for r in (row1, row2, row3) if r]


async def push_new(bot, threshold=None, limit=None):
    """Send every unnotified job above the fit threshold. Returns count sent."""
    rows = db.pending_notification(
        threshold or config.NOTIFY_THRESHOLD, limit or config.MAX_NOTIFY_PER_RUN)
    for j in rows:
        try:
            await bot.send_message(config.MY_USER_ID, _card(j),
                                   buttons=_buttons(j), parse_mode="html",
                                   link_preview=False)
            db.set_status(j["id"], "notified")
        except Exception:
            log.exception("failed to push job %s", j["id"])
    return len(rows)


def register(bot: TelegramClient):
    prof = config.profile()

    @bot.on(events.CallbackQuery)
    async def _cb(event):
        if event.sender_id != config.MY_USER_ID:
            return await event.answer("Not for you.", alert=True)

        action, _, jid = event.data.decode().partition(":")
        j = db.job(int(jid))
        if not j:
            return await event.answer("Job not found.")

        if action in ("applied", "skip", "later"):
            status = {"applied": "applied", "skip": "skipped", "later": "later"}[action]
            db.set_status(j["id"], status)
            if status == "applied":
                db.log_application(j["id"], method=j["apply_type"])
            await event.edit(f"{STATUS_EMOJI[status]} <b>{status.upper()}</b> — "
                             f"{html.escape(j['title'] or '')} @ "
                             f"{html.escape(j['company'] or '?')}",
                             parse_mode="html", buttons=None)
            return await event.answer(status)

        if action == "note":
            await event.answer("Writing…")
            n = drafts.tailored_note(j, prof)
            await event.reply(
                f"<b>Subject:</b> {html.escape(n['subject'])}\n\n"
                f"<pre>{html.escape(n['body'])}</pre>", parse_mode="html")
            return

        if action == "draft":
            await event.answer("Creating Gmail draft…")
            n = drafts.tailored_note(j, prof)
            did = drafts.create_draft(j["apply_email"], n["subject"], n["body"],
                                      attach=config.CV_PATH)
            if did:
                db.log_application(j["id"], method="email", draft_id=did)
                await event.reply(
                    "📝 Draft sitting in Gmail with your CV attached. "
                    "Read it, then send.\nhttps://mail.google.com/mail/u/0/#drafts")
            else:
                await event.reply("Draft failed — check the logs and your Gmail token.")
            return

    @bot.on(events.NewMessage(pattern=r"^/(start|stats|digest|help)"))
    async def _cmd(event):
        if event.sender_id != config.MY_USER_ID:
            return
        cmd = event.raw_text.split()[0].lstrip("/")
        if cmd == "stats":
            s = db.stats()
            await event.reply(
                "\n".join(f"{k}: {v}" for k, v in s.items()))
        elif cmd == "digest":
            n = await push_new(bot)
            if not n:
                await event.reply("Nothing new above threshold.")
        else:
            await event.reply(
                "Job radar.\n"
                "/digest — push anything new above threshold\n"
                "/stats — pipeline counts\n"
                "Buttons on each card: Open, Source, Applied, Skip, Later, Draft, Note")

    return bot
