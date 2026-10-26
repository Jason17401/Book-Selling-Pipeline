# Daily use

The routine once [SETUP.md](SETUP.md) is done: photograph → send → process → review → sell. Two double-click files
do all the everyday work: **`bot.bat`** (receive and process photos) and **`review.bat`** (check the books, then sell
them from its **Sell products** window - Shopee's upload file, order reports, everything).

1. [Start the bot](#1-start-the-bot)
2. [Send a set (in Telegram)](#2-send-a-set-in-telegram)
3. [Process](#3-process)
4. [Review and fix (on the computer)](#4-review-and-fix-on-the-computer)
5. [Sell on Shopee](#5-sell-on-shopee)
6. [Bot commands](#6-bot-commands)
7. [All commands](#7-all-commands)
8. [Troubleshooting](#8-troubleshooting)

**Words used here.** A **set** is the books of one front photo (8 by default, photographed together). A **product** is
what you sell as one thing: a whole set (the default), or - when you choose it for a set - each of its books on its
own. Each product has a **product code**
(for a set: batch + set id, e.g. `20261008-153012-S01`). A **listing** is a product on one platform (Shopee now).
What each file stores: [DATA_AND_STATUSES.md](DATA_AND_STATUSES.md).

---

## 1. Start the bot

Double-click **`bot.bat`** and leave its window open while you work. It shows each book being looked up, live.

## 2. Send a set (in Telegram)

1. Send the **front photo** on its own and wait for the reply `Set 1: FRONT photo saved`.
2. Select the **8 back photos in book order** and send them in **one go** (one album). Never select more than 10
   at once - Telegram splits bigger selections into albums that can arrive out of order.
3. The bot answers once the album has arrived: `Set 1: barcode 8/8 saved. Set complete`.
4. Next set: front photo again, then its 8 back photos. Send as many sets as you like before processing.

**Captions** (optional, typed with a photo):

| Caption | On the front photo | On a back photo |
|---|---|---|
| a condition: `全新` `近全新` `良好` `普通` `差強人意` (or `new` `as new` `good` `fair` `poor`) | condition of every book in the set | condition of that book |
| condition + price: `良好 150`, `good 150 TWD` | condition + price for every book | condition + price for that book |
| `retake` | - | replaces the previous back photo (send it right after the bad one) |
| `front` | starts a new set early (a set of fewer than 8 books) | - |

Without a caption, books are 近全新 as new (`as_new`) and priced from the market price. The grades are explained in
[DATA_AND_STATUSES.md](DATA_AND_STATUSES.md#condition-grades).

## 3. Process

Send **`/process`**. One message in the chat shows a progress bar and what is being looked up; any book the pipeline
couldn't identify gets its own `NEEDS MANUAL` message. At the end you get a summary, the review sheet for each set, and
the list of books that still need something.

## 4. Review and fix (on the computer)

Double-click **`review.bat`** (the bot can keep running). On the left, the books that need you, **grouped by set**:
each set is a heading - `Set S01 · batch 20261008-153012` (set + batch together identify it) with how many books still
need something - and its books below it as `#3 Title`. Click a set heading to fold it away or open it again.

On the right, the chosen book: four photos - **its front** (cut out of the set photo), **its back photo**, **front +
back** side by side (the picture its listing uses) and **the whole set** with every book's number on it and this
book outlined in blue. Click one to see it big: it opens fitted to the window; **+ Bigger / − Smaller** (or `+` / `-`,
a left / right click on the photo, `Ctrl`+mouse wheel) zoom in and out, **Fit to window** (`0`) shows it whole again,
**Actual size** (`1`). **Genres** are chips: × removes one, **+ Add genre** adds one (type it, Enter).

What each colour means:

- **Red - to fix:** something is missing or wrong (e.g. no ISBN, no price). The fields to fix are outlined in red.
- **Amber - quick check:** everything was found. Glance at the photos and fields; if they look right just press
  **Save / looks right** (or `Ctrl+Enter` to save and go to the next one) - nothing needs typing.
- **Green - checked:** you have saved it. A set's product goes on sale when all its books are green.

- **Barcode not read:** read the 13 digits under the barcode on the back photo (click it to see it big), type them, press
  **Look up**, check, **Save**.
- **Price missing:** no market price was found. Type your price (and currency), or find the book online - **Search the
  web** opens a Google search; paste the shop's product link into **Shop link** and press **Use link**.
- **"Other edition" note under the market price:** the price came from another printing of the same book (same
  title and author, different ISBN/year) - check it looks right.
- **Whole set** fills an empty condition or price for every book of the set at once.
- **Sell this set** (in the Whole set box) chooses how *this* set is sold, before you open Sell products:
  **As one product** (the default - all its books together, as below) or **Each book on its own**: every book of the
  set becomes its own product, ready as soon as that book is green (no waiting for the others). The set photos just
  save photo taking; the pipeline cuts each book out of them. The set heading on the left then says "sold per book".
  It can't be changed while the set - or one of its books - is listed, ordered or sold (set it to Withdrawn first).

`Ctrl+S` saves, `Ctrl+Enter` saves and jumps to the next red or amber book (into the next set too), `Ctrl+R` refreshes,
`Ctrl+L` opens **Sell products** (5). Tick **Show checked books too** to see the green ones again.

**How books and prices are found** (book sources, market price steps, your price = 40% of a new copy):
[README.md](README.md#how-books-and-prices-are-found).

## 5. Sell on Shopee

Everything happens in the review window: **Sell products** (button at the top right, or `Ctrl+L`). What a product
looks like depends on how its set is sold (**Sell this set**, section 4):

| | A set as one product (default) | Each book on its own |
|---|---|---|
| Products | one for the set | one per book (8 for a set of 8) |
| Product code / parent SKU | the set's, e.g. `20261008-153012-S01` | the book's sku, e.g. `20261008-153012-S01-03` |
| Title | `【<code>】二手書8本一套` | the book's title (a title under 10 characters - Shopee's minimum - gets its author added) |
| Price | the books' prices added up, minus the whole-set discount (`SET_DISCOUNT`) | the book's own price (no discount unless you type one) |
| Photos | 9: the numbered set photo, then each book's front + back | 2: its front (cut out of the set photo - the cover image) and its back |
| Description | every book by its number on the photo, the set price, `description_footer.txt` | the book's details (title, author, publisher, year, pages, genre, ISBN, condition, price - no numbering), `description_footer_book.txt` |

Stock and minimum purchase (1 each), category, delivery and the rest are the same either way.

### Every time

1. In `review.bat`, check every book of a set (**Save / looks right** until all are green). Its product is now
   **ready**.
2. Click **Sell products**, choose the product and glance at it: title, price, quantity in stock and minimum
   purchase, description (change anything, then **Save product**).
3. Click **Make Shopee upload file** (top right). It
   - fills Shopee's template (from `data\listings\shopee\template\`) with one row per ready product and saves it as
     `data\listings\shopee\uploads\shopee_upload_<date-time>.xlsx`,
   - makes each product's photos `data\listings\photos\<product code>\01.jpg ... 09.jpg` - photo 1 is the numbered
     front photo with the code; photos 2-9 show each book's front and back side by side, with its number - and puts
     them online when photo links are set up ([SETUP.md step 7](SETUP.md#7-photo-links-photos-online-automatically)),
   - **opens the uploads folder** and tells you what it made.

   Those products are now **listed**, their books too; a product is never put in two upload files. Titles and
   descriptions go in exactly as they are in Sell products (unsaved edits there are saved first - you are asked).
4. **You:** Seller Center → 我的商品 → 新增商品 → 大量上傳 → upload the file. The products appear under **尚未刊登**
   (not yet published). For each: if its photos have no links, click **Open its photos** in Sell products and drag
   `01.jpg` to `09.jpg` in; glance at it, and **publish** (刊登).
5. *Optional:* paste the product's Shopee link into Sell products → **Save link**, so you can open it from there.

### Orders

Seller Center → 訂單 → 我的銷售 → **匯出** (export) the order report. Then in Sell products click **Read Shopee
report...** and choose the file (it stays where it is). Ordered products become **reserved**, completed orders
**sold** (their books too, with price, fees and tracking number), cancelled orders put the product back on sale. If it
is also on another platform, you are told to take it down there. (A report you drop into
`data\listings\shopee\import\` instead is read by the next **Make Shopee upload file** and moved to `import\done`.)

### The Sell products window

| Part | What it does |
|---|---|
| **Read Shopee report...** (top) | reads the order report / product list you exported from Seller Center |
| **Make Shopee upload file** (top) | the upload file for every ready product, photos online, uploads folder opened |
| the list (left) | every product, with its status and price |
| **Listing** | **title** (max 60 characters; automatically `【code】二手書8本一套` - "set of 8 second-hand books"); **original price** (the books' prices added up), **whole-set discount** (20% unless you change it) and the resulting **price** - type either the discount or the price and the other follows (1,000 with 25% off = 750, or type 800 and the discount becomes 20%; the one you typed last is kept); **Quantity**: how many are **in stock** (庫存) and the **minimum purchase** (最低購買數量, the fewest a buyer may order at once) - both 1 unless you change them, 1 to 999,999, and the minimum never more than the stock; the **description** buyers read (Shopee allows 3,000 characters). **Save product** keeps your text from then on; **Back to automatic text** undoes that (title, price, description - not the quantities) |
| **On Shopee** | its Shopee status, link and order; **Save link**; **Change status** by hand (sold at a market, withdrawn, or ready again after you deleted it on Shopee); **Open its photos**; **Make a new upload file for it** |

**If Shopee rejects the upload**, Seller Center offers a file with the reason per row (失敗原因). Fix it (often in
`.env`, or the product's text), then choose the product and click **Make a new upload file for it**.

## 6. Bot commands

| Command | What it does |
|---|---|
| `/start` or `/help` | how to send photos, and your Telegram user id |
| `/status` | where you are: which set, how many back photos saved |
| `/undo` | removes the last photo you sent |
| `/process` | processes every photo sent so far (`/process 20` also checks you sent 20 books) |
| `/todo` | which books still need fixing, and how many just need a quick check |

## 7. All commands

Run in the `Book Selling Pipeline` folder, in a PowerShell window where `.venv` is active
([SETUP.md 1.3](SETUP.md#13-typing-commands-yourself-optional)), as `python -m pipeline <command>`. The everyday ones have
buttons or `.bat` files; the rest are for fixing and checking.

| Command | What it does |
|---|---|
| `setup` | makes every data folder and shows where they are (every command makes missing folders anyway) |
| `bot` | runs the Telegram bot (= `bot.bat`) |
| `review` | opens the review window (= `review.bat`) |
| `todo` / `status` | what still needs fixing / how many books and products per status |
| `shopee` | the Sell products buttons from the command line: reads Shopee's files in `data\listings\shopee\import`, then makes the upload file for every ready product. `--redo <product code>` = **Make a new upload file for it**; `--no-export` only reads; files can also be given: `shopee Order.all.xlsx` (= **Read Shopee report...**); `--open` opens the uploads folder |
| `products` | every product by status, with price, title and what stops it |
| `product-status <code> <status>` | change a product by hand: `ready`, `listed`, `sold`, `withdrawn` (`--note "sold at the fair"`) |
| `shopee-link <code> <link>` | save the Shopee link of a product you published |
| `ingest` | processes `data\inbox` without the bot (photos copied there yourself) |
| `market` | finds market prices for books that have none. `--redo-similar` searches again for books priced from another edition; `--reprice` recomputes automatic prices after changing `PRICE_RATIO`; `market <isbn>` tests one book |
| `titles` | Taiwanese books saved with English (or missing) title / author / publisher get the Chinese ones (books not yet listed) |
| `genres` | looks up the genre of books that still have none |
| `segment <photo>` | shows how a front photo is cut into books (`--covers folder` also saves the cut-outs) |
| `covers` | makes any missing cover cut-outs and front + back pictures (`--redo` cuts every not-yet-listed book's cover again, e.g. after an update that cuts them better) |
| `lookup <isbn>` | shows what each book source returns for one ISBN (`--fresh` ignores the cache) |
| `check-barcode <photo>` | tries to read the barcode in a photo (`--effort max` tries hardest) |
| `orient <photo>` | helps you find `ROTATE` for sideways front photos |
| `fill`, `enrich`, `validate`, `quota` | bulk-fill condition/price; re-look-up books whose ISBN you typed; recheck all statuses; Google calls used today |

## 8. Troubleshooting

| Problem | What to do |
|---|---|
| A bot reply skips a number (`barcode 6/8` after `4/8`) | a photo didn't arrive: `/undo` back to the gap, or send the missing photo again before the next one |
| `NOT SAVED: this file is .. MB` | Telegram bots can't receive files over 20 MB - send it as a normal photo |
| Numbers on the review sheet are on the wrong books | the front photo is sideways: [SETUP.md step 5](SETUP.md#5-photograph-your-first-set) (`orient`), or photos were sent out of order |
| A cover picture is cut badly | `python -m pipeline segment <front photo>` shows the boxes; leave gaps between books, use a plainer surface, or `REGION` to ignore clutter around the books; `SEGMENT=grid` as a last resort |
| Cover pictures are blurry | send the front photo as a File (Telegram shrinks normal photos) |
| A barcode won't read | type the ISBN in the review window, or test the photo with `check-barcode` |
| No market price for many books | add a Tavily key ([API_KEYS.md](API_KEYS.md#2-tavily-tavily_api_key)); if books.com.tw blocks you, its pages are saved in `data\cache\debug` |
| No market price for a book you can find online | press **Find** in the review window (it always searches again), or paste the shop's link into **Shop link**. What the search tried is in `data\cache\debug\..._market_<isbn>.html` - send it if the book's shop page exists but was not found |
| `books.csv is busy` | close it in Excel / another program |
| The bot window shows odd characters like `←[1A` | set `PLAIN_PROGRESS=1` in `.env` |
| "No Shopee template yet" | put Shopee's downloaded template (any name) into `data\listings\shopee\template\` - [SETUP.md step 6](SETUP.md#6-get-ready-to-sell-on-shopee) |
| "No category given" | not an error: Shopee suggests a category for each product under 尚未刊登 - check it there, or put a code from the list it shows in `SHOPEE_CATEGORY_ID` |
| "photos not put online" | the reason follows (no internet, wrong `IMGBB_API_KEY` ...); fix it, then choose the product in Sell products → **Make a new upload file for it** - or add its photos by hand (**Open its photos**) |
| Shopee rejects the upload file | download Seller Center's failure file for the reasons; fix, then **Make a new upload file for it** in Sell products. If Shopee says the file itself is invalid, open it in Excel and save it once |
| The order report "could not be opened" | it is password protected: `SHOPEE_EXPORT_PASSWORD` in `.env`, or open it in Excel and save it without a password |
| A product is `draft` although all its books are green | Sell products shows why in red under its name (also `python -m pipeline products`) - usually a book priced in another currency than TWD |
| Anything else | run the step again from the command line - it prints the exact error |
