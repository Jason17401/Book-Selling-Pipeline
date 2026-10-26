# Book Selling Pipeline

Photograph a set of books, send the photos to a Telegram bot, and get every book's ISBN, title, author and the price of
a new copy - with your selling price worked out for you. You fix anything missing in a simple review window, then sell
on Shopee from the same window: one click makes Shopee's bulk-upload file (and puts the photos online) for every
finished product, and another reads Shopee's order report back to mark products sold.

![How the pipeline works](docs/pipeline-flow.png)

## Guides

| Read | When |
|---|---|
| **[SETUP.md](SETUP.md)** | once, first: install, data folders, Telegram bot, keys, first set, Shopee, photo links (~40 minutes) |
| [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md) | step 3 of the setup: your own Telegram bot (~5 minutes) |
| [API_KEYS.md](API_KEYS.md) | step 4 of the setup: the four free keys (Google Books, Tavily, LangSearch, ImgBB) - how to get each, what uses it |
| **[DAILY_USE.md](DAILY_USE.md)** | every day: send → process → review → sell on Shopee; all commands; troubleshooting |
| [DATA_AND_STATUSES.md](DATA_AND_STATUSES.md) | what is stored where (`books.csv`, `products.csv`, `listings.csv`), condition grades, every status |

**Quick start:** double-click **`setup.bat`** (installs everything, makes `.env` and the data folders, runs the
tests) → fill in `.env` → from then on it is two double-clicks: **`bot.bat`** (receive and process photos) and
**`review.bat`** (check the books, then **Sell products**: Shopee's upload file, photos, order reports).

**Words.** A **set** is the books of one front photo (photographed together). A **product** is what you sell as one
thing - a whole set, or (if you choose it for that set) each of its books on its own - with a **product code** (a set's
is batch + set id, e.g. `20261008-153012-S01`; a book's is its sku). A **listing** is a product on one platform.

---

## How books and prices are found

**Book details** (title, author, publisher, year, pages, genre). Taiwanese ISBNs (978-957, 978-986, 978-626) are
asked, in order (`BOOK_PROVIDERS_TW`): **NCL → eslite → books.com.tw → Google Books → Open Library** - the
Chinese sources first; other ISBNs: Open Library → Google Books. Later sources only fill what is still empty, and the
search stops once title, author, publisher, year and genre are known. For Taiwanese books the **Chinese** title,
author, publisher and genre are always preferred over English ones (Google often only knows a translated book in
English), so the search goes on until they are Chinese. Every answer is remembered in `data\cache`, so processing a
book again costs nothing - except when a source could not be reached: that is not remembered, so it is asked again.

- **NCL** (國家圖書館 全國新書資訊網, Taiwan's ISBN agency) is searched by ISBN in its catalogue (it keeps each
  search in a session, so its search page is opened first). When the title in the result is a link, the full record
  behind it gives the registered price (定價) and the subject heading (主題標題). A search that finds nothing is
  saved in `data\cache\debug` - send it if NCL misses a book you can find on its site.
- **Genre** is stored as *overarching > most specific*, e.g. `童書 > 冒險／驚悚小說`: from NCL's subject heading,
  eslite's category levels, books.com.tw's 本書分類 / breadcrumb, or Google's category. Navigation steps that are not
  genres (首頁, 中文書, the book's own title ...) are left out.

**Market price** = the list price (定價) of a **new, printed** copy, first match wins:

1. the small **price barcode** printed next to the ISBN barcode, read from your back photo (free, instant)
2. **NCL** - the price the publisher registered (finds out-of-print books)
3. the ISBN in eslite's and books.com.tw's own search
4. a web search for the ISBN on eslite.com and books.com.tw - first the ISBN alone on both shops (like Google's
   `9789574760190 (site:eslite.com OR site:books.com.tw)`), then `9789574760190 site:eslite.com` and
   `eslite 9789574760190` - only with a Tavily / LangSearch key. eslite's own search leaves out books that are not in
   stock, so this is often how their product page (and its 定價) is found
5. the title in the shops' search → the same book in **another printed edition** (same title and author, Taiwanese
   edition, never a boxed set or another volume)
6. one web search for title + author

E-books and audiobooks are never used. Step 5-6 prices are marked `similar` in the `market_match` column and in the
review window, so you can check them.

**Your price** = market price × `PRICE_RATIO` (40%) in the **same currency** - 300 TWD → 120 TWD. A price you type or
caption is never changed automatically.

---

## Updating to a new version

Your `.env`, `data` and `.venv` are never part of an update. With the project in Git:

1. `git status` - commit or undo your own changes first.
2. Delete everything except `.git`, `.env`, `data` and `.venv`:
   `Get-ChildItem -Force | Where-Object { '.git','.env','data','.venv' -notcontains $_.Name } | Remove-Item -Recurse -Force`
3. Copy the **contents** of the new `Book Selling Pipeline` folder from the zip into your folder.
4. `git add -A`, `git status` (check the list), `git commit -m "Update"`, `git push`.
5. Double-click **`setup.bat`**: it installs anything new and runs the tests (your `.env` is left as it is). Then
   `git diff HEAD~1 -- .env.example` shows new or changed settings worth copying into your `.env`.

**Settings from older versions.** Your `.env` keeps working; these older values are handled for you, and the lines
can be tidied whenever you like:

- `BOOK_PROVIDERS_TW=google,ncl,eslite,books_tw,openlibrary` (the old default) is read as today's
  `ncl,eslite,books_tw,google,openlibrary` - the Chinese sources first.
- `WEB_QUERIES=site,shop` (the old default) is read as `isbn,site,shop`.
- `isbndb` in `BOOK_PROVIDERS` is skipped; `DEFAULT_CONDITION=like_new` means `as_new`.
- No longer used, delete if present: `ISBNDB_API_KEY`, `PHOTO_HOST`, `IMAGE_PUBLIC_URL`, `S3_ENDPOINT_URL`,
  `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `SET_WEIGHT_PER_BOOK_G`, `SET_SPLIT`, `BACK_ROTATE`,
  `PHOTO_ORDER`, `GOOGLE_COUNTRY`, `BRAVE_API_KEY`, `MARKET_EBOOK`.
- `SHOPEE_TEMPLATE` should be blank: the template lives in `data\listings\shopee\template\` (any file name).

---

## Project layout

```
Book Selling Pipeline/
├── README.md                        this overview
├── SETUP.md, TELEGRAM_SETUP.md      one-time setup
├── API_KEYS.md                      getting the four free API keys, and what each is used for
├── DAILY_USE.md                     the routine, commands, troubleshooting
├── DATA_AND_STATUSES.md             what is stored, and every status
├── .env.example → .env              settings (your copy: .env)
├── setup.bat                        double-click once (and after updates): installs, .env, data folders, tests
├── bot.bat, review.bat              double-click launchers for every day
├── requirements.txt                 what pip installs (requirements-dev.txt adds pytest)
├── pyproject.toml                   test and code-style settings (pytest, ruff)
├── docs/                            the flow diagram (pipeline-flow.svg / .png)
├── data/                            made by setup.bat (or any command): inbox, processed photos,
│                                    books.csv, products.csv, listings.csv, listings (photos, Shopee template /
│                                    uploads / import), cache
├── tests/                           automated tests (python -m pytest)
└── pipeline/                        the program  (python -m pipeline ...)
    ├── __main__.py                  the commands
    ├── core/                        settings, the CSV files, web requests, checks, live progress
    ├── photos/                      barcode reading, front-photo grid, inbox, splitting into sets
    ├── sources/                     book details, market prices, web search
    └── apps/                        Telegram bot, review window, editing, products, Shopee, photo hosting
```
