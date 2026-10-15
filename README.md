# Book Selling Pipeline

Photograph a set of books, send the photos to a Telegram bot, and get every book's ISBN, title, author and the price of
a new copy - with your selling price worked out for you. You fix anything missing in a simple review window, then sell each
set on Shopee: one double-click makes Shopee's bulk-upload file and photos for every finished set, and reads Shopee's
order report back to mark sets sold.

![How the pipeline works](docs/pipeline-flow.png)

**Setting up for the first time?** Follow sections 1 → 2 → 3 in order (about 30 minutes, once).
After that, section 4 is all you need day to day.

1. [Install](#1-install)
2. [Set up the Telegram bot](#2-set-up-the-telegram-bot)
3. [Before your first set](#3-before-your-first-set)
4. [Daily use](#4-daily-use)
5. [How books and prices are found](#5-how-books-and-prices-are-found)
6. [What is stored, and statuses](#6-what-is-stored-and-statuses)
7. [Commands](#7-commands)
8. [Troubleshooting](#8-troubleshooting)
9. [Project layout](#9-project-layout)

---

## 1. Install

Written for Windows (PowerShell). On macOS / Linux the differences are noted in brackets.

### 1.1 You need

- **Python 3.11 or 3.12** from [python.org](https://www.python.org/downloads/). In the installer, tick
  **"Add python.exe to PATH"**. Check in a new PowerShell window: `python --version`
- **Telegram** on your phone (and a Telegram account).

### 1.2 Install the pipeline

Open PowerShell in the `Book Selling Pipeline` folder (in File Explorer: open the folder, then
**File → Open Windows PowerShell**, or type `powershell` in the address bar) and run, one line at a time:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

This makes a private Python environment (`.venv`) inside the folder and installs everything into it.
[macOS / Linux: use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.]

> Typing `.\.venv\Scripts\python.exe` every time is optional: run `.\.venv\Scripts\Activate.ps1` once per window and
> plain `python` then means the project's Python. If PowerShell says "running scripts is disabled", run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again. The rest of this README writes `python`.

### 1.3 Create your settings file

```powershell
Copy-Item .env.example .env
```

[macOS / Linux: `cp .env.example .env`]

`.env` holds your settings and keys. You fill in the Telegram part in section 2; everything else already has working
values. Each setting is explained in `.env` itself.

### 1.4 Check that it works

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

All tests should pass (they use made-up photos and no internet). Then try a real look-up:

```powershell
python -m pipeline lookup 9789861371955
```

It prints what each book source found for that ISBN (被討厭的勇氣).

---

## 2. Set up the Telegram bot

Follow **[TELEGRAM_SETUP.md](TELEGRAM_SETUP.md)** - it takes about 5 minutes: you create your own bot, put its token
and your user id into `.env`, and send it a first photo. It ends by sending you back here.

---

## 3. Before your first set

### 3.1 Recommended free keys (put them in `.env`)

| Key                    | What it adds                                                               | How to get it                                                                                                                                                                                                                                           |
| ---------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GOOGLE_BOOKS_API_KEY` | Google Books as a book source (1,000 look-ups a day)                       | [Google Cloud Console](https://console.cloud.google.com/): create a project → **APIs & Services → Library** → enable **Books API** → **Credentials → Create credentials → API key**. Then edit the key and under **API restrictions** choose Books API. |
| `TAVILY_API_KEY`       | web search for books the shops' own search can't find (often out of print) | sign up at [tavily.com](https://tavily.com) - 1,000 searches a month, no credit card                                                                                                                                                                    |
| `LANGSEARCH_API_KEY`   | a second web search, used when Tavily is used up                           | sign up at [langsearch.com](https://langsearch.com) - free daily allowance                                                                                                                                                                              |

Everything works without them; you just find fewer books and prices automatically. Keep keys only in `.env` - never
paste them anywhere else.

### 3.2 Lay out and photograph your first set

1. Lay the books in a grid: by default **2 rows of 5** (`GRID=2x5`, `SET_SIZE=10`). Book 1 is top-left, then left to
   right along the row, then the next row. Leave a small gap between books if you can, on a plain surface that
   contrasts with the covers - each book's cover picture is cut out of this photo automatically.
2. **Photo 1:** all the front covers together, in landscape, straight from above. Send it **as a File** (in Telegram:
   paperclip → File) for sharp cover pictures - a normal Telegram photo is shrunk to about 1280 px.
3. **Photos 2-11:** the back cover of each book with its barcode, in book order 1 → 10. Fill the frame, keep the
   barcode sharp and without glare. A normal photo is plenty.

Send them as described in section 4, then look at the **review sheet** the bot sends back: on each row, the book's
cover (cut out of the front photo) and its back photo must be the same book.

**How the covers are cut out.** The pipeline finds each book on the front photo (where it differs from the table, and
the straight edges between books), straightens slightly turned books, and saves each one at full resolution with a
small margin of table around it (`SEGMENT_MARGIN`, default 4% of the book's size). The shadow a book casts on the
table (darker table, same colour, carpet texture still showing through) is not counted as book, so the cover sits in
the middle of its picture with the same margin on every side, and the margin never reaches past halfway to the next
book. The numbering stays the grid order,
so book N's cover always pairs with back photo N. To see how a photo is cut:

```powershell
python -m pipeline segment path\to\front.jpg --covers covers_test
```

It writes `segments.jpg` with every book's box and number (green = found, orange = not clearly found, so the plain grid
cell is used) and the cut-out covers into `covers_test`. If a layout keeps confusing it, `SEGMENT=grid` goes back to
equal cells.

If the numbers sit on the wrong books because the front photo arrives **sideways**, run this once on that photo:

```powershell
python -m pipeline orient data\processed\<batch>\S01\front.jpg
```

It writes `orient.jpg` with the photo turned four ways. Put the number of the upright, landscape one into `.env` as
`ROTATE=` and restart the bot. (Only the front photo is turned; barcodes read in any direction.)
If there is empty table around the books, `REGION` trims it - see `.env`.

Setup done. From now on, section 4 is the routine.

---

## 4. Daily use

### 4.1 Start

Double-click **`bot.bat`** and leave its window open while you work. It shows each book being looked up, live.

### 4.2 Send a set (in Telegram)

1. Send the **front photo** on its own and wait for the reply `Set 1: FRONT photo saved`.
2. Select the **10 back photos in book order** and send them in **one go** (one album). Never select more than 10
   at once - Telegram splits bigger selections into albums that can arrive out of order.
3. The bot answers once the album has arrived: `Set 1: barcode 10/10 saved. Set complete`.
4. Next set: front photo again, then its 10 back photos. Send as many sets as you like before processing.

**Captions** (optional, typed with a photo):

| Caption                                                                                          | On the front photo                                    | On a back photo                                                    |
| ------------------------------------------------------------------------------------------------ | ----------------------------------------------------- | ------------------------------------------------------------------ |
| a condition: `全新` `近全新` `良好` `普通` `差強人意` (or `new` `like new` `good` `fair` `poor`) | condition of every book in the set                    | condition of that book                                             |
| condition + price: `良好 150`, `good 150 TWD`                                                    | condition + price for every book                      | condition + price for that book                                    |
| `retake`                                                                                         | -                                                     | replaces the previous back photo (send it right after the bad one) |
| `front`                                                                                          | starts a new set early (a set of fewer than 10 books) | -                                                                  |

Without a caption, books are 近全新 (`like_new`) and priced from the market price. The grades are explained in
[section 6.1](#61-condition-grades).

### 4.3 Process

Send **`/process`**. One message in the chat shows a progress bar and what is being looked up; any book the pipeline
couldn't identify gets its own `NEEDS MANUAL` message. At the end you get a summary, the review sheet for each set, and
the list of books that still need something.

### 4.4 Review and fix (on the computer)

Double-click **`review.bat`** (the bot can keep running). On the left, the books that need you, **grouped by set**:
each set is a heading - `Set S01 · batch 20261008-153012` (set + batch together identify it) with how many books still
need something - and its books below it as `#3 Title`. Click a set heading to fold it away or open it again.

On the right, the chosen book: four photos - **its front** (cut out of the set photo), **its back photo**, **front +
back** side by side (the picture its set listing uses) and **the whole set** with every book's number on it and this
book outlined in blue. Click one to see it big: it opens fitted
to the window, so every photo shows the same way; **+ Bigger / − Smaller** (or `+` / `-`, a left / right click on the
photo, `Ctrl`+mouse wheel) zoom in and out, **Fit to window** (`0`) shows it whole again, **Actual size** (`1`).
**Genres** are chips: × removes one, **+ Add genre** adds one (type it, Enter).

What each colour means:

- **Red - to fix:** something is missing or wrong (e.g. no ISBN, no price). The fields to fix are outlined in red.
- **Amber - quick check:** everything was found. Glance at the photos and fields; if they look right just press
  **Save / looks right** (or `Ctrl+Enter` to save and go to the next one) - nothing needs typing.
- **Green - checked:** you have saved it. A set goes on sale when all its books are green.

- **Barcode not read:** read the 13 digits under the barcode on the back photo (click it to see it big), type them, press
  **Look up**, check, **Save**.
- **Price missing:** no market price was found. Type your price (and currency), or find the book online - **Search the
  web** opens a Google search; paste the shop's product link into **Shop link** and press **Use link**.
- **"Other edition" note under the market price:** the price came from another printing of the same book (same
  title and author, different ISBN/year) - check it looks right.
- **Whole set** fills an empty condition or price for every book of the set at once.

`Ctrl+S` saves, `Ctrl+Enter` saves and jumps to the next red or amber book (into the next set too), `Ctrl+R` refreshes,
`Ctrl+L` opens **Sell sets** (4.5). Tick **Show checked books too** to see the green ones again.

### 4.5 Sell the sets on Shopee

A set is sold as **one Shopee product**: title with the set's code, the total price of its books, 9 photos and a
description listing every book by its number on the photo.

**Where the pipeline stops.** Shopee only opens its automation API to Shopee Mall sellers and registered companies in
Taiwan, and a robot clicking through Seller Center would put your shop at risk. So the pipeline uses Shopee's own
**bulk upload (大量上傳)**: it fills in Shopee's Excel template for you, and **you upload that file** in Seller Center
(about a minute), then check and publish the sets there. Everything before and after that is automatic: the upload
file, the photos, and - from Shopee's order report - which sets are ordered and sold.

**Once, before the first upload (about 10 minutes)**

1. **Shopee's template.** Seller Center → 我的商品 → 新增商品 → **大量上傳** → download the **基礎模板** (basic
   template). If it is an `.xls` file, open it in Excel and **Save As → Excel Workbook (.xlsx)**. Save it as
   `data\listings\shopee\template.xlsx`. Its upload sheet is 上傳模板: rows 1-6 are Shopee's keys, names,
   必填/選填 (required/optional), explanations and formats; the sets go from row 7. Shopee lists only the delivery
   options switched on in your shop, so download it again after changing them.
2. **Category - optional, nothing to set.** The template marks 分類代碼 as 選填 (optional). The sets go in **101573
   其他書籍** (Books & Magazines > Books > Other Books) when the template's category list has it; otherwise the column is
   left blank and Shopee suggests a category for each set under 尚未刊登 - check it there before publishing. To always
   use one, set `SHOPEE_CATEGORY_ID` in `.env` (e.g. 101541 繪本童書 for children's books).
3. **Delivery** in `.env`: `SHOPEE_LOGISTICS` (the delivery options to switch on, e.g. 7-ELEVEN, 全家, 蝦皮店到店), and
   `SET_DISCOUNT`, the whole-set discount (default 20% off the sum of its books' prices = 8 折; change it per set in
   Sell sets). Nothing to weigh or measure: Shopee needs a weight,
   so it is estimated (`SET_WEIGHT_PER_BOOK_G` x books); the parcel size is optional and left out.
4. _Optional:_ `data\listings\description_footer.txt` - the lines at the end of every description (made on the first
   run; edit freely).
5. _Optional:_ photo links, so the photos are filled in automatically (4.6). Without them you drag 9 photos into
   each product in Seller Center.

**Every time**

1. Check every book of a set in the review window (all green). The set is now **ready**.
2. Double-click **`shopee.bat`** (= `python -m pipeline shopee`). It makes
   - `data\listings\shopee\shopee_upload_<date-time>.xlsx` - one row per ready set, in Shopee's template, and
   - `data\listings\photos\<set code>\01.jpg ... 09.jpg` - photo 1 is the numbered front photo with the set code;
     photos 2-9 show each book's front and back side by side, with its number;

   and opens the folder. Those sets are now **listed**, their books too; a set is never put in two upload files.

3. **You:** Seller Center → 我的商品 → 新增商品 → 大量上傳 → upload the file. The sets appear under **尚未刊登**
   (not yet published). For each: add the photos `01.jpg` to `09.jpg` in order (unless they have links), glance at
   it, and **publish** (刊登).
4. _Optional:_ paste the product's Shopee link into the review window → **Sell sets** → **Save link** (or
   `python -m pipeline shopee-link <set code> <link>`), so you can open it from there.
5. **Orders.** Seller Center → 訂單 → 我的銷售 → **匯出** (export) the order report. Put the file in
   `data\listings\shopee\import` and double-click `shopee.bat` again. Ordered sets become **reserved**, completed
   orders **sold** (its books too, with price, fees and tracking number), cancelled orders put the set back on sale.
   If the set is also on another platform, it tells you to take it down there. Read files move to `import\done`.
   Shopee's report is password protected (usually the last 6 digits of your phone): put the password in
   `SHOPEE_EXPORT_PASSWORD` and it is opened automatically.

**Each set's code** is its batch + set id, e.g. `20261008-153012-S01` - the start of its books' skus
(`20261008-153012-S01-03` is book 3). It is the product's 主商品貨號 (parent SKU), in its title and on photo 1, so every
Shopee file and order can be traced to its books.

**The review window → Sell sets** (`Ctrl+L`) is where the sets are sold: one row per set - the thing that becomes one
Shopee product - with its status, listing title (max 60 characters), its price - **original price** (the books' prices added up), **whole-set
discount** (20% unless you change it) and the resulting **set price** (type either one - the discount or the set
price - and the other follows: 1,000 with 25% off = 750, or type 800 and the discount becomes 20%; the one you typed
last is what is kept) - and the description buyers read (with
a character count - Shopee allows 3,000), its Shopee status / link / order, and **Make Shopee upload file** at the top
(= `shopee.bat`). Change any of them and **Save set**: your text is kept
from then on (**Back to automatic text** undoes that). There you can also change a set's status by hand - sold at a
market, withdrawn, or ready again after you deleted it on Shopee (`python -m pipeline set-status <code> <status>`).

**If Shopee rejects the upload**, Seller Center offers a file with the reason per row. Fix it (often `.env`), then
`python -m pipeline shopee --redo <set code>` makes a new upload file for that set.

### 4.6 Photo links (optional)

Shopee's bulk upload takes photos only as web links. To have them filled in, the photos must be online, e.g. in a free
**Cloudflare R2** bucket (10 GB free; Cloudflare asks for a card or PayPal to switch R2 on, even on the free plan).
Shopee copies each photo once during the upload and shows its own copy, so R2's simple `r2.dev` address is enough
here (it is rate-limited, which matters for a website, not for this):

1. [dash.cloudflare.com](https://dash.cloudflare.com) → R2 → create a bucket (e.g. `books`) → Settings → **Public
   access: allow r2.dev subdomain**. Note the public URL (`https://pub-....r2.dev`).
2. R2 → **Manage API tokens** → create a token with **Object Read & Write** for that bucket. Note the access key id,
   secret and the S3 endpoint (`https://<account id>.r2.cloudflarestorage.com`).
3. In `.env`: `IMAGE_PUBLIC_URL=https://pub-....r2.dev`, `S3_ENDPOINT_URL=...`, `S3_BUCKET=books`,
   `S3_ACCESS_KEY_ID=...`, `S3_SECRET_ACCESS_KEY=...`, and install the uploader once:
   `.\.venv\Scripts\python.exe -m pip install boto3`

The photos are then uploaded by `shopee.bat` and their links written into the upload file. Anyone with a link can see
that photo (like on Shopee itself). Keep the keys in `.env` only.

### 4.7 Bot commands

| Command             | What it does                                                                    |
| ------------------- | ------------------------------------------------------------------------------- |
| `/start` or `/help` | how to send photos, and your Telegram user id                                   |
| `/status`           | where you are: which set, how many back photos saved                            |
| `/undo`             | removes the last photo you sent                                                 |
| `/process`          | processes every photo sent so far (`/process 20` also checks you sent 20 books) |
| `/todo`             | which books still need fixing, and how many just need a quick check             |

---

## 5. How books and prices are found

**Book details** (title, author, publisher, year, pages, genre). Taiwanese ISBNs (978-957, 978-986, 978-626) are
asked, in order (`BOOK_PROVIDERS_TW`): **Google Books → NCL → eslite → books.com.tw → Open Library**; other ISBNs:
Open Library → Google Books → ISBNdb. Later sources only fill what is still empty, and the search stops once title,
author, publisher, year and genre are known. For Taiwanese books the **Chinese** title and genre are always preferred
over English ones, so a book Google only knows in English is looked up further. Every answer is remembered in
`data\cache`, so processing a book again costs nothing.

- **NCL** (國家圖書館 全國新書資訊網, Taiwan's ISBN agency) is searched by ISBN in its catalogue. When the title in the
  result is a link, the full record behind it gives the registered price (定價) and the subject heading (主題標題).
- **Genre** is stored as _overarching > most specific_, e.g. `童書 > 冒險／驚悚小說`: from NCL's subject heading,
  eslite's category levels, books.com.tw's 本書分類 / breadcrumb, or Google's category. Navigation steps that are not
  genres (首頁, 中文書, the book's own title ...) are left out.

**Market price** = the list price (定價) of a **new, printed** copy, first match wins:

1. the small **price barcode** printed next to the ISBN barcode, read from your back photo (free, instant)
2. **NCL** - the price the publisher registered (finds out-of-print books)
3. the ISBN in eslite's and books.com.tw's own search
4. a web search for the ISBN, e.g. `9789574760190 site:eslite.com` - only with a Tavily / LangSearch key
5. the title in the shops' search → the same book in **another printed edition** (same title and author, Taiwanese
   edition, never a boxed set or another volume)
6. one web search for title + author

E-books and audiobooks are never used. Step 5-6 prices are marked `similar` in the `market_match` column and in the
review window, so you can check them.

**Your price** = market price × `PRICE_RATIO` (40%) in the **same currency** - 300 TWD → 120 TWD. A price you type or
caption is never changed automatically.

---

## 6. What is stored, and statuses

Everything lives in **`data\books.csv`** (one row per book). Edit it with the review window rather than Excel - Excel
turns ISBNs into `9.78E+12` and locks the file.

| Column                                                    | Meaning                                                                                                                                                                                                                                                                       |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sku`, `batch`, `set_id`, `position`                      | unique id; when it was processed; set (S01, S02 ...); number in the set (1-10)                                                                                                                                                                                                |
| `isbn13`, `title`, `author`, `publisher`, `year`, `pages` | the book                                                                                                                                                                                                                                                                      |
| `genre`                                                   | the book's genres, broadest first, separated by `>`: e.g. `童書 > 冒險／驚悚小說` (one chip each in the review window). Only `>`, `\|` and `;` separate genres - never `/` or `／` (part of names like 冒險／驚悚小說), commas (`Body, Mind & Spirit` is one genre) or spaces |
| `condition`                                               | one of the five grades below (required)                                                                                                                                                                                                                                       |
| `price`, `currency`, `price_basis`                        | your price (required) and how it was set: `40% of 300 TWD`, `caption` or `manual`                                                                                                                                                                                             |
| `market_price`, `market_currency`                         | list price of a new copy                                                                                                                                                                                                                                                      |
| `market_source`, `market_url`                             | where it was found, and the page to check it                                                                                                                                                                                                                                  |
| `market_match`, `market_isbn`                             | `exact`, `similar` (another printed edition) or `manual`; the ISBN of the edition priced online (blank when the price came from the price barcode on the book itself)                                                                                                         |
| `front_photo`                                             | this book's own cover, cut out of the set's front photo (`data\processed\<batch>\S01\01\cover.jpg`)                                                                                                                                                                           |
| `set_photo`, `back_photo`                                 | the whole set's front photo, and this book's back photo (the whole back cover, with the barcode)                                                                                                                                                                              |
| `front_back_photo`                                        | front + back side by side in one square picture, made with the cover cut-out (`...\01\front_back.jpg`)                                                                                                                                                                        |
| `status`, `errors`                                        | see below; `errors` names what is still missing                                                                                                                                                                                                                               |
| `book_source`                                             | where the book's details (title, author, publisher, year, genre ...) came from, e.g. `google+ncl`; `+manual` once you changed them in the review window. The market price's origin is `market_source`.                                                                        |
| `notes`, `created_at`                                     | bookkeeping                                                                                                                                                                                                                                                                   |

A book's set is `batch` + `set_id`; the set's code `<batch>-<set_id>` is the start of the book's `sku`. Whether a book
is listed or sold follows its set (6.3).

### 6.1 Condition grades

The five grades used by [TAAZE 讀冊生活](https://www.taaze.tw/), Taiwan's largest used-book marketplace, so Taiwanese
buyers know them. Listings show the Chinese name. The descriptions are a practical guide in the spirit of those
grades (TAAZE's staff judge each book; they don't publish exact rules).

| Grade                           | Code       | Means                                                                                                                                    |
| ------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **全新** New                    | `new`      | Unread. No marks, wear or yellowing; looks as it did in the shop.                                                                        |
| **近全新** Like new _(default)_ | `like_new` | Read carefully once or twice. No writing or highlighting; at most tiny shelf wear on edges or corners.                                   |
| **良好** Good                   | `good`     | Clearly read, but clean and complete: light wear on cover/corners or slight yellowing; no or very little writing.                        |
| **普通** Fair                   | `fair`     | Obvious wear: creases, yellowing, foxing (書斑), some writing or highlighting, a name on the first page. All pages present and readable. |
| **差強人意** Poor               | `poor`     | Heavy wear: lots of writing, water marks, loose or damaged pages or cover. Readable, priced as such.                                     |

Books saved earlier as `acceptable` become `fair` automatically.

### 6.2 Statuses

| Status           | Review window      | Meaning                                                                      |
| ---------------- | ------------------ | ---------------------------------------------------------------------------- |
| `needs_manual`   | red: to fix        | not identified yet: barcode unreadable, ISBN wrong, or no title/author found |
| `enriched`       | red: to fix        | identified, but price or condition missing, or a value is invalid            |
| `to_check`       | amber: quick check | complete - waiting for you to glance at it and press Save                    |
| `validated`      | green: checked     | checked by you; when the whole set is, the set is ready to sell              |
| `listed`, `sold` | -                  | its set is on sale / sold (follows the set's status)                         |

### 6.3 Sets: `data\sets.csv`

One row per set, the same whatever platform it is sold on. Made and kept up to date from `books.csv` every time
`shopee.bat`, `python -m pipeline sets` or the set view runs.

| Column                                                | Meaning                                                                                                                                                                                                                    |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `set_code`                                            | `<batch>-<set_id>`, e.g. `20261008-153012-S01`                                                                                                                                                                             |
| `book_count`                                          | books in the set                                                                                                                                                                                                           |
| `set_title`                                           | the listing title (max 60 characters)                                                                                                                                                                                      |
| `original_price`, `discount`, `set_price`, `currency` | the books' prices added up, the whole-set discount in % (default `SET_DISCOUNT`, 20), and the set's price = original minus the discount, in TWD (e.g. 1,000 - 20% = 800); type the price yourself and the discount follows |
| `weight_g`                                            | estimated weight (books x `SET_WEIGHT_PER_BOOK_G`) - Shopee needs one; store pickup charges a flat fee per parcel up to 5 kg                                                                                               |
| `description`                                         | what buyers read: every book by number - title, author, publisher, condition, price                                                                                                                                        |
| `images`                                              | its photos, in order                                                                                                                                                                                                       |
| `status`, `errors`                                    | see below; `errors` says what stops a draft set                                                                                                                                                                            |
| `sold_on`, `sold_at`, `sold_price`                    | where (`shopee`, or `manual`), when and for how much it sold                                                                                                                                                               |
| `custom`                                              | the fields you changed by hand - kept instead of the automatic text                                                                                                                                                        |
| `created_at`, `updated_at`, `notes`                   | bookkeeping                                                                                                                                                                                                                |

| Set status  | Meaning                                                              | Its books   |
| ----------- | -------------------------------------------------------------------- | ----------- |
| `draft`     | a book is not checked yet, or not priced in TWD (`errors` says)      | as they are |
| `ready`     | every book checked - the next `shopee.bat` puts it in an upload file | validated   |
| `listed`    | in an upload file / on Shopee                                        | listed      |
| `reserved`  | ordered; the order is not finished (or a return is open)             | listed      |
| `sold`      | order completed (or marked sold by hand)                             | sold        |
| `withdrawn` | taken off sale (by you, or seen as delisted on Shopee)               | validated   |

### 6.4 Listings: `data\listings.csv`

One row per set **per platform**, so the same set can later also go on Facebook Marketplace. The columns come in
groups: first those every platform has, then one group per platform.

| Group            | Column                                                                              | Meaning                                                                          |
| ---------------- | ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| every platform   | `set_code`, `platform`                                                              | which set, where (`shopee`)                                                      |
|                  | `status`                                                                            | `exported` (in an upload file), `live` (on sale), `sold`, `delisted`, `rejected` |
|                  | `title`, `price`, `currency`, `url`                                                 | as listed; the product's link                                                    |
|                  | `export_file`, `exported_at`                                                        | the upload file it is in, and when it was made                                   |
|                  | `listed_at`, `updated_at`, `delisted_at`, `last_synced_at`, `errors`                | dates; when Shopee's files last mentioned it; what to do                         |
| Shopee           | `shopee_item_id`, `shopee_sku`                                                      | Shopee's product number; the parent SKU (= set code)                             |
|                  | `shopee_category_id`, `shopee_condition`, `shopee_logistics`, `shopee_days_to_ship` | as uploaded (二手 = used)                                                        |
|                  | `shopee_order_sn`, `shopee_order_status`, `shopee_tracking_no`                      | the order, its status, the parcel's tracking number                              |
|                  | `shopee_fees`, `shopee_payout`                                                      | Shopee's fees on the order, and what you receive                                 |
| Facebook (later) | `fb_...`                                                                            | added the same way when Facebook Marketplace is supported                        |

---

## 7. Commands

Run from the `Book Selling Pipeline` folder as `python -m pipeline <command>` (see 1.2). The everyday ones have
buttons or `.bat` files; the rest are for fixing and checking.

| Command                                                 | What it does                                                                                                                                                                                                                                                                     |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bot`                                                   | runs the Telegram bot (= `bot.bat`)                                                                                                                                                                                                                                              |
| `review`                                                | opens the review window (= `review.bat`)                                                                                                                                                                                                                                         |
| `todo` / `status`                                       | what still needs fixing / how many books per status                                                                                                                                                                                                                              |
| `shopee`                                                | sells on Shopee (= `shopee.bat`): reads Shopee's files in `data\listings\shopee\import`, then makes the upload file for every ready set. `--redo <set code>` makes a new upload file for a set again; `--no-export` only reads; files can also be given: `shopee Order.all.xlsx` |
| `sets`                                                  | every set by status, with price, title and what stops it                                                                                                                                                                                                                         |
| `set-status <code> <status>`                            | change a set by hand: `ready`, `listed`, `sold`, `withdrawn` (`--note "sold at the fair"`)                                                                                                                                                                                       |
| `shopee-link <code> <link>`                             | save the Shopee link of a set you published                                                                                                                                                                                                                                      |
| `export-sets`                                           | a plain listing folder per finished set (photo + text, any platform). `--set-price 40 --set-currency NZD` for one bundle price, `--with-barcodes`, `--force` to redo                                                                                                             |
| `ingest`                                                | processes `data\inbox` without the bot (photos copied there yourself)                                                                                                                                                                                                            |
| `market`                                                | finds market prices for books that have none. `--redo-similar` searches again for books priced from another edition (or an e-book, by older versions); `--reprice` recomputes automatic prices after changing `PRICE_RATIO`; `market <isbn>` tests one book                      |
| `titles`                                                | gives older Taiwanese books saved with an English title their Chinese title                                                                                                                                                                                                      |
| `genres`                                                | looks up the genre of books saved before genres existed                                                                                                                                                                                                                          |
| `segment <photo>`                                       | shows how a front photo is cut into books (`--covers folder` also saves the cut-outs)                                                                                                                                                                                            |
| `covers`                                                | cuts each book's own cover out of the set photo for books processed before this existed (`--redo` cuts all not-yet-listed covers again, e.g. after an update)                                                                                                                    |
| `lookup <isbn>`                                         | shows what each book source returns for one ISBN (`--fresh` ignores the cache)                                                                                                                                                                                                   |
| `check-barcode <photo>`                                 | tries to read the barcode in a photo (`--effort max` tries hardest)                                                                                                                                                                                                              |
| `orient <photo>`                                        | helps you find `ROTATE` for sideways front photos                                                                                                                                                                                                                                |
| `fill`, `enrich`, `validate`, `labels`, `quota`, `init` | bulk-fill condition/price; re-look-up books whose ISBN you typed; recheck all statuses; printable number labels; Google calls used today; show the data folders                                                                                                                  |

---

## 8. Troubleshooting

| Problem                                                  | What to do                                                                                                                                                                                   |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A bot reply skips a number (`barcode 6/10` after `4/10`) | a photo didn't arrive: `/undo` back to the gap, or send the missing photo again before the next one                                                                                          |
| `NOT SAVED: this file is .. MB`                          | Telegram bots can't receive files over 20 MB - send it as a normal photo                                                                                                                     |
| Numbers on the review sheet are on the wrong books       | the front photo is sideways: section 3.2 (`orient`), or photos were sent out of order                                                                                                        |
| A cover picture is cut badly                             | `python -m pipeline segment <front photo>` shows the boxes; leave gaps between books, use a plainer surface, or `REGION` to ignore clutter around the books; `SEGMENT=grid` as a last resort |
| Cover pictures are blurry                                | send the front photo as a File (Telegram shrinks normal photos)                                                                                                                              |
| A barcode won't read                                     | type the ISBN in the review window, or test the photo with `check-barcode`                                                                                                                   |
| No market price for many books                           | add a Tavily key (section 3.1); if books.com.tw blocks you, its pages are saved in `data\cache\debug`                                                                                        |
| `books.csv is busy`                                      | close it in Excel / another program                                                                                                                                                          |
| The bot window shows odd characters like `←[1A`          | set `PLAIN_PROGRESS=1` in `.env`                                                                                                                                                             |
| `shopee.bat`: "No category given"                        | not an error: Shopee suggests a category for each set under 尚未刊登 - check it there, or put a code from the list it shows in `SHOPEE_CATEGORY_ID`                                          |
| Shopee rejects the upload file                           | download Seller Center's failure file for the reasons; fix, then `python -m pipeline shopee --redo <set code>`. If Shopee says the file itself is invalid, open it in Excel and save it once |
| The order report "could not be opened"                   | it is password protected: `SHOPEE_EXPORT_PASSWORD` in `.env`, or open it in Excel and save it without a password                                                                             |
| A set is `draft` although all books are green            | `python -m pipeline sets` says why - usually a book priced in another currency than TWD                                                                                                      |
| Anything else                                            | run the step again from the command line - it prints the exact error                                                                                                                         |

### Updating to a new version

Your `.env`, `data` and `.venv` are never part of an update. With the project in Git:

1. `git status` - commit or undo your own changes first.
2. Delete everything except `.git`, `.env`, `data` and `.venv`:
   `Get-ChildItem -Force | Where-Object { '.git','.env','data','.venv' -notcontains $_.Name } | Remove-Item -Recurse -Force`
3. Copy the **contents** of the new `Book Selling Pipeline` folder from the zip into your folder.
4. `git add -A`, `git status` (check the list), `git commit -m "Update"`, `git push`.
5. `python -m pip install -r requirements.txt`, and `git diff HEAD~1 -- .env.example` to see new or changed settings
   worth copying into your `.env`.

`books.csv` from an older version is converted the first time it is saved (e.g. the column `source` became
`book_source`, `barcode_photo` became `back_photo`; `barcode_addon`, `trademe_id`, `ebay_id` and `fb_status` are gone - selling is tracked in `sets.csv`
and `listings.csv`). Books you had marked `listed` / `sold` yourself keep that: their set starts as listed / sold. After an update that cuts covers better, `python -m pipeline covers --redo`
cuts the covers of books not yet listed again. `python -m pipeline covers` (no `--redo`) makes the front + back pictures
of books processed before they existed.

Settings that no longer exist are simply ignored (`SET_SPLIT`, `BACK_ROTATE`, `PHOTO_ORDER`, `GOOGLE_COUNTRY`,
`BRAVE_API_KEY`, `MARKET_EBOOK`). If your `.env` was made from an older version, check these - your old values
override the new defaults:

- `BOOK_PROVIDERS_TW=google,ncl,eslite,books_tw,openlibrary`
- `LOOKUP_STOP_WHEN=title,author,publisher,year,genre`
- `MARKET_PROVIDERS=barcode,ncl,eslite,books_tw`
- `SET_DISCOUNT=20%` (older `.env` files say 0) and `SHOPEE_CATEGORY_NAME=其他書籍` (older ones say 二手書, which
  Shopee doesn't have)

---

## 9. Project layout

```
Book Selling Pipeline/
├── README.md, TELEGRAM_SETUP.md     this guide and the bot setup
├── .env.example → .env              settings (your copy: .env)
├── bot.bat, review.bat, shopee.bat  double-click launchers
├── requirements.txt                 what pip installs (requirements-dev.txt adds pytest)
├── docs/                            the flow diagram
├── data/                            created on first run: inbox, processed photos, books.csv, sets.csv,
│                                    listings.csv, listings (Shopee upload files, set photos), cache
├── tests/                           automated tests (python -m pytest)
└── pipeline/                        the program  (python -m pipeline ...)
    ├── __main__.py                  the commands
    ├── core/                        settings, books.csv, web requests, checks, live progress
    ├── photos/                      barcode reading, front-photo grid, inbox, splitting into sets
    ├── sources/                     book details, market prices, web search
    └── apps/                        Telegram bot, review window, editing, sets, Shopee
```
