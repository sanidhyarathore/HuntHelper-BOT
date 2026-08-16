# Telegram Job Radar

Watches your Telegram job channels, kills the noise, and DMs you only the roles
worth your time — with tap-to-act buttons and a Gmail draft already written.

```
Telegram channels ──▶ ingest (Telethon, read-only)
                          │
                          ▼
                     extract (Haiku)  →  regex prefilter kills ~70% first
                          │
                          ▼
                      dedupe (canonical URL / fuzzy company+title+city)
                          │
                          ▼
                       score (hard rules → LLM fit 0-100)
                          │
                          ▼
                    bot DM ──▶ [Open] [Source] [Applied] [Skip] [Later] [Draft] [Note]
                          │
                          ▼
                    tracker → follow-up nudge at day 5 and day 12
```

## Setup (≈25 minutes)

**1. Credentials**

| What | Where | Cost |
|---|---|---|
| `TG_API_ID` / `TG_API_HASH` | [my.telegram.org](https://my.telegram.org) → API development tools | free |
| `BOT_TOKEN` | DM [@BotFather](https://t.me/BotFather) → `/newbot` | free |
| `MY_USER_ID` | DM [@userinfobot](https://t.me/userinfobot) | free |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | pay per token |

**2. Install**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill it in
```

**3. Pick your channels**

```bash
python run.py channels      # prompts for phone + OTP on first run, then lists everything
```

Copy the `@usernames` you want into `CHANNELS=` in `.env`.

**4. Fill in `profile.yaml`.** This is the highest-leverage file in the repo —
it drives both the filter and the note writer. Add your email, phone, LinkedIn.
Drop your CV at `assets/cv.pdf`.

**5. First run**

Message your bot `/start` first — Telegram won't let a bot DM you until you've
spoken to it once. Then:

```bash
python run.py cycle
```

The first run pulls ~200 recent messages per channel and takes a few minutes.
Every run after that only fetches what's new, so it's seconds.

## Everyday use

**Double-click `runme.command` (Mac) or `runme.bat` (Windows).**

That runs one full cycle: fetch anything new → score it → send the good ones to
your Telegram → nudge you on applications that have gone quiet. Then it sits
there so the buttons on those messages work. Close the window when you're done
triaging.

From a terminal it's the same thing:

```bash
python run.py cycle
```

**You do not need a server.** Telegram keeps channel history, so a run picks up
everything posted since last time — close your laptop for three days and the
next run reads back through the whole gap. Nothing is missed. The only cost of
not being always-on is hearing about a job a few hours late, which for
applications is almost never the difference.

Run it once in the morning and once in the evening and you're covered.

### If you later want it always-on

Two ways, in order of effort:

**Scheduled on your laptop.** Runs by itself whenever the machine is awake.

- Mac: `crontab -e`, then add
  `0 9,19 * * * cd ~/tgjobs && ./.venv/bin/python run.py cycle`
- Windows: Task Scheduler → Create Basic Task → point it at `runme.bat`

**A rented server (VPS).** A computer in a datacentre that never sleeps. Around
€4/month at Hetzner, or free on Oracle Cloud's always-free tier. You'd run
`python run.py serve` under systemd, and copy `data/user.session` and
`data/gmail_token.json` up from your laptop first — those two need an OTP and a
browser to create, which a server doesn't have.

Only worth it if you find yourself wanting sub-hour alerts. Start on the laptop.

## Cost

Haiku handles extraction and scoring; Sonnet only runs when you tap **Draft** or
**Note**. At ~300 messages/day, of which the regex prefilter passes ~90, expect
roughly **$1–3/month** in API spend. The prefilter is doing most of the work
there — if you add very chatty channels, tune `JOB_KEYWORDS` in `config.py`
before you tune anything else.

## Tuning in week one

Watch what slips through and what gets wrongly killed:

```bash
sqlite3 data/jobs.db "SELECT fit_score, company, title, location, fit_reason
  FROM jobs WHERE status='autorejected' ORDER BY id DESC LIMIT 30;"
```

If good roles are being auto-rejected, the culprit is almost always
`target_locations` in `profile.yaml` or the `JUNIOR`/`ENGINEERING` regexes in
`score.py`. If junk is getting through, raise `NOTIFY_THRESHOLD` from 60 to 70
before you touch the prompts.

`python run.py export` dumps everything to CSV if you'd rather tune in a sheet.

## Deliberate design choices

**Read-only Telegram access.** The user client never posts, joins or clicks. It
does exactly what the official app does, so there's no realistic account risk.
Bot callback buttons in channels are surfaced for you to tap, never auto-clicked
— that's the one action that would look automated.

**Nothing is ever submitted for you.** Gmail drafts sit in drafts. The autofill
script stops before submit. This isn't excess caution: extraction misfires, and
a wrong answer to a visa-status or notice-period question on a real application
is expensive to undo. The time saved is in the triage and the drafting, which is
where it actually was.

**Volume is not the goal.** For senior roles in the UAE market, twenty tailored
applications beat two hundred blasted ones, and the channels are full of
CV-harvesting operations that a volume strategy walks straight into. The system
is built to make each application cheap, not to make applications numerous.

## Phase 2

- `python autofill.py <job_id>` — Playwright form fill for Greenhouse/Lever, laptop only.
- Company enrichment: pull the careers page and LinkedIn headcount before you apply.
- Referral surfacing: flag jobs where an IIT-K or Urban Company alum works there.
