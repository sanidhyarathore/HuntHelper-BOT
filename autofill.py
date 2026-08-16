"""PHASE 2 — run this on your LAPTOP, not the VPS. Needs a real browser.

    python autofill.py <job_id>

Opens the apply page, fills what it recognises, attaches your CV, and then
stops. It does not click submit. You review and submit yourself — form parsers
misfire and a wrong answer on a visa or notice-period question is expensive.

Greenhouse and Lever have stable field names. Workday does not; it is not worth
automating and this script will just open it for you.
"""
import asyncio
import sys

import config
import db
import drafts

FIELDS = {
    "first_name": ["input#first_name", "input[name='name']", "input[autocomplete='given-name']"],
    "last_name": ["input#last_name", "input[autocomplete='family-name']"],
    "email": ["input#email", "input[type='email']", "input[name='email']"],
    "phone": ["input#phone", "input[type='tel']", "input[name='phone']"],
    "linkedin": ["input[name*='urls'][name*='LinkedIn']", "input[name*='linkedin' i]"],
}
RESUME = ["input[type='file'][name*='resume' i]", "input[type='file']"]


async def fill(job_id: int):
    from playwright.async_api import async_playwright

    j = db.job(job_id)
    if not j or not j["apply_url"]:
        sys.exit("No job or no apply URL for that id.")
    prof = config.profile()
    name = (prof.get("name") or "").split()
    vals = {
        "first_name": name[0] if name else "",
        "last_name": name[-1] if len(name) > 1 else "",
        "email": prof.get("email", ""),
        "phone": str(prof.get("phone", "")),
        "linkedin": prof.get("linkedin", ""),
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto(j["apply_url"], wait_until="domcontentloaded")

        for key, selectors in FIELDS.items():
            if not vals.get(key):
                continue
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() and await el.is_visible():
                        await el.fill(vals[key])
                        break
                except Exception:
                    continue

        for sel in RESUME:
            try:
                el = page.locator(sel).first
                if await el.count():
                    await el.set_input_files(config.CV_PATH)
                    break
            except Exception:
                continue

        note = drafts.tailored_note(j, prof)
        print("\n--- Cover note (paste if there's a box) ---\n")
        print(note["body"])
        print("\n--- Form is filled. Review every field, then submit yourself. ---")
        input("Press Enter here once you're done to close the browser… ")
        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python autofill.py <job_id>")
    db.init()
    asyncio.run(fill(int(sys.argv[1])))
