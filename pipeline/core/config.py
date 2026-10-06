from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #")[0] if " #" in v else v      # allow trailing "  # comment"
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _ratio(text: str) -> float:
    """'0.4', '40%' or '40' -> 0.4"""
    t = (text or "0.4").strip().rstrip("%")
    v = float(t)
    return v / 100 if (text.strip().endswith("%") or v > 1) else v


def _csv(text: str) -> tuple:
    return tuple(x.strip().lower() for x in (text or "").split(",") if x.strip())


ALL_PROVIDERS = ("openlibrary", "openlibrary_search", "google", "isbndb", "eslite", "books_tw", "ncl")


@dataclass
class Config:
    data_dir: Path = Path("data")
    telegram_token: str = ""
    allowed_user_ids: set = field(default_factory=set)
    google_books_key: str = ""
    isbndb_key: str = ""
    isbndb_base: str = "https://api2.isbndb.com"
    contact_email: str = "you@example.com"
    providers: tuple = ("openlibrary", "openlibrary_search", "google", "isbndb")
    providers_tw: tuple = ("eslite", "books_tw", "google", "ncl", "openlibrary")  # used for Taiwanese ISBNs (957/986/626)
    stop_when: tuple = ("title", "author", "publisher", "year")  # stop asking more providers once these are filled
    cache_days: int = 180          # remember what a provider returned for an ISBN (saves your Google quota)
    cache_miss_days: int = 7       # remember "not found" for this long, then ask again
    google_daily_limit: int = 950  # stop calling Google Books after this many calls per (Pacific-time) day
    google_queries: tuple = ("id", "isbn")  # GOOGLE_STEPS: ways to find the book on Google, tried in order
    scrape_delay: float = 3.0      # seconds between requests to the same website when scraping (eslite/books_tw/ncl)
    scrape_user_agent: str = ""    # blank = a normal desktop browser string
    ncl_base: str = "https://isbn.ncl.edu.tw/NEW_ISBNNet"
    eslite_details: bool = False   # one extra eslite call per book to get page count and binding
    min_price: float = 1.0
    max_price: float = 500.0
    grid: tuple = (2, 5)  # rows x cols the books are laid out in on the front photo
    region: tuple = (0.0, 0.0, 1.0, 1.0)  # left, top, right, bottom of the books as fractions of the photo (ignore blank margins)
    rotate: int = 0  # degrees CLOCKWISE to turn every photo so it is upright (0/90/180/270); see `orient`
    default_currency: str = "TWD"  # used when a price is given without a currency
    default_condition: str = "like_new"  # condition of a new book unless a caption says otherwise ("" = none)
    # market price of a NEW copy and your price from it (see pipeline/sources/market.py)
    market_providers: tuple = ("eslite", "books_tw", "ncl", "barcode")
    market_search: tuple = ("isbn", "isbn10", "title", "web")
    web_search: str = ""           # tavily | langsearch: find shop pages with a web search API when the shop's search fails
    tavily_api_key: str = ""
    langsearch_api_key: str = ""
    web_search_monthly_limit: int = 900
    web_queries: tuple = ("site", "shop")   # "9789574760190 site:eslite.com", then "eslite 9789574760190"
    market_similar: bool = True    # no copy with this ISBN anywhere: use another edition (same title + author)
    market_ebook: bool = True      # no printed copy with this ISBN: use the e-book with the same ISBN
    market_title_pages: int = 3    # books.com.tw: product pages checked per title search
    auto_price: bool = True        # fill an EMPTY price from the market price
    price_ratio: float = 0.4       # your price = market price x this
    price_round: str = "TWD:1,*:0.5"  # round your automatic price per currency (0 = cents)
    listing_show_market: bool = True  # show "new: 300 TWD" next to each book in listings
    set_size: int = 10
    decoders: tuple = ("zxing", "zbar", "opencv")
    decode_effort: str = "normal"  # fast | normal | max
    bot_timeout: float = 30.0  # seconds the Telegram bot waits for Telegram before retrying (BOT_TIMEOUT)
    settle_seconds: int = 5  # ignore files modified in the last N seconds (still syncing)

    @property
    def inbox(self) -> Path:
        return Path(self.data_dir) / "inbox"

    @property
    def processed(self) -> Path:
        return Path(self.data_dir) / "processed"

    @property
    def review(self) -> Path:
        return Path(self.data_dir) / "needs_review"

    @property
    def listings_dir(self) -> Path:
        return Path(self.data_dir) / "listings"

    @property
    def csv_path(self) -> Path:
        return Path(self.data_dir) / "books.csv"

    @property
    def cache_dir(self) -> Path:
        return Path(self.data_dir) / "cache"

    def ensure_dirs(self) -> None:
        for d in (self.inbox, self.processed, self.review, self.listings_dir):
            d.mkdir(parents=True, exist_ok=True)

    def apply_decode_settings(self) -> None:
        from ..photos import decode
        decode.configure(self.decoders, self.decode_effort)

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        e = os.environ.get
        ids = {int(x) for x in e("ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x}
        rotate = int(e("ROTATE", "0") or 0) % 360
        cfg = cls(
            data_dir=Path(e("DATA_DIR", "data")),
            telegram_token=e("TELEGRAM_BOT_TOKEN", ""),
            allowed_user_ids=ids,
            google_books_key=e("GOOGLE_BOOKS_API_KEY", ""),
            isbndb_key=e("ISBNDB_API_KEY", ""),
            isbndb_base=e("ISBNDB_BASE_URL", "https://api2.isbndb.com"),
            contact_email=e("CONTACT_EMAIL", "you@example.com"),
            providers=_csv(e("BOOK_PROVIDERS", "openlibrary,openlibrary_search,google,isbndb")),
            providers_tw=_csv(e("BOOK_PROVIDERS_TW", "eslite,books_tw,google,ncl,openlibrary")),
            stop_when=_csv(e("LOOKUP_STOP_WHEN", "title,author,publisher,year")),
            cache_days=int(e("LOOKUP_CACHE_DAYS", "180") or 0),
            cache_miss_days=int(e("LOOKUP_CACHE_MISS_DAYS", "7") or 0),
            google_daily_limit=int(e("GOOGLE_DAILY_LIMIT", "950") or 0),
            google_queries=_csv(e("GOOGLE_STEPS", "") or e("GOOGLE_QUERIES", "") or "id,isbn"),
            scrape_delay=float(e("SCRAPE_DELAY", "3") or 0),
            scrape_user_agent=e("SCRAPE_USER_AGENT", "").strip(),
            ncl_base=e("NCL_BASE_URL", "https://isbn.ncl.edu.tw/NEW_ISBNNet").rstrip("/"),
            eslite_details=e("ESLITE_DETAILS", "0").strip().lower() in ("1", "yes", "true", "on"),
            min_price=float(e("MIN_PRICE", "1")),
            max_price=float(e("MAX_PRICE", "500")),
            grid=tuple(int(x) for x in e("GRID", "2x5").lower().split("x")),
            region=tuple(float(x) for x in e("REGION", "0,0,1,1").split(",")),
            rotate=rotate,
            default_currency=(e("DEFAULT_CURRENCY", "TWD").strip().upper() or "TWD"),
            default_condition=e("DEFAULT_CONDITION", "like_new").strip().lower(),
            market_providers=_csv(e("MARKET_PROVIDERS", "eslite,books_tw,ncl,barcode")),
            market_search=_csv(e("MARKET_SEARCH", "isbn,isbn10,title,web")),
            web_search=e("WEB_SEARCH", "").strip().lower(),
            tavily_api_key=e("TAVILY_API_KEY", "").strip(),
            langsearch_api_key=e("LANGSEARCH_API_KEY", "").strip(),
            web_search_monthly_limit=int(e("WEB_SEARCH_MONTHLY_LIMIT", "900") or 0),
            web_queries=tuple(q for q in _csv(e("WEB_QUERIES", "site,shop")) if q in ("site", "shop")) or ("site",),
            market_title_pages=int(e("MARKET_TITLE_PAGES", "3") or 3),
            market_similar=e("MARKET_SIMILAR", "1").strip().lower() not in ("0", "no", "false", "off"),
            market_ebook=e("MARKET_EBOOK", "1").strip().lower() not in ("0", "no", "false", "off"),
            auto_price=e("AUTO_PRICE", "1").strip().lower() in ("1", "yes", "true", "on"),
            price_ratio=_ratio(e("PRICE_RATIO", "0.4")),
            price_round=e("PRICE_ROUND", "TWD:1,*:0.5").strip(),
            listing_show_market=e("LISTING_SHOW_MARKET", "1").strip().lower() in ("1", "yes", "true", "on"),
            set_size=int(e("SET_SIZE", "10")),
            decoders=_csv(e("DECODERS", "zxing,zbar,opencv")),
            decode_effort=e("DECODE_EFFORT", "normal").strip().lower(),
            settle_seconds=int(e("SETTLE_SECONDS", "5")),
            bot_timeout=float(e("BOT_TIMEOUT", "30") or 30),
        )
        unknown = [p for p in cfg.providers + cfg.providers_tw if p not in ALL_PROVIDERS]
        if unknown:
            raise SystemExit(f"Unknown book provider(s) in .env: {', '.join(unknown)}. Known: {', '.join(ALL_PROVIDERS)}")
        if cfg.default_condition and cfg.default_condition not in ("new", "like_new", "good", "acceptable", "poor"):
            raise SystemExit("DEFAULT_CONDITION must be one of new, like_new, good, acceptable, poor (or blank)")
        bad_m = [m for m in cfg.market_providers if m not in ("barcode", "eslite", "books_tw", "ncl")]
        if bad_m:
            raise SystemExit(f"MARKET_PROVIDERS: unknown {', '.join(bad_m)}. Use any of: barcode, eslite, books_tw, ncl")
        bad_q = [q for q in cfg.google_queries if q not in ("id", "isbn", "isbn10", "plain")]
        if bad_q:
            raise SystemExit(f"GOOGLE_STEPS: unknown {', '.join(bad_q)}. Use any of: id, isbn, isbn10, plain")
        cfg.apply_decode_settings()
        return cfg
