"""Find a shop's product page for an ISBN with a WEB search engine - the same trick as typing
"eslite 9789869283533" into Google, then opening the top results:

    1. search "9789869283533 site:eslite.com" (then "eslite 9789869283533" if needed; WEB_QUERIES)
    2. take the first 3 results that are pages on that shop
    3. open each one in turn; if the page's ISBN is OURS, read its 定價 (list price) - done
       (eslite: the product's own data via the shop's API; books.com.tw: the product page, or the page text the
       search engine returns if books.com.tw refuses us)

Why: the shops' own search leaves books out. eslite's search, for example, does not return OUT-OF-PRINT books at all
(9789869283533, the 2016 edition of Google必修的圖表簡報術, only shows the 2020 edition), yet the product page with
its list price still exists. A web search engine has indexed that page.

Google itself has no free search API any more, and automatically scraping Google's result pages breaks its terms and
gets blocked within a few queries. Two official search APIs are supported; a search is made ONCE, on the first engine
that works (tavily, then langsearch), only when the shops' own search found nothing; searches are counted per calendar
month (WEB_SEARCH_MONTHLY_LIMIT) and answers are cached:

  tavily  https://tavily.com - free plan: 1,000 searches a month, NO credit card. Key in TAVILY_API_KEY.
          POST https://api.tavily.com/search   header: Authorization: Bearer <key>
          body: {"query": "eslite 9789869283533", "include_domains": ["eslite.com"], "max_results": 10,
                 "include_raw_content": true}
          reply: {"results": [{"url": "https://www.eslite.com/product/1001247312496322", "title": ...}]}
  langsearch  https://langsearch.com - free plan with a DAILY allowance (resets 00:00 UTC), no published number.
          POST https://api.langsearch.com/v1/web-search   header: Authorization: Bearer <key>
          body: {"query": "eslite 9789869283533", "freshness": "noLimit", "summary": true, "count": 10}

Without any key you can still do it by hand, for free: in the review window press "Search the web" (opens the search
in your browser), copy the shop's product link and paste it into "Shop link" - the pipeline reads the price from it.
"""
from __future__ import annotations

import logging
import re

from ..core import net, progress
from ..core.config import Config

TAVILY_URL = "https://api.tavily.com/search"
LANGSEARCH_URL = "https://api.langsearch.com/v1/web-search"
KEYS = {"tavily": "tavily_api_key", "langsearch": "langsearch_api_key"}
log = logging.getLogger("pipeline.market")


def engines(cfg: Config) -> list:
    """The web search engines that will be used, in order: those named in WEB_SEARCH (comma-separated), or - if
    WEB_SEARCH is blank - every engine whose key is in .env (tavily, langsearch). Engines without a key are
    skipped. If one finds nothing (or is used up / refuses the key), the next one is tried."""
    names = [n.strip() for n in (cfg.web_search or "").split(",") if n.strip()] or ["tavily", "langsearch"]
    return [n for n in names if n in KEYS and getattr(cfg, KEYS[n], "")]


def enabled(cfg: Config) -> bool:
    return bool(engines(cfg))


def describe(cfg: Config) -> str:
    on = engines(cfg)
    if on:
        return "web search: " + " -> ".join(on)
    return "web search: OFF (no TAVILY_API_KEY / LANGSEARCH_API_KEY in .env)"


def _all_results(obj) -> list:
    """Every result with an http(s) 'url' anywhere in a JSON reply, in order (whatever the exact reply layout is):
    [{"url":..., "title":..., "text": snippet/summary/content}]."""
    out = []
    if isinstance(obj, dict):
        url = obj.get("url")
        if isinstance(url, str) and url.startswith("http"):
            text = " ".join(str(obj.get(k) or "") for k in ("raw_content", "content", "summary", "snippet",
                                                            "description", "extra_snippets"))
            out.append({"url": url, "title": str(obj.get("title") or obj.get("name") or ""), "text": text})
        for k, v in obj.items():
            if k != "url":
                out += _all_results(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _all_results(v)
    return out


# what you would type into Google for each shop. WEB_QUERIES picks which forms are tried, in order:
#   site = "9789574760190 site:eslite.com"   shop = "eslite 9789574760190"
SHOP_WORD = {"eslite.com": "eslite", "books.com.tw": "books.com.tw"}
TOP = 3   # results checked per search, like reading the first three Google results


def query_for(isbn: str, site: str, form: str = "shop") -> str:
    if form == "site":
        return f"{isbn} site:{site}"
    return f"{SHOP_WORD.get(site, site)} {isbn}"


def run_queries(queries: list, sites: list, cfg: Config, top: int = TOP):
    """Run each query ONCE, on the first engine that works (tavily -> langsearch: the next engine is only
    used when one fails, is used up or refuses the key - never to repeat the same search). Yields, per search, its
    top `top` results that are pages on one of `sites`; results already yielded are skipped. The caller checks each
    batch and stops as soon as one has the book, so the next query is only spent when needed."""
    seen = set()
    for q in queries:
        for engine in engines(cfg):
            if net.blocked(engine):
                continue
            progress.step("market", f"web search ({engine}): \"{q}\"")
            try:
                results = _search(engine, q, sites, cfg)
            except (net.QuotaReached, net.ProviderBlocked) as exc:   # e.g. monthly limit, or a wrong key (401)
                net.block(engine, str(exc))
                log.warning("  web search (%s) switched off for this run: %s", engine, exc)
                continue
            except Exception as exc:
                log.warning("  web search (%s) failed: %s", engine, net.redact(exc))
                continue
            on_site = [r for r in results if any(site in r["url"] for site in sites)]
            fresh = []
            for r in on_site:
                if r["url"] not in seen and len(fresh) < top:
                    seen.add(r["url"])
                    fresh.append(r)
            log.info('  web search (%s) "%s": %d result(s), %d on %s, checking %d new', engine, q, len(results),
                     len(on_site), " / ".join(sites), len(fresh))
            if fresh:
                yield fresh
            break          # this query is done - the other engines are not asked the same thing


def searches(isbn: str, site: str, cfg: Config, top: int = TOP):
    """The ISBN searches for one shop: "<isbn> site:eslite.com", then "eslite <isbn>" (WEB_QUERIES)."""
    return run_queries([query_for(isbn, site, form) for form in cfg.web_queries], [site], cfg, top)


def search_query(query: str, sites: list, cfg: Config, top: int = TOP):
    """One free-text search (e.g. a title + author) limited to the shops' sites."""
    return run_queries([query], sites, cfg, top)


def search(isbn: str, site: str, cfg: Config, top: int = TOP) -> list:
    """The first batch from searches() ([] when web search is off or nothing was found)."""
    for batch in searches(isbn, site, cfg, top):
        return batch
    return []


def search_urls(isbn: str, site: str, cfg: Config, top: int = TOP) -> list:
    return [r["url"] for r in search(isbn, site, cfg, top)]


TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


def page_text(url: str, cfg: Config) -> str:
    """The text of a web page as fetched by Tavily's servers (POST https://api.tavily.com/extract {"urls": [url]}).
    Used when a shop refuses OUR request for its page (books.com.tw sometimes answers 403). Needs TAVILY_API_KEY;
    counted like a search. '' when unavailable."""
    if not cfg.tavily_api_key or net.blocked("tavily"):
        return ""
    try:
        net.DailyCounter(cfg.cache_dir / "quota.json", period="month").take(
            "tavily", cfg.web_search_monthly_limit, "WEB_SEARCH_MONTHLY_LIMIT")
        progress.step("market", f"reading the page through Tavily: {url}")
        data = net.request("tavily", "POST", TAVILY_EXTRACT_URL, min_interval=1.0,
                           json={"urls": [url], "extract_depth": "basic"},
                           headers={"Authorization": f"Bearer {cfg.tavily_api_key}",
                                    "Content-Type": "application/json"}) or {}
    except (net.QuotaReached, net.ProviderBlocked) as exc:
        net.block("tavily", str(exc))
        return ""
    except Exception as exc:
        log.info("  tavily could not read %s: %s", url, net.redact(exc))
        return ""
    return " ".join(str(r.get("raw_content") or "") for r in data.get("results") or [])


def _search(engine: str, query: str, sites, cfg: Config, count: int = 10) -> list:
    sites = [sites] if isinstance(sites, str) else list(sites)
    net.DailyCounter(cfg.cache_dir / "quota.json", period="month").take(
        engine, cfg.web_search_monthly_limit, "WEB_SEARCH_MONTHLY_LIMIT")
    if engine == "langsearch":
        data = net.request("langsearch", "POST", LANGSEARCH_URL, min_interval=0.5,
                           json={"query": query, "freshness": "noLimit", "summary": True, "count": count},
                           headers={"Authorization": f"Bearer {cfg.langsearch_api_key}",
                                    "Content-Type": "application/json"}) or {}
        return _all_results(data)
    if engine == "tavily":
        # include_domains = Google's "site:" - every result is a page on the shop; raw_content = the page text,
        # so the ISBN and 定價 can be checked even if the shop refuses our own request for the page
        data = net.request("tavily", "POST", TAVILY_URL, min_interval=1.0,
                           json={"query": query, "include_domains": sites, "max_results": count,
                                 "search_depth": "basic", "include_raw_content": True},
                           headers={"Authorization": f"Bearer {cfg.tavily_api_key}",
                                    "Content-Type": "application/json"}) or {}
        return _all_results(data.get("results") or [])
    raise ValueError(f"unknown web search engine {engine!r}")


_PRICE_RE = re.compile(r"定\s*價\s*[:：]?\s*(?:NT\$|NT|\$)?\s*([0-9][0-9,]{1,5})\s*元?")


def price_in_text(text: str, isbn: str) -> str:
    """The 定價 (list price) from a result's page text - only if that text also shows OUR ISBN. '' otherwise."""
    flat = re.sub(r"[\s-]", "", text or "")
    if isbn not in flat:
        return ""
    m = _PRICE_RE.search(text or "")
    return m.group(1).replace(",", "") if m else ""


def browser_search_url(isbn: str) -> str:
    """For the review window's 'Search the web' button: the search YOU run in your own browser (free, no limits)."""
    from urllib.parse import quote
    return "https://www.google.com/search?q=" + quote(f"{isbn} (site:eslite.com OR site:books.com.tw)")
