"""Message -> job record. Runs extract, dedupe, score in one pass."""
import json
import logging

import config
import db
import dedupe
import extract
import llm
import score

log = logging.getLogger("pipeline")


def process(limit=200) -> dict:
    prof = config.profile()
    counts = {"seen": 0, "not_job": 0, "repost": 0, "prefiltered": 0,
              "dupe": 0, "rejected": 0, "kept": 0, "scam": 0,
              "api_calls_saved": 0, "failed_will_retry": 0, "quota_stopped": False}

    # Belt and braces alongside DailyQuotaExceeded: if Google ever changes
    # their error format again (they have, more than once), our string-based
    # classifier could miss it. Several straight failures for ANY reason is
    # itself strong evidence of a persistent problem, not bad luck on
    # individual messages — so stop instead of grinding a 500-message
    # backlog through pointless retries one at a time.
    consecutive_failures = 0
    CIRCUIT_BREAKER_LIMIT = 4

    for msg in db.unprocessed_messages(limit):
        counts["seen"] += 1
        text = msg["text"] or ""

        # --- free skip 1: we have already analysed this exact posting ---
        chash = extract.content_hash(text)
        if chash:
            db.set_content_hash(msg["id"], chash)
            if db.seen_content(chash, msg["id"]):
                counts["repost"] += 1
                counts["api_calls_saved"] += 1
                db.mark_processed(msg["id"])
                continue

        # --- free skip 2: a title we would reject after paying to read ---
        if extract.looks_like_job(text):
            reason = extract.negative_prefilter(text)
            if reason:
                counts["prefiltered"] += 1
                counts["api_calls_saved"] += 1
                db.mark_processed(msg["id"])
                continue

        # --- the actual API calls. A message only gets marked processed once
        # we KNOW the outcome — a failure here must leave it alone so the next
        # run retries it, rather than silently discarding it forever. ---
        try:
            buttons = json.loads(msg["buttons"] or "[]")
            data = extract.extract(text, buttons)
            consecutive_failures = 0
        except llm.DailyQuotaExceeded:
            log.warning("Daily API quota reached. Stopping this run — "
                       "remaining messages are untouched and will run next time.")
            counts["quota_stopped"] = True
            break
        except llm.LLMCallFailed as e:
            counts["failed_will_retry"] += 1
            consecutive_failures += 1
            if consecutive_failures >= CIRCUIT_BREAKER_LIMIT:
                log.warning(
                    "%s calls in a row have failed — treating this as a stuck "
                    "quota/outage rather than bad luck. Stopping this run; "
                    "remaining messages are untouched and will run next time. "
                    "Last error: %s", consecutive_failures, e)
                counts["quota_stopped"] = True
                break
            log.warning("extraction failed for message %s (will retry next run): %s",
                       msg["id"], e)
            continue
        except Exception:
            # A genuine bug (bad JSON in stored buttons, etc), not an API failure.
            # Retrying won't help, so mark it processed rather than getting stuck.
            log.exception("unexpected error processing message %s — skipping it", msg["id"])
            db.mark_processed(msg["id"])
            continue

        if not data:
            counts["not_job"] += 1
            db.mark_processed(msg["id"])
            continue

        try:
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
            db.mark_processed(msg["id"])
        except llm.DailyQuotaExceeded:
            log.warning("Daily API quota reached during scoring. Stopping this run — "
                       "this message is untouched and will run next time.")
            counts["quota_stopped"] = True
            break
        except Exception:
            log.exception("failed scoring/saving message %s", msg["id"])
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
