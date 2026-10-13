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
    providers_tw: tuple = ("google", "ncl", "eslite", "books_tw", "openlibrary")  # used for Taiwanese ISBNs (957/986/626)
    stop_when: tuple = ("title", "author", "publisher", "year", "genre")  # stop asking more providers once these are filled
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
    segment: str = "auto"          # auto = find each book on the front photo; grid = equal cells (the old way)
    segment_margin: float = 0.04   # table kept around each cut-out book cover, as a fraction of the book's size
    rotate: int = 0  # degrees CLOCKWISE to turn every photo so it is upright (0/90/180/270); see `orient`
    default_currency: str = "TWD"  # used when a price is given without a currency
    default_condition: str = "like_new"  # condition of a new book unless a caption says otherwise ("" = none)
    # market price of a NEW copy and your price from it (see pipeline/sources/market.py)
    market_providers: tuple = ("barcode", "ncl", "eslite", "books_tw")
    market_search: tuple = ("isbn", "isbn10", "title", "web")
    web_search: str = ""           # tavily | langsearch: find shop pages with a web search API when the shop's search fails
    tavily_api_key: str = ""
    langsearch_api_key: str = ""
    web_search_monthly_limit: int = 900
    web_queries: tuple = ("site", "shop")   # "9789574760190 site:eslite.com", then "eslite 9789574760190"
    market_similar: bool = True    # no copy with this ISBN anywhere: use another edition (same title + author)
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
    # selling a set (pipeline/apps/sets.py) and Shopee (pipeline/apps/shopee.py)
    set_discount: float = 0.0          # set price = sum of the books' prices minus this fraction (0.1 = 10% off)
    set_weight_per_book_g: int = 300   # a set's weight when you have not weighed it: books x this
    set_box_cm: tuple = (30, 22, 15)   # parcel size (length, width, height in cm) when not given per set
    shopee_template: str = ""          # Shopee's bulk-upload template, saved as .xlsx (blank = data/listings/shopee/template.xlsx)
    shopee_category_id: str = ""       # 分類代碼 of the category to list in (blank = search the template for shopee_category_name)
    shopee_category_name: str = "二手書"
    shopee_logistics: tuple = ("7-eleven", "全家", "萊爾富", "ok", "蝦皮店到店")   # delivery options switched on
    shopee_days_to_ship: str = ""      # 較長備貨天數 (blank = Shopee's normal handling time)
    shopee_max_images: int = 9
    shopee_shop_id: str = ""           # your shop's number, to build product links (blank = links only from what you paste)
    shopee_export_password: str = ""   # password of Shopee's order report (it is protected), to open it automatically
    image_public_url: str = ""         # photos online at <this>/<set code>/01.jpg ... (blank = you add photos by hand)
    s3_endpoint_url: str = ""          # S3-compatible bucket (e.g. Cloudflare R2) the photos are uploaded to
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

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
    def sets_path(self) -> Path:
        return Path(self.data_dir) / "sets.csv"

    @property
    def listings_path(self) -> Path:
        return Path(self.data_dir) / "listings.csv"

    @property
    def shopee_dir(self) -> Path:
        return self.listings_dir / "shopee"

    @property
    def shopee_import(self) -> Path:
        """Put Shopee's order report / product list files here; `python -m pipeline shopee` reads them."""
        return self.shopee_dir / "import"

    @property
    def template_path(self) -> Path:
        return Path(self.shopee_template) if self.shopee_template else self.shopee_dir / "template.xlsx"

    @property
    def cache_dir(self) -> Path:
        return Path(self.data_dir) / "cache"

    def ensure_dirs(self) -> None:
        for d in (self.inbox, self.processed, self.review, self.listings_dir, self.shopee_import):
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
            providers_tw=_csv(e("BOOK_PROVIDERS_TW", "google,ncl,eslite,books_tw,openlibrary")),
            stop_when=_csv(e("LOOKUP_STOP_WHEN", "title,author,publisher,year,genre")),
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
            segment=(e("SEGMENT", "auto").strip().lower() or "auto"),
            segment_margin=max(0.0, float(e("SEGMENT_MARGIN", "0.04") or 0)),
            default_currency=(e("DEFAULT_CURRENCY", "TWD").strip().upper() or "TWD"),
            default_condition=e("DEFAULT_CONDITION", "like_new").strip().lower(),
            market_providers=_csv(e("MARKET_PROVIDERS", "barcode,ncl,eslite,books_tw")),
            market_search=_csv(e("MARKET_SEARCH", "isbn,isbn10,title,web")),
            web_search=e("WEB_SEARCH", "").strip().lower(),
            tavily_api_key=e("TAVILY_API_KEY", "").strip(),
            langsearch_api_key=e("LANGSEARCH_API_KEY", "").strip(),
            web_search_monthly_limit=int(e("WEB_SEARCH_MONTHLY_LIMIT", "900") or 0),
            web_queries=tuple(q for q in _csv(e("WEB_QUERIES", "site,shop")) if q in ("site", "shop")) or ("site",),
            market_title_pages=int(e("MARKET_TITLE_PAGES", "3") or 3),
            market_similar=e("MARKET_SIMILAR", "1").strip().lower() not in ("0", "no", "false", "off"),
            auto_price=e("AUTO_PRICE", "1").strip().lower() in ("1", "yes", "true", "on"),
            price_ratio=_ratio(e("PRICE_RATIO", "0.4")),
            price_round=e("PRICE_ROUND", "TWD:1,*:0.5").strip(),
            listing_show_market=e("LISTING_SHOW_MARKET", "1").strip().lower() in ("1", "yes", "true", "on"),
            set_size=int(e("SET_SIZE", "10")),
            decoders=_csv(e("DECODERS", "zxing,zbar,opencv")),
            decode_effort=e("DECODE_EFFORT", "normal").strip().lower(),
            settle_seconds=int(e("SETTLE_SECONDS", "5")),
            bot_timeout=float(e("BOT_TIMEOUT", "30") or 30),
            set_discount=_ratio(e("SET_DISCOUNT", "0") or "0"),
            set_weight_per_book_g=int(float(e("SET_WEIGHT_PER_BOOK_G", "300") or 300)),
            set_box_cm=tuple(int(float(x)) for x in (e("SET_BOX_CM", "30,22,15") or "30,22,15").split(",")[:3]),
            shopee_template=e("SHOPEE_TEMPLATE", "").strip(),
            shopee_category_id=e("SHOPEE_CATEGORY_ID", "").strip(),
            shopee_category_name=e("SHOPEE_CATEGORY_NAME", "二手書").strip(),
            shopee_logistics=_csv(e("SHOPEE_LOGISTICS", "7-ELEVEN,全家,萊爾富,OK,蝦皮店到店")),
            shopee_days_to_ship=e("SHOPEE_DAYS_TO_SHIP", "").strip(),
            shopee_max_images=max(2, int(e("SHOPEE_MAX_IMAGES", "9") or 9)),
            shopee_shop_id=e("SHOPEE_SHOP_ID", "").strip(),
            shopee_export_password=e("SHOPEE_EXPORT_PASSWORD", "").strip(),
            image_public_url=e("IMAGE_PUBLIC_URL", "").strip().rstrip("/"),
            s3_endpoint_url=e("S3_ENDPOINT_URL", "").strip(),
            s3_bucket=e("S3_BUCKET", "").strip(),
            s3_access_key_id=e("S3_ACCESS_KEY_ID", "").strip(),
            s3_secret_access_key=e("S3_SECRET_ACCESS_KEY", "").strip(),
        )
        unknown = [p for p in cfg.providers + cfg.providers_tw if p not in ALL_PROVIDERS]
        if unknown:
            raise SystemExit(f"Unknown book provider(s) in .env: {', '.join(unknown)}. Known: {', '.join(ALL_PROVIDERS)}")
        from .validate import CONDITIONS, OLD_CONDITIONS
        cfg.default_condition = OLD_CONDITIONS.get(cfg.default_condition, cfg.default_condition)
        if cfg.default_condition and cfg.default_condition not in CONDITIONS:
            raise SystemExit(f"DEFAULT_CONDITION must be one of {', '.join(CONDITIONS)} (or blank)")
        bad_m = [m for m in cfg.market_providers if m not in ("barcode", "eslite", "books_tw", "ncl")]
        if bad_m:
            raise SystemExit(f"MARKET_PROVIDERS: unknown {', '.join(bad_m)}. Use any of: barcode, eslite, books_tw, ncl")
        bad_q = [q for q in cfg.google_queries if q not in ("id", "isbn", "isbn10", "plain")]
        if bad_q:
            raise SystemExit(f"GOOGLE_STEPS: unknown {', '.join(bad_q)}. Use any of: id, isbn, isbn10, plain")
        if cfg.segment not in ("auto", "grid"):
            raise SystemExit("SEGMENT must be auto or grid")
        cfg.apply_decode_settings()
        return cfg
