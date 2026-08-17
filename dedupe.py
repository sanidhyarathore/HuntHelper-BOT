"""Collapse the same job posted across five channels into one record.

Two signals, in priority order:
  1. Canonical apply URL (tracking params stripped, ATS job-id isolated).
  2. Fuzzy key of normalised company + title + city.
The stronger one wins; jobs.dedupe_key has a UNIQUE constraint so the DB
enforces it for us.
"""
import hashlib
import re
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

TRACKING = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "referer", "referrer", "source", "src", "fbclid", "gclid", "igshid",
    "gh_src", "lever-source", "trk", "trackingId", "refId",
}

NOISE_WORDS = {
    "hiring", "urgent", "immediate", "joining", "new", "opening", "openings",
    "role", "job", "vacancy", "apply", "now", "fulltime", "full", "time",
    "wfh", "remote", "onsite", "hybrid", "yrs", "years", "exp", "experience",
    "the", "a", "an", "and", "for", "at", "in", "of",
}

# Redirect wrappers we can safely unwrap-ish (we just note them, no network calls)
SHORTENERS = {"bit.ly", "tinyurl.com", "lnkd.in", "t.co", "rb.gy", "shorturl.at", "cutt.ly"}


def canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return url.strip().lower()
    if not p.netloc:
        return url.strip().lower()

    host = p.netloc.lower().removeprefix("www.")
    if host in SHORTENERS:
        # Can't resolve without a network hop; treat the short link as its own identity.
        return f"{host}{p.path}".rstrip("/")

    qs = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in TRACKING]
    qs.sort()
    path = p.path.rstrip("/")

    # Greenhouse / Lever / Ashby: the job id in the path is the true identity.
    m = re.search(r"/(jobs?|postings?)/([A-Za-z0-9\-_]+)", path)
    if m and host.split(".")[-2:] in (["greenhouse", "io"], ["lever", "co"], ["ashbyhq", "com"]):
        return f"{host}/job/{m.group(2)}".lower()

    return urlunparse(("", host, path, "", urlencode(qs), "")).lstrip("/").lower()


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9\s]", " ", (s or "").lower())
    toks = [t for t in s.split() if t and t not in NOISE_WORDS]
    return " ".join(sorted(set(toks)))


def dedupe_key(company: str, title: str, location: str, apply_url: str) -> str:
    """Identity of a job posting.

    Company+title is the better signal when we have it: aggregator channels
    repost the same role behind their own tracking links, so URLs differ even
    though the job is identical. URL is the fallback for posts with no named
    employer.
    """
    nc, nt = _norm(company), _norm(title)
    if nc and nt:
        city = _norm(location).split(" ")[0] if location else ""
        basis = f"fuzzy::{nc}::{nt}::{city}"
    else:
        cu = canonical_url(apply_url)
        basis = f"url::{cu}" if cu else f"fuzzy::{nc}::{nt}::{_norm(location)}"
    return hashlib.sha1(basis.encode()).hexdigest()[:20]
