# API keys

The pipeline uses four free services. Each needs its own key, and every key goes into your **`.env`** file (made by
`setup.bat`, step 1 of [SETUP.md](SETUP.md)). All four are optional: without a key that step is simply skipped and you
fill in by hand what it would have found. With them, most books need no typing at all.

| Key in `.env` | Service | Used in the workflow when ... | What you get without it | Cost |
|---|---|---|---|---|
| `GOOGLE_BOOKS_API_KEY` | Google Books | the bot processes a set (`/process`) and looks up each book's details | Google is still asked, but with a small shared allowance that runs out quickly | free: 1,000 look-ups a day |
| `TAVILY_API_KEY` | Tavily web search | a book's **market price** is not on its price barcode, at NCL or in the shops' own search - during `/process`, and when you press **Find** in the review window | those books get no market price: you type their price | free: 1,000 credits a month, no card |
| `LANGSEARCH_API_KEY` | LangSearch web search | the same as Tavily, when Tavily is used up, refuses the key or can't be reached | no second chance when Tavily fails | free daily allowance, no card |
| `IMGBB_API_KEY` | ImgBB image hosting | you press **Make Shopee upload file** in Sell products: each product's photos are put online and their links written into the upload file | no photo links: you drag each product's photos into Seller Center yourself | free, no card |

How it fits together:

```
setup.bat ── makes .env ──► you paste the four keys into .env
                                │
bot.bat ── /process ──► book details ............ GOOGLE_BOOKS_API_KEY
                        market price (web search) TAVILY_API_KEY, then LANGSEARCH_API_KEY
review.bat ── Find (market price) ............... TAVILY_API_KEY, then LANGSEARCH_API_KEY
           └─ Sell products ── Make Shopee upload file ── photos online ... IMGBB_API_KEY
```

**Keep keys secret.** They only ever go in `.env`, which is never uploaded to Git (`.gitignore` keeps it out). The
pipeline sends them in request headers or the request body, never in a link. If a key ever ends up somewhere public,
delete it on the service's site and make a new one.

**After changing `.env`**, close and restart `bot.bat` / `review.bat` - they read `.env` when they start.

Contents: [Google Books](#1-google-books-google_books_api_key) · [Tavily](#2-tavily-tavily_api_key) ·
[LangSearch](#3-langsearch-langsearch_api_key) · [ImgBB](#4-imgbb-imgbb_api_key) · [Checking all four](#checking-all-four)

---

## 1. Google Books (`GOOGLE_BOOKS_API_KEY`)

**What it does:** Google Books is one of the book-detail sources (title, author, publisher, year, pages, genre). For
Taiwanese books it is asked after NCL, eslite and books.com.tw (it often only knows the English title); for other books
it is the main source.

**Get the key** (about 5 minutes, a Google account is enough - no billing needed for this API):

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and sign in.
2. At the top, click the project picker → **New project** → name it e.g. `Book Selling Pipeline` → **Create**, and
   make sure it is selected.
3. Menu ☰ → **APIs & Services** → **Library** → search **Books API** → open it → **Enable**.
4. Menu ☰ → **APIs & Services** → **Credentials** → **+ Create credentials** → **API key**. Copy the key shown.
5. Recommended: click the new key (or **Edit API key**) → under **API restrictions** choose **Restrict key** → tick
   **Books API** → **Save**. The key then works for nothing else.

**Put it in `.env`:**

```
GOOGLE_BOOKS_API_KEY=AIza...your key...
```

`GOOGLE_DAILY_LIMIT=950` (already in `.env`) stops the pipeline just before Google's 1,000 a day; the count restarts
at midnight US Pacific time. `python -m pipeline quota` shows how many were used today.

**Check it:** `python -m pipeline lookup 9789861371955 --fresh --only google` should print the book's details from
Google (in a PowerShell window with `.venv` active - [SETUP.md 1.3](SETUP.md#13-typing-commands-yourself-optional)).
"403" or "API key not valid" means the key is mistyped or the Books API is not enabled for its project.

---

## 2. Tavily (`TAVILY_API_KEY`)

**What it does:** finds a book's product page on eslite.com / books.com.tw with a web search when the shops' own search
can't (eslite's search leaves out books that are out of stock - yet their product page, with its 定價, still exists).
It is only used for books whose market price was not found on the price barcode, at NCL or in the shops' search, so
most months use only a handful of searches.

**Get the key** (2 minutes, no credit card):

1. Go to [app.tavily.com](https://app.tavily.com) and sign up (Google sign-in works).
2. The dashboard (**Overview**) shows your API key, starting with `tvly-`. Copy it.

**Put it in `.env`:**

```
TAVILY_API_KEY=tvly-...your key...
```

**Limits:** the free plan has 1,000 API credits a month. The pipeline uses Tavily's thorough ("advanced") search,
2 credits per search, and also counts its own searches: `WEB_SEARCH_MONTHLY_LIMIT=900` (in `.env`) stops it before the
plan runs out. When Tavily is used up, LangSearch takes over.

**Check it:** `python -m pipeline market 9789574760190 --fresh` lists every search it makes - a line
`web search (tavily) "9789574760190": ...` means the key works. "401" means the key is wrong.

---

## 3. LangSearch (`LANGSEARCH_API_KEY`)

**What it does:** the second web search engine, used for the same searches as Tavily when Tavily is used up, refuses the
key or can't be reached. A search is never made twice: LangSearch is only asked when Tavily could not answer.

**Get the key** (2 minutes):

1. Go to [langsearch.com](https://langsearch.com) and sign up.
2. Open the [dashboard](https://langsearch.com/dashboard) → **API Keys** → create a key. Copy it straight away (it is
   shown in full only once).

**Put it in `.env`:**

```
LANGSEARCH_API_KEY=sk-...your key...
```

**Limits:** a free allowance that resets every day at 00:00 UTC (the dashboard's plan page shows it).

**Check it:** with no Tavily key (or Tavily used up), `python -m pipeline market 9789574760190 --fresh` shows
`web search (langsearch) ...`. To try LangSearch on its own once, put `WEB_SEARCH=langsearch` in `.env` (blank it again
afterwards: blank = Tavily first, then LangSearch).

---

## 4. ImgBB (`IMGBB_API_KEY`)

**What it does:** Shopee's bulk upload takes photos only as web links. With this key, **Make Shopee upload file** puts
each product's photos on [ImgBB](https://imgbb.com) (a free image host) and writes their links
(`https://i.ibb.co/.../01.jpg`) into the upload file, so Shopee fetches the photos by itself. Each photo is uploaded
once; its link is remembered in `data\listings\photos\<product code>\links.json` and reused until the photo changes.

**Get the key** (2 minutes, no credit card):

1. Go to [imgbb.com](https://imgbb.com) → **Sign up** (free) and sign in.
2. Open [api.imgbb.com](https://api.imgbb.com) while signed in → **Get API key** → copy it.

**Put it in `.env`:**

```
IMGBB_API_KEY=...your key...
```

**Check it:** in review → **Sell products** → **Make Shopee upload file**: the result says
`Photos: ... (online: imgbb)`, and the upload file's 主商品圖片 / 商品圖片 columns hold `https://i.ibb.co/...` links -
paste one into a browser to see the photo. If a photo can't be put online (wrong key, no internet), the upload file is
still made; that product just has no photo links and the result says why.

Uploaded photos stay until you delete them; anyone with a photo's link can see it (as on Shopee itself).

---

## Checking all four

With `.venv` active ([SETUP.md 1.3](SETUP.md#13-typing-commands-yourself-optional)):

| Key | Command / place | Works when you see |
|---|---|---|
| Google Books | `python -m pipeline lookup 9789861371955 --fresh --only google` | the book's title and author from `google` |
| Tavily | `python -m pipeline market 9789574760190 --fresh` | `web search (tavily) ...` |
| LangSearch | the same, with `WEB_SEARCH=langsearch` in `.env` for the test | `web search (langsearch) ...` |
| ImgBB | Sell products → **Make Shopee upload file** | `(online: imgbb)` and `i.ibb.co` links in the upload file |

Back to the setup: [SETUP.md step 4](SETUP.md#4-api-keys) · the everyday routine: [DAILY_USE.md](DAILY_USE.md)
