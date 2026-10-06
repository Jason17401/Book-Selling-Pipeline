"""Polite HTTP for the book-data providers: one shared session, a minimum gap between calls to the same site,
retries that respect Retry-After, a 'stop asking this site for the rest of the run' switch when it says
we are rate-limited or blocked, a local cache, and a daily counter for quota-limited APIs (Google Books).

Nothing here ever puts an API key in a URL or an error message.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")


class ProviderBlocked(RuntimeError):
    """The site rate-limited or blocked us; don't ask it again this run."""


class QuotaReached(RuntimeError):
    """Our own daily limit for this provider is used up."""


_SESSION: Optional[requests.Session] = None
_LOCK = threading.Lock()
_LAST_CALL: dict = {}      # host -> time.monotonic() of the last request
_BLOCKED: dict = {}        # provider -> reason (cleared when the program restarts)
_SECRET_RE = re.compile(r"(key|token|apikey|api_key|authorization)=([^&\s]+)", re.I)


def session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
    return _SESSION


def redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", str(text))


def blocked(provider: str) -> Optional[str]:
    return _BLOCKED.get(provider)


def block(provider: str, why: str) -> None:
    _BLOCKED[provider] = why


def reset_blocks() -> None:
    _BLOCKED.clear()


def _throttle(host: str, min_interval: float) -> None:
    if min_interval <= 0:
        return
    with _LOCK:
        last = _LAST_CALL.get(host)
        now = time.monotonic()
        if last is not None and now - last < min_interval:
            time.sleep(min_interval - (now - last))
        _LAST_CALL[host] = time.monotonic()


def _retry_after(r: requests.Response, default: float) -> float:
    v = r.headers.get("Retry-After", "")
    try:
        return min(60.0, max(1.0, float(v)))
    except ValueError:
        return default


_MEMO: dict = {}   # answers to identical GETs during this run (metadata + market price often need the same page)


def request(provider: str, method: str, url: str, *, min_interval: float = 1.0, retries: int = 2,
            timeout: float = 20, expect: str = "json", memo: bool = False, **kw):
    """memo=True: an identical GET made earlier in this run is answered from memory (no second request)."""
    key = None
    if memo and method == "GET":
        key = (url, expect, repr(sorted((kw.get("params") or {}).items())))
        if key in _MEMO:
            return _MEMO[key]
    result = _request(provider, method, url, min_interval=min_interval, retries=retries, timeout=timeout,
                      expect=expect, **kw)
    if key is not None:
        if len(_MEMO) > 300:
            _MEMO.clear()
        _MEMO[key] = result
    return result


def _request(provider: str, method: str, url: str, *, min_interval: float = 1.0, retries: int = 2,
             timeout: float = 20, expect: str = "json", **kw):
    """Returns parsed JSON (expect='json'), text (expect='text'), or None on 404.

    429 / 503: wait (Retry-After if given, else 2s, 4s) and retry; still failing -> ProviderBlocked.
    403: ProviderBlocked straight away (retrying a ban only makes it longer).
    """
    if _BLOCKED.get(provider):
        raise ProviderBlocked(f"skipped - {_BLOCKED[provider]}")
    host = urlparse(url).netloc
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        _throttle(host, min_interval)
        try:
            r = session().request(method, url, timeout=timeout, **kw)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 404:
            return None
        if r.status_code in (429, 503) or (r.status_code >= 500 and attempt < retries):
            if attempt < retries:
                time.sleep(_retry_after(r, 2.0 * (attempt + 1)))
                continue
            why = f"{host} answered {r.status_code} (rate limited / overloaded) - not asking it again this run"
            block(provider, why)
            raise ProviderBlocked(why)
        if r.status_code in (401, 403):
            detail = ""
            try:
                err = r.json().get("error", {})
                reasons = {e.get("reason", "") for e in err.get("errors", [])} if isinstance(err, dict) else set()
                detail = f" ({', '.join(sorted(x for x in reasons if x)) or err.get('message', '')})" if err else ""
            except Exception:
                pass
            why = f"{host} answered {r.status_code}{redact(detail)} - not asking it again this run"
            block(provider, why)
            raise ProviderBlocked(why)
        if r.status_code >= 400:
            raise RuntimeError(f"{host} answered HTTP {r.status_code}")
        if expect == "json":
            try:
                return r.json()
            except ValueError:
                raise RuntimeError(f"{host} did not return JSON (blocked by a captcha page?)")
        r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else (r.apparent_encoding or "utf-8")
        return r.text
    why = f"{host} could not be reached ({type(last_exc).__name__ if last_exc else 'error'}) - not asking it again this run"
    block(provider, why)
    raise ProviderBlocked(why)


# ---- cache -------------------------------------------------------------------------------------
class Cache:
    """data/cache/lookups.json: {"provider:isbn": {"t": unix_time, "v": {...}}}"""

    def __init__(self, path: Path, days: int, miss_days: int):
        self.path, self.days, self.miss_days = Path(path), days, miss_days
        self._data: Optional[dict] = None

    def _load(self) -> dict:
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}
        return self._data

    def get(self, provider: str, isbn: str) -> Optional[dict]:
        hit = self._load().get(f"{provider}:{isbn}")
        if not hit:
            return None
        age_days = (time.time() - hit.get("t", 0)) / 86400
        limit = self.days if hit.get("v") else self.miss_days
        return hit["v"] if age_days <= limit else None

    def put(self, provider: str, isbn: str, value: dict) -> None:
        if (self.days if value else self.miss_days) <= 0:
            return
        self._load()[f"{provider}:{isbn}"] = {"t": int(time.time()), "v": value}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=0), encoding="utf-8")
        tmp.replace(self.path)


# ---- daily quota -------------------------------------------------------------------------------
def pacific_today() -> str:
    """Google's daily quotas reset at midnight US Pacific time."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:  # Windows without the tzdata package: approximate with UTC-8
        return (datetime.now(timezone.utc) - timedelta(hours=8)).strftime("%Y-%m-%d")


class DailyCounter:
    """data/cache/quota.json: {"google": {"day": "2026-10-02", "used": 12}, "tavily": {"day": "2026-10", ...}}
    period="day" resets at midnight US Pacific (Google); period="month" resets each calendar month (web search)."""

    def __init__(self, path: Path, period: str = "day"):
        self.path, self.period = Path(path), period

    def _now(self) -> str:
        return pacific_today() if self.period == "day" else datetime.now(timezone.utc).strftime("%Y-%m")

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def used(self, name: str) -> int:
        d = self._read().get(name, {})
        return d.get("used", 0) if d.get("day") == self._now() else 0

    def take(self, name: str, limit: int, setting: str = "GOOGLE_DAILY_LIMIT") -> int:
        """Count one call. Raises QuotaReached if the limit is used up. Returns calls used in this period."""
        data = self._read()
        d = data.get(name, {})
        if d.get("day") != self._now():
            d = {"day": self._now(), "used": 0}
        if limit and d["used"] >= limit:
            when = "today, resets at midnight US Pacific time" if self.period == "day" else "this month"
            kind = "daily" if self.period == "day" else "monthly"
            raise QuotaReached(f"{kind} limit reached ({d['used']}/{limit} calls {when}). Change {setting} in .env "
                               "if you really need more.")
        d["used"] += 1
        data[name] = d
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return d["used"]


def save_debug(cache_dir: Path, name: str, text: str, keep: int = 30) -> Path:
    """Keep a copy of a page we could not understand (data/cache/debug/...), so it can be inspected or sent to a
    developer. Only the newest `keep` files are kept."""
    d = Path(cache_dir) / "debug"
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80]
    path = d / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe}.html"
    path.write_text(text or "", encoding="utf-8")
    for old in sorted(d.glob("*.html"))[:-keep]:
        old.unlink(missing_ok=True)
    return path


BLOCK_HINTS = ("captcha", "recaptcha", "cf-chl", "just a moment", "access denied", "robot check", "請進行驗證",
               "驗證碼", "unusual traffic")


def looks_blocked(page: str) -> bool:
    """A 'please prove you are human' page instead of the real one."""
    low = (page or "")[:20000].lower()
    return any(h in low for h in BLOCK_HINTS)
