# Setup (once)

Everything you do **once** before using the pipeline, in order - about 30 minutes, plus 10 for Shopee. In short:
**double-click `setup.bat`**, fill in `.env`, then use `bot.bat` and `review.bat`. Afterwards,
[DAILY_USE.md](DAILY_USE.md) is all you need. Written for Windows.

1. [Install](#1-install)
2. [The data folders](#2-the-data-folders)
3. [Set up the Telegram bot](#3-set-up-the-telegram-bot)
4. [API keys](#4-api-keys)
5. [Photograph your first set](#5-photograph-your-first-set)
6. [Get ready to sell on Shopee](#6-get-ready-to-sell-on-shopee)
7. [Photo links: photos online automatically](#7-photo-links-photos-online-automatically)

---

## 1. Install

### 1.1 You need

- **Python 3.11 or 3.12** from [python.org](https://www.python.org/downloads/). In the installer, tick
  **"Add python.exe to PATH"**.
- **Telegram** on your phone (and a Telegram account).

### 1.2 Double-click `setup.bat`

In the `Book Selling Pipeline` folder, double-click **`setup.bat`**. A window shows each step:

| Step | What it does |
|---|---|
| 1 | makes a private Python environment, `.venv`, inside the folder (or reuses it) |
| 2-3 | updates pip and installs everything the pipeline needs into `.venv` - a few minutes the first time |
| 4 | makes your settings file **`.env`** from `.env.example` - only if you have none yet, so your keys are never overwritten |
| 5 | makes the data folders (step 2 below) |
| 6 | runs the tests (about a minute; they use made-up photos and no internet) |

It ends with **"Setup done"** and opens the new `.env` in Notepad. If a step fails (e.g. no internet), it stops and
says which; fix it and double-click `setup.bat` again - repeating it is always safe. Run it again **after every
update** too: it installs anything new and brings your data files up to date.

`.env` holds your settings and keys. You fill in the Telegram part in step 3; everything else already has working
values, and each setting is explained in `.env` itself. Keep keys only in `.env` - it is never uploaded to Git.

### 1.3 Typing commands yourself (optional)

Day to day you only double-click `bot.bat` and `review.bat`. For the extra commands in
[DAILY_USE.md](DAILY_USE.md#7-all-commands), open PowerShell in the folder (in File Explorer type `powershell` in the
address bar and press Enter) and switch it to the project's Python first:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pipeline lookup 9789861371955
```

The prompt then starts with `(.venv)`. If PowerShell says **"running scripts is disabled"**, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, answer `Y`, and try again. The look-up above prints what
each book source found for that ISBN (被討厭的勇氣) - a good first check that the internet sources work.

<details>
<summary>What setup.bat runs, if you'd rather do it by hand (or on macOS / Linux)</summary>

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS / Linux: source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env             # macOS / Linux: cp .env.example .env  (only the first time)
python -m pipeline setup
python -m pytest
```
</details>

---

## 2. The data folders

`setup.bat` made the `data` folder and everything in it (`python -m pipeline setup` shows where each one is):

```
data\
├── inbox\                     photos from the bot wait here until processed
├── processed\                 photos after processing, one folder per batch and set
├── listings\
│   ├── photos\                each product's listing photos (<product code>\01.jpg ...)
│   └── shopee\
│       ├── template\          ← put Shopee's template here (step 6)
│       ├── uploads\           the upload files made for you
│       └── import\            Shopee reports can also be dropped here
└── cache\
```

Nothing needs creating by hand: the bot and the review window also make any missing folder when they start.

---

## 3. Set up the Telegram bot

Follow **[TELEGRAM_SETUP.md](TELEGRAM_SETUP.md)** - about 5 minutes: you create your own bot, put its token and your
user id into `.env`, and send it a first photo. It ends by sending you back here.

---

## 4. API keys

Four free keys make the pipeline find much more on its own: **Google Books** (book details), **Tavily** and
**LangSearch** (web searches for market prices) and **ImgBB** (photos online for Shopee). Each takes a couple of
minutes; **[API_KEYS.md](API_KEYS.md)** walks through getting every one, where it goes in `.env`, which step of the
workflow uses it and how to check it works. Everything also runs without them - you just fill in more by hand.

---

## 5. Photograph your first set

1. Lay the books in a grid: by default **2 rows of 4** (`GRID=2x4`, `SET_SIZE=8`). Book 1 is top-left, then left to
   right along the row, then the next row. Leave a small gap between books if you can, on a plain surface that
   contrasts with the covers - each book's cover picture is cut out of this photo automatically.
2. **Photo 1:** all the front covers together, in landscape, straight from above. Send it **as a File** (in Telegram:
   paperclip → File) for sharp cover pictures - a normal Telegram photo is shrunk to about 1280 px.
3. **Photos 2-9:** the back cover of each book with its barcode, in book order 1 → 8. Fill the frame, keep the
   barcode sharp and without glare. A normal photo is plenty.

Send them as described in [DAILY_USE.md](DAILY_USE.md#2-send-a-set-in-telegram), then look at the **review sheet**
the bot sends back: on each row, the book's cover (cut out of the front photo) and its back photo must be the same
book.

**How the covers are cut out.** The pipeline finds each book on the front photo (where it differs from the table, and
the straight edges between books), straightens slightly turned books, and saves each one at full resolution with a
small margin of table around it (`SEGMENT_MARGIN`, default 4% of the book's size). A book's shadow on the table is not
counted as book, so the cover sits in the middle of its picture, and the margin never reaches past halfway to the next
book. The numbering stays the grid order, so book N's cover always pairs with back photo N. To see how a photo is cut:
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

---

## 6. Get ready to sell on Shopee

**What the pipeline does, and where it stops.** Shopee only opens its automation API to Shopee Mall sellers and
registered companies in Taiwan, and a robot clicking through Seller Center would put your shop at risk. So the pipeline
uses Shopee's own **bulk upload (大量上傳)**: it fills in Shopee's Excel template for you, and **you upload that file**
in Seller Center (about a minute), then check and publish there. Everything before and after that is automatic.

1. **Shopee's template.** Seller Center → 我的商品 → 新增商品 → **大量上傳** → download the **基礎模板** (basic
   template) and put the file - **keep the name Shopee gave it** - into `data\listings\shopee\template\`. The only (or
   first, by name) Excel file in that folder is used, so to change templates replace the file. If it is an old `.xls`
   file, open it in Excel and **Save As → Excel Workbook (.xlsx)** into the same folder. Shopee lists only the delivery
   options switched on in your shop, so download it again after changing them. **Without the template nothing is
   made** - no upload file, and nothing changes status - since Shopee accepts only its own file. The upload files the
   pipeline makes go to the folder next to it, `data\listings\shopee\uploads\`. (For an English explanation of every
   column, see `Shopee_mass_upload_basic_template_ENGLISH.xlsx` → *Column Guide*, if you kept it.)
2. **Category - optional, nothing to set.** The template marks 分類代碼 as 選填 (optional). Products go in **101573
   其他書籍** (Books & Magazines > Books > Other Books) when the template's category list has it; otherwise the column is
   left blank and Shopee suggests a category for each product under 尚未刊登 - check it there before publishing. To
   always use one, set `SHOPEE_CATEGORY_ID` in `.env` (e.g. 101541 繪本童書 for children's books).
3. **Delivery and price** in `.env`: `SHOPEE_LOGISTICS` (the delivery options to switch on, e.g. 7-ELEVEN, 全家,
   蝦皮店到店), and `SET_DISCOUNT`, the whole-set discount (default 20% off the sum of its books' prices = 8 折;
   changeable per product in **Sell products**). Nothing to weigh or measure: weight and parcel size (重量 / 長度 /
   寬度 / 高度) are optional in the template and left empty.
4. **Order report password:** Shopee's order report is password protected (usually the last 6 digits of your phone).
   Put it in `SHOPEE_EXPORT_PASSWORD` so the report opens automatically.
5. *Optional:* `data\listings\description_footer.txt` - the lines at the end of every set's description, and
   `description_footer_book.txt` - the same for a book sold on its own (both made on the first run; edit freely).
6. *Optional but recommended:* photo links (step 7). Without them you drag 9 photos into each product in Seller
   Center.

---

## 7. Photo links: photos online automatically

Shopee's bulk upload takes photos only as **web links**. With an ImgBB key in `.env` (`IMGBB_API_KEY` - see
[API_KEYS.md](API_KEYS.md#4-imgbb-imgbb_api_key)), **Make Shopee upload file** (in Sell products) puts each product's
photos online on [ImgBB](https://imgbb.com) by itself and writes their links into the upload file - no dragging photos
into Seller Center. Each photo is uploaded once; its link is remembered in
`data\listings\photos\<product code>\links.json` and reused until the photo changes. Without a key, no links are made
and you add the photos by hand (**Open its photos** in Sell products).

Setup done. From now on: **[DAILY_USE.md](DAILY_USE.md)**.
