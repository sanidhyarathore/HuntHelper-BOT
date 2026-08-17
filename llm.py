"""One structured-output call, four possible providers.

Anthropic uses native tool-use. Everything else goes through the OpenAI-compatible
route, which Gemini, Groq and OpenAI all speak.

Free tiers have tight per-minute limits, so every call goes through a throttle
and retries on 429 with backoff. On Gemini's free tier a full run of ~180 calls
takes roughly ten minutes. That's fine — it runs in the background.
"""
import json
import logging
import random
import re
import threading
import time

import config

log = logging.getLogger("llm")

_client = None
_lock = threading.Lock()
_last_call = 0.0

BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "openai": None,          # SDK default
    "openrouter": "https://openrouter.ai/api/v1",
}


def _throttle():
    """Space calls out so we stay under the provider's requests-per-minute cap."""
    global _last_call
    with _lock:
        gap = config.LLM_MIN_INTERVAL - (time.monotonic() - _last_call)
        if gap > 0:
            time.sleep(gap)
        _last_call = time.monotonic()


def _client_for(provider):
    global _client
    if _client is not None:
        return _client
    if provider == "anthropic":
        from anthropic import Anthropic
        config.require("ANTHROPIC_API_KEY")
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    else:
        from openai import OpenAI
        config.require("LLM_API_KEY")
        _client = OpenAI(api_key=config.LLM_API_KEY, base_url=BASE_URLS.get(provider))
    return _client


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
    client = _client_for(provider)
    sys_prompt = (
        f"{system}\n\n"
        f"Reply with a single JSON object and nothing else — no prose, no code fences.\n"
        f"It must match this schema exactly:\n{json.dumps(schema)}"
    )
    kwargs = dict(
        model=model,
        max_tokens=1500,
        messages=[{"role": "system", "content": sys_prompt},
                  {"role": "user", "content": user}],
    )
    try:
        resp = client.chat.completions.create(
            **kwargs, response_format={"type": "json_object"})
    except Exception:
        # Some models/providers reject response_format. The prompt still asks for JSON.
        resp = client.chat.completions.create(**kwargs)
    return _parse_json(resp.choices[0].message.content)


def structured(system, user: str, schema: dict, name: str = "result",
               model: str | None = None, cache_system: bool = False) -> dict | None:
    """Get a schema-shaped dict back from whichever provider is configured.

    system: a string, or a list of strings where the last one is the stable
    prefix worth caching (pass cache_system=True to enable that).
    """
    provider = config.LLM_PROVIDER
    model = model or config.MODEL_EXTRACT

    for attempt in range(config.LLM_MAX_RETRIES):
        _throttle()
        try:
            if provider == "anthropic":
                return _call_anthropic(model, system, user, schema, name,
                                       cache_system=cache_system)
            joined = system if isinstance(system, str) else "\n\n".join(system)
            return _call_openai_compat(provider, model, joined, user, schema, name)
        except Exception as e:
            msg = str(e).lower()
            rate_limited = "429" in msg or "rate" in msg or "resource_exhausted" in msg
            last = attempt == config.LLM_MAX_RETRIES - 1
            if last:
                log.error("LLM call failed after %s attempts: %s",
                          config.LLM_MAX_RETRIES, e)
                return None
            wait = (config.LLM_BACKOFF_BASE * (2 ** attempt)
                    + random.uniform(0, 1)) if rate_limited else 2
            if rate_limited:
                log.warning("rate limited, waiting %.0fs (attempt %s)", wait, attempt + 1)
            time.sleep(wait)
    return None
