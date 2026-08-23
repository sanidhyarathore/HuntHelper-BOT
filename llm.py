"""One structured-output call, four possible providers.

Anthropic uses native tool-use. Everything else goes through the OpenAI-compatible
route, which Gemini, Groq and OpenAI all speak.

Free tiers have tight limits, so every call goes through a throttle and retries
on transient rate limits. A DAILY quota hit is different — no amount of backoff
helps until the quota resets — so that raises immediately instead of retrying.
"""
import json
import logging
import random
import re
import threading
import time

import config

log = logging.getLogger("llm")

_clients = {}
_lock = threading.Lock()
_last_call = {}

BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "openai": None,          # SDK default
    "openrouter": "https://openrouter.ai/api/v1",
}


class LLMCallFailed(Exception):
    """The call did not succeed after retries. The caller decides what that means —
    for extraction, it means the message must NOT be marked processed."""


class DailyQuotaExceeded(LLMCallFailed):
    """A per-day cap was hit. Retrying won't help until it resets — callers should
    stop the current run rather than keep retrying this or later messages."""


def _classify_error(msg_lower: str) -> str | None:
    """'daily', 'minute', or None (not a rate-limit error at all)."""
    is_rate = ("429" in msg_lower or "resource_exhausted" in msg_lower
               or "rate limit" in msg_lower or "ratelimiterror" in msg_lower
               or "quota" in msg_lower)
    if not is_rate:
        return None
    if re.search(r"\bper[\s_-]?day\b|\bdaily\b|\brpd\b", msg_lower):
        return "daily"
    return "minute"


def _throttle(provider):
    """Space calls per provider, so a slow free tier doesn't stall a fast one."""
    with _lock:
        interval = config.min_interval(provider)
        gap = interval - (time.monotonic() - _last_call.get(provider, 0.0))
        if gap > 0:
            time.sleep(gap)
        _last_call[provider] = time.monotonic()


def _client_for(provider):
    if provider in _clients:
        return _clients[provider]
    key = config.api_key_for(provider)
    if not key:
        raise SystemExit(
            f"No API key for provider '{provider}'. Set "
            f"{provider.upper()}_API_KEY in your .env file.")
    if provider == "anthropic":
        from anthropic import Anthropic
        _clients[provider] = Anthropic(api_key=key)
    else:
        from openai import OpenAI
        _clients[provider] = OpenAI(api_key=key, base_url=BASE_URLS.get(provider))
    return _clients[provider]


def _parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in fences or prose. Dig it out."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def _call_anthropic(model, system, user, schema, name, cache_system=False):
    """system may be a str, or a list of strings where the LAST is cached.

    Caching only fires above a per-model minimum (4,096 tokens for Haiku 4.5).
    Below that the API silently ignores cache_control and bills full rate, so
    we check the size ourselves and warn rather than let it fail invisibly.
    """
    if isinstance(system, str):
        system = [system]

    blocks = []
    for i, part in enumerate(system):
        b = {"type": "text", "text": part}
        is_last = i == len(system) - 1
        if cache_system and is_last:
            if _approx_tokens(part) < config.CACHE_MIN_TOKENS:
                global _cache_warned
                if not _cache_warned:
                    log.warning(
                        "cacheable block is ~%s tokens, under the %s minimum — "
                        "caching will be silently skipped",
                        _approx_tokens(part), config.CACHE_MIN_TOKENS)
                    _cache_warned = True
            else:
                b["cache_control"] = {"type": "ephemeral"}
        blocks.append(b)

    resp = _client_for("anthropic").messages.create(
        model=model,
        max_tokens=1500,
        system=blocks,
        tools=[{"name": name, "description": "Emit the result.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": name},
        messages=[{"role": "user", "content": user}],
    )
    _record_cache_usage(resp)

    for b in resp.content:
        if b.type == "tool_use":
            return dict(b.input)
    raise ValueError("no tool_use block in response")


def _approx_tokens(s: str) -> int:
    """Rough token count. Good enough to check against the cache minimum."""
    return len(s) // 3


_cache_warned = False
CACHE_STATS = {"written": 0, "read": 0, "uncached": 0}


def _record_cache_usage(resp):
    u = getattr(resp, "usage", None)
    if not u:
        return
    CACHE_STATS["written"] += getattr(u, "cache_creation_input_tokens", 0) or 0
    CACHE_STATS["read"] += getattr(u, "cache_read_input_tokens", 0) or 0
    CACHE_STATS["uncached"] += getattr(u, "input_tokens", 0) or 0


def cache_report() -> str:
    s = CACHE_STATS
    total = s["read"] + s["uncached"] + s["written"]
    if not total:
        return "no API calls made"
    pct = 100 * s["read"] / total
    return (f"input tokens: {s['read']:,} cached (charged ~10%), "
            f"{s['written']:,} cache writes, {s['uncached']:,} full price "
            f"— {pct:.0f}% served from cache")


def _call_openai_compat(provider, model, system, user, schema, name):
    """Try strict json_schema first (most reliable), fall back progressively.

    Not every OpenAI-compatible endpoint supports every response_format
    variant, so this degrades gracefully rather than assuming.
    """
    client = _client_for(provider)
    base_messages = [{"role": "system", "content": system},
                     {"role": "user", "content": user}]
    kwargs = dict(model=model, max_tokens=1500, messages=base_messages)

    try:
        resp = client.chat.completions.create(
            **kwargs, response_format={
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            })
        return _parse_json(resp.choices[0].message.content)
    except Exception:
        pass  # not every provider supports json_schema — degrade below

    try:
        resp = client.chat.completions.create(
            **kwargs, response_format={"type": "json_object"})
        return _parse_json(resp.choices[0].message.content)
    except Exception:
        pass  # some providers/models reject response_format entirely

    sys_prompt = (
        f"{system}\n\nReply with a single JSON object and nothing else — "
        f"no prose, no code fences. It must match this schema:\n{json.dumps(schema)}"
    )
    resp = client.chat.completions.create(
        model=model, max_tokens=1500,
        messages=[{"role": "system", "content": sys_prompt},
                  {"role": "user", "content": user}])
    return _parse_json(resp.choices[0].message.content)


def structured(system, user: str, schema: dict, name: str = "result",
               model: str | None = None, provider: str | None = None,
               cache_system: bool = False) -> dict:
    """Get a schema-shaped dict back from whichever provider is configured.

    system: a string, or a list of strings where the last one is the stable
    prefix worth caching (pass cache_system=True to enable that; Anthropic only).

    Raises DailyQuotaExceeded or LLMCallFailed on failure — it does NOT return
    None. A caller that treated a None return as "not applicable" would silently
    mistake a failed call for a real negative result, which is exactly the bug
    this replaces.
    """
    provider = provider or config.LLM_PROVIDER
    model = model or config.MODEL_EXTRACT
    last_err = None

    for attempt in range(config.LLM_MAX_RETRIES):
        _throttle(provider)
        try:
            if provider == "anthropic":
                return _call_anthropic(model, system, user, schema, name,
                                       cache_system=cache_system)
            joined = system if isinstance(system, str) else "\n\n".join(system)
            return _call_openai_compat(provider, model, joined, user, schema, name)
        except Exception as e:
            last_err = e
            kind = _classify_error(str(e).lower())
            if kind == "daily":
                log.error("daily quota hit on %s (%s) — no point retrying today",
                          provider, model)
                raise DailyQuotaExceeded(str(e)) from e

            last_attempt = attempt == config.LLM_MAX_RETRIES - 1
            if last_attempt:
                break
            if kind == "minute":
                wait = config.LLM_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 1)
                log.warning("rate limited on %s, waiting %.0fs (attempt %s)",
                           provider, wait, attempt + 1)
            else:
                wait = 2
            time.sleep(wait)

    log.error("LLM call to %s failed after %s attempts: %s",
              provider, config.LLM_MAX_RETRIES, last_err)
    raise LLMCallFailed(str(last_err))
