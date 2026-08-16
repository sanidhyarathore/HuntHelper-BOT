"""Message -> job record. Runs extract, dedupe, score in one pass."""
import json
import logging

import config
import db
import dedupe
import extract
import score

log = logging.getLogger("pipeline")


def process(limit=200) -> dict:
    prof = config.profile()
    counts = {"seen": 0, "not_job": 0, "dupe": 0, "rejected": 0, "kept": 0, "scam": 0}

    for msg in db.unprocessed_messages(limit):
        counts["seen"] += 1
        try:
            buttons = json.loads(msg["buttons"] or "[]")
            data = extract.extract(msg["text"] or "", buttons)
            if not data:
                counts["not_job"] += 1
                continue

            key = dedupe.dedupe_key(
                data["company"], data["title"], data["location"], data["apply_url"])

            s, reason, auto_rejected = score.score(data, prof)
            if data.get("scam"):
                counts["scam"] += 1

            row = {
                "message_id": msg["id"],
                "company": data["company"] or None,
                "title": data["title"] or None,
                "location": data["location"] or None,
                "country": data["country"] or None,
                "seniority": data["seniority"],
                "function": data["function"] or None,
                "comp": data["comp"] or None,
                "min_years": data["min_years"],
                "apply_type": data["apply_type"],
                "apply_url": data["apply_url"] or None,
                "apply_email": data["apply_email"] or None,
                "canonical_url": dedupe.canonical_url(data["apply_url"]),
                "dedupe_key": key,
                "scam": int(bool(data.get("scam"))),
                "scam_reason": data.get("scam_reason") or None,
                "fit_score": s,
                "fit_reason": reason,
                "raw": json.dumps(data, ensure_ascii=False),
                "status": "autorejected" if auto_rejected else "new",
            }
            if db.save_job(row) is None:
                counts["dupe"] += 1
            elif auto_rejected:
                counts["rejected"] += 1
            else:
                counts["kept"] += 1
        except Exception:
            log.exception("failed on message %s", msg["id"])
        finally:
            db.mark_processed(msg["id"])

    return counts


async def send_followups(bot):
    """Nudge on applications that have gone quiet."""
    d1, d2 = config.FOLLOWUP_DAYS
    rows = db.due_followups(d1, d2)
    for a in rows:
        which = 1 if not a["followup_1"] else 2
        await bot.send_message(
            config.MY_USER_ID,
            f"🔔 Follow-up #{which} due: <b>{a['title']}</b> @ {a['company']}\n"
            f"Applied {a['applied_at'][:10]}. Worth a nudge or a LinkedIn touch "
            f"to someone on the team.",
            parse_mode="html")
        db.mark_followup(a["id"], which)
    return len(rows)
