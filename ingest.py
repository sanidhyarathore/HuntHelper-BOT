"""Reads job channels via your own Telegram account (MTProto / Telethon).

Read-only by design. It never posts, never joins, never clicks. That keeps the
account risk essentially nil — you are doing what the official client does.
"""
import json
import logging

from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat, KeyboardButtonUrl, KeyboardButtonCallback

import config
import db

log = logging.getLogger("ingest")


def client() -> TelegramClient:
    config.require("TG_API_ID", "TG_API_HASH")
    return TelegramClient(config.TG_SESSION, config.TG_API_ID, config.TG_API_HASH)


def _buttons(msg) -> list:
    """Flatten a message's inline keyboard into [{text, kind, value}]."""
    out = []
    if not getattr(msg, "reply_markup", None):
        return out
    for row in getattr(msg.reply_markup, "rows", []) or []:
        for b in row.buttons:
            if isinstance(b, KeyboardButtonUrl):
                out.append({"text": b.text, "kind": "url", "value": b.url})
            elif isinstance(b, KeyboardButtonCallback):
                # Callback buttons talk to a bot. We record but never auto-click.
                out.append({"text": b.text, "kind": "callback", "value": None})
            else:
                out.append({"text": getattr(b, "text", "?"), "kind": "other", "value": None})
    return out


def _permalink(chat, msg) -> str:
    uname = getattr(chat, "username", None)
    if uname:
        return f"https://t.me/{uname}/{msg.id}"
    cid = str(chat.id).replace("-100", "")
    return f"https://t.me/c/{cid}/{msg.id}"


def _store(chat, msg) -> bool:
    text = msg.message or ""
    btns = _buttons(msg)
    if not text.strip() and not btns:
        return False
    return db.save_message(
        channel=str(getattr(chat, "username", None) or chat.id),
        channel_title=getattr(chat, "title", "?"),
        msg_id=msg.id,
        posted_at=msg.date.isoformat() if msg.date else None,
        text=text,
        buttons=btns,
        permalink=_permalink(chat, msg),
    )


async def list_channels():
    """Print every channel/group you're in, so you can pick which to watch."""
    async with client() as tg:
        print(f"{'ID':<16} {'@username':<28} Title")
        print("-" * 80)
        async for d in tg.iter_dialogs():
            e = d.entity
            if isinstance(e, (Channel, Chat)):
                uname = f"@{e.username}" if getattr(e, "username", None) else ""
                print(f"{e.id:<16} {uname:<28} {d.name}")
        print("\nCopy the @usernames (or ids) you want into CHANNELS= in .env, comma separated.")


async def backfill(limit=None):
    """Pull recent history from each configured channel. Run once at setup."""
    limit = limit or config.BACKFILL_LIMIT
    if not config.CHANNELS:
        raise SystemExit("No CHANNELS configured. Run: python run.py channels")
    total = 0
    async with client() as tg:
        for ch in config.CHANNELS:
            try:
                entity = await tg.get_entity(ch)
            except Exception as e:
                log.warning("cannot resolve %s: %s", ch, e)
                continue
            n = 0
            async for msg in tg.iter_messages(entity, limit=limit):
                if _store(entity, msg):
                    n += 1
            log.info("backfilled %s new from %s", n, getattr(entity, "title", ch))
            total += n
    return total


async def _resolve(tg, ch):
    """Resolve a channel from a @username OR a bare numeric id.

    Private channels have no username, only an id. Telegram internally prefixes
    channel ids with -100, but the listing prints them bare, so try both forms.
    """
    ch = str(ch).strip()
    if not ch:
        return None

    if ch.lstrip("-").isdigit():
        n = int(ch)
        candidates = [n] if n < 0 else [int(f"-100{n}"), n]
        for cid in candidates:
            try:
                return await tg.get_entity(cid)
            except Exception:
                continue
        # Last resort: scan the dialog list, which is always authoritative.
        target = abs(n)
        async for d in tg.iter_dialogs():
            if abs(d.entity.id) == target or abs(d.entity.id) == abs(int(f"-100{target}")):
                return d.entity
        return None

    return await tg.get_entity(ch)


async def _resolve_all(tg):
    """Resolve every configured channel once. Returns [(key, entity), ...]."""
    out = []
    for ch in config.CHANNELS:
        try:
            entity = await _resolve(tg, ch)
        except Exception as e:
            log.warning("cannot resolve %s: %s", ch, e)
            continue
        if entity is None:
            log.warning("cannot resolve %s — not found in your dialogs", ch)
            continue
        out.append((str(getattr(entity, "username", None) or entity.id), entity))
    return out


async def catch_up():
    """Fetch only what's new since the last run. This is the normal path.

    Telegram keeps channel history, so a laptop that runs twice a day misses
    nothing — it just reads back through the gap. Uses a per-channel high-water
    mark so repeat runs stay fast and cheap.
    """
    if not config.CHANNELS:
        raise SystemExit("No CHANNELS configured. Run: python run.py channels")
    total = 0
    async with client() as tg:
        for key, entity in await _resolve_all(tg):
            since = db.last_msg_id(key)
            kwargs = ({"min_id": since, "limit": None} if since
                      else {"limit": config.BACKFILL_LIMIT})

            n = 0
            try:
                async for msg in tg.iter_messages(entity, **kwargs):
                    if _store(entity, msg):
                        n += 1
            except Exception as e:
                log.warning("failed reading %s: %s", getattr(entity, "title", key), e)
                continue
            if n:
                log.info("+%s from %s", n, getattr(entity, "title", key))
            total += n
    log.info("caught up: %s new messages", total)
    return total


def attach_listener(tg: TelegramClient):
    """Register the live handler on an already-running client.

    Only used by `serve`. Numeric ids need the -100 prefix here, unlike the
    dialog listing which prints them bare.
    """
    chats = []
    for ch in config.CHANNELS:
        ch = str(ch).strip()
        if ch.lstrip("-").isdigit():
            n = int(ch)
            chats.append(n if n < 0 else int(f"-100{n}"))
        elif ch:
            chats.append(ch)

    @tg.on(events.NewMessage(chats=chats or None))
    async def _handler(event):
        try:
            chat = await event.get_chat()
            if _store(chat, event.message):
                log.info("new msg from %s", getattr(chat, "title", "?"))
        except Exception:
            log.exception("listener error")
    return tg
