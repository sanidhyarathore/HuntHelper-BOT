"""Write the tailored note, and (for email-type postings) park a Gmail draft.

Deliberately stops at DRAFT. Nothing is ever sent without you pressing send in
Gmail. Cheap insurance against a bad parse emailing your CV to a stranger.
"""
import base64
import json
import logging
import mimetypes
import os.path
from email.message import EmailMessage

import config
import llm

log = logging.getLogger("drafts")

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
TOKEN = str(config.ROOT / "data" / "gmail_token.json")
CREDS = str(config.ROOT / "data" / "gmail_credentials.json")

NOTE_SYSTEM = """Write a short application email for this candidate.

Hard rules:
- 110-150 words in the body. Nobody reads more.
- Plain human English. No "I am writing to express my keen interest", no
  "leverage", no "synergies", no em-dash-heavy consultant voice.
- Open with the single most relevant proof point for THIS role, with its number.
- One sentence on why this company specifically, only if the posting gives you
  something real to work with. If it doesn't, skip it rather than inventing.
- If the role is outside India, state availability to relocate in one clause.
- Close with a plain ask for a conversation.
- Never invent experience, employers, metrics or credentials.

Return a subject line and a body.
"""

NOTE_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
}


def tailored_note(job, prof) -> dict:
    payload = {
        "candidate": {
            "name": prof.get("name"),
            "headline": prof.get("headline"),
            "achievements": prof.get("achievements"),
            "current_title": prof.get("current_title"),
            "current_company": prof.get("current_company"),
            "linkedin": prof.get("linkedin"),
        },
        "job": {k: job[k] for k in ("company", "title", "location", "function", "seniority")
                if k in job.keys()},
    }
    try:
        data = llm.structured(NOTE_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str),
                              NOTE_SCHEMA, name="emit_note",
                              provider=config.PROVIDER_WRITE, model=config.MODEL_WRITE)
    except llm.LLMCallFailed as e:
        log.warning("note generation failed: %s", e)
        data = None
    if data and data.get("body"):
        return {"subject": data.get("subject") or
                f"Application: {job['title']} — {prof.get('name','')}",
                "body": data["body"]}
    return {
        "subject": f"Application: {job['title']} — {prof.get('name','')}",
        "body": "(note generation failed — write manually)",
    }


def _gmail():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Interactive — run this once on your LAPTOP, then copy data/gmail_token.json
            # to the VPS. Servers have no browser to complete the consent screen.
            flow = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def create_draft(to_addr: str, subject: str, body: str, attach: str | None = None) -> str | None:
    try:
        svc = _gmail()
        msg = EmailMessage()
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)
        if attach and os.path.exists(attach):
            ctype, _ = mimetypes.guess_type(attach)
            maintype, _, subtype = (ctype or "application/pdf").partition("/")
            with open(attach, "rb") as f:
                msg.add_attachment(f.read(), maintype=maintype, subtype=subtype,
                                   filename=os.path.basename(attach))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        draft = svc.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}).execute()
        return draft["id"]
    except Exception:
        log.exception("gmail draft failed")
        return None
