# Data and statuses

What the pipeline stores, in which file, and what every status means. Everything is in the `data` folder (made by
`setup.bat` - [SETUP.md step 2](SETUP.md#2-the-data-folders)).

| File | One row per | Made by |
|---|---|---|
| [`data\books.csv`](#books-databookscsv) | **book** you photographed | the bot / `/process`; you fix it in the review window |
| [`data\products.csv`](#products-dataproductscsv) | **product** - what you sell as one thing: a set of books, or a single book when its set is sold per book | worked out from `books.csv`; you adjust it in **Sell products** |
| [`data\listings.csv`](#listings-datalistingscsv) | product **per platform** (Shopee now; Facebook Marketplace could follow) | the Sell products window, from the upload files it makes and the Shopee reports you give it |

So `products.csv` is the platform-neutral description of what you sell (title, price, description, photos), and
`listings.csv` is where each product is listed and what happened to it there - neither is a copy of Shopee's
template. The Shopee upload file is made from `products.csv` each time, and the result is recorded in
`listings.csv`.

Edit the files with the review window rather than Excel - Excel turns ISBNs into `9.78E+12` and locks the file.

- [Books](#books-databookscsv) · [Condition grades](#condition-grades) · [Book statuses](#book-statuses)
- [Products](#products-dataproductscsv) · [Product statuses](#product-statuses)
- [Listings](#listings-datalistingscsv)
- [Other files and folders](#other-files-and-folders)

---

## Books: `data\books.csv`

| Column | Meaning |
|---|---|
| `sku`, `batch`, `set_id`, `position` | unique id; when it was processed; set (S01, S02 ...); number in the set (1-8 by default) |
| `isbn13`, `title`, `author`, `publisher`, `year`, `pages` | the book |
| `genre` | the book's genres, broadest first, separated by ` > `: e.g. `童書 > 冒險／驚悚小說` (one chip each in the review window). Only `>`, `\|` and `;` separate genres - never `/` or `／` (part of names like 冒險／驚悚小說), commas (`Body, Mind & Spirit` is one genre) or spaces |
| `condition` | one of the five grades below (required) |
| `price`, `currency`, `price_basis` | your price (required) and how it was set: `40% of 300 TWD`, `caption` or `manual` |
| `market_price`, `market_currency` | list price of a new copy |
| `market_source`, `market_url` | where it was found, and the page to check it |
| `market_match`, `market_isbn` | `exact`, `similar` (another printed edition) or `manual`; the ISBN of the edition priced online (blank when the price came from the price barcode on the book itself) |
| `front_photo` | this book's own cover, cut out of the set's front photo (`data\processed\<batch>\S01\01\cover.jpg`) |
| `set_photo`, `back_photo` | the whole set's front photo, and this book's back photo (the whole back cover, with the barcode) |
| `front_back_photo` | front + back side by side in one square picture, made with the cover cut-out (`...\01\front_back.jpg`) |
| `status`, `errors` | see below; `errors` names what is still missing |
| `sell_as` | how its set is sold (the same for every book of the set; review window → Whole set → **Sell this set**): empty = as one product, `book` = each book is its own product |
| `book_source` | where the book's details (title, author, publisher, year, genre ...) came from, e.g. `google+ncl`; `+manual` once you changed them in the review window. The market price's origin is `market_source`. |
| `notes`, `created_at` | bookkeeping |

A book's **set** is `batch` + `set_id` (the books of one front photo). The set's product code `<batch>-<set_id>` is the
start of the book's `sku` (`20261008-153012-S01-03` is book 3 of `20261008-153012-S01`). Whether a book is listed or
sold follows its product.

### Condition grades

The five used-book grades of [TAAZE 讀冊生活](https://www.taaze.tw/), Taiwan's largest used-book marketplace, so
Taiwanese buyers know them - with TAAZE's own definitions (書況標定, grades A-E). `books.csv` stores the English code;
the review window shows both names, and listings show the Chinese one (書況：近全新).

| TAAZE | Grade | Code | Means (TAAZE's definition) |
|---|---|---|---|
| A | **全新** New | `new` | Still in the supplier's original shrink wrap, unopened; no defects. 原供應商膠膜未拆，無瑕疵。 |
| B | **近全新** As new *(default)* | `as_new` | Not shrink-wrapped; signs of reading barely noticeable - like a book on display in a bookshop. 未包膜，翻閱痕跡不明顯，如實體賣場陳列販售之書籍。 |
| C | **良好** Good | `good` | Signs of use, such as folded corners or knocks; not as clean and white as a new book. 有使用痕跡，如摺角、碰撞等，不如新書潔白。 |
| D | **普通** Fair | `fair` | Obvious signs of use, or yellowing / brown or black spots. 有明顯使用痕跡或黃褐色、黑斑等。 |
| E | **差強人意** Poor | `poor` | Faded or blurred printing, or worse. 印刷褪色、模糊或其它更糟之書況。 |

Books saved earlier as `like_new` become `as_new`, and `acceptable` becomes `fair`, automatically.

### Book statuses

| Status | Review window | Meaning |
|---|---|---|
| `needs_manual` | red: to fix | not identified yet: barcode unreadable, ISBN wrong, or no title/author found |
| `enriched` | red: to fix | identified, but price or condition missing, or a value is invalid |
| `to_check` | amber: quick check | complete - waiting for you to glance at it and press Save |
| `validated` | green: checked | checked by you; when every book of a set is, its product is ready to sell (a book sold on its own: at once) |
| `listed`, `sold` | - | its product is on sale / sold (follows the product's status) |

---

## Products: `data\products.csv`

One row per product, the same whatever platform it is sold on. Made and kept up to date from `books.csv` every time
the Sell products window (or `python -m pipeline products` / `shopee`) opens or makes an upload file.

| Column | Meaning |
|---|---|
| `product_code` | the product's code. A set: `<batch>-<set_id>`, e.g. `20261008-153012-S01`. A single book: its `sku`, e.g. `20261008-153012-S01-03`. It is the Shopee parent SKU (主商品貨號), in the title and on photo 1, so every Shopee file and order can be traced to its books |
| `kind` | `set` (the books of one front photo, sold together) or `book` (one book on its own - when its set is sold per book: `sell_as` in books.csv) |
| `book_count` | books in it |
| `title` | the listing title (10-60 characters; automatically `【<code>】二手書8本一套`, "set of 8 second-hand books") |
| `original_price`, `discount`, `price`, `currency` | the books' prices added up, the whole-set discount in % (default `SET_DISCOUNT`, 20), and the product's price = original minus the discount, in TWD (e.g. 1,000 - 20% = 800); type the price yourself and the discount follows |
| `stock`, `min_purchase` | how many you have (Shopee 庫存) and the fewest a buyer may order at once (最低購買數量): whole numbers from 1 to 999,999, both 1 unless you change them in Sell products; the minimum is never more than the stock |
| `description` | what buyers read: every book by number - title, author, publisher, condition, price |
| `images` | its photos, in order (`data\listings\photos\<product code>\01.jpg ...`) |
| `status`, `errors` | see below; `errors` says what stops a draft product |
| `sold_on`, `sold_at`, `sold_price` | where (`shopee`, or `manual`), when and for how much it sold |
| `custom` | the fields you changed by hand - kept instead of the automatic text |
| `created_at`, `updated_at`, `notes` | bookkeeping |

### Product statuses

| Status | Meaning | Its books |
|---|---|---|
| `draft` | a book is not checked yet, or not priced in TWD (`errors` says) | as they are |
| `ready` | every book checked - the next **Make Shopee upload file** puts it in an upload file | validated |
| `listed` | in an upload file / on Shopee | listed |
| `reserved` | ordered; the order is not finished (or a return is open) | listed |
| `sold` | order completed (or marked sold by hand) | sold |
| `withdrawn` | taken off sale (by you, or seen as delisted on Shopee) | validated |

---

## Listings: `data\listings.csv`

One row per product **per platform**, so the same product can later also go on Facebook Marketplace. The columns come
in groups: first those every platform has, then one group per platform.

| Group | Column | Meaning |
|---|---|---|
| every platform | `product_code`, `platform` | which product, where (`shopee`) |
| | `status` | `exported` (in an upload file), `live` (on sale), `sold`, `delisted`, `rejected` |
| | `title`, `price`, `currency`, `url` | as listed; the product's link |
| | `export_file`, `exported_at` | the upload file it is in, and when it was made |
| | `listed_at`, `updated_at`, `delisted_at`, `last_synced_at`, `errors` | dates; when Shopee's files last mentioned it; what to do |
| Shopee | `shopee_item_id`, `shopee_sku` | Shopee's product number; the parent SKU (= product code) |
| | `shopee_category_id`, `shopee_condition`, `shopee_logistics`, `shopee_days_to_ship` | as uploaded (二手 = used) |
| | `shopee_order_sn`, `shopee_order_status`, `shopee_tracking_no` | the order, its status, the parcel's tracking number |
| | `shopee_fees`, `shopee_payout` | Shopee's fees on the order, and what you receive |
| Facebook (later) | `fb_...` | added the same way when Facebook Marketplace is supported |

---

## Other files and folders

| Where | What |
|---|---|
| `data\inbox\` | photos from the bot, waiting for `/process` |
| `data\processed\<batch>\<set>\` | the photos after processing; each book's cut-out cover and front + back picture in `<set>\01\` ... |
| `data\listings\photos\<product code>\` | the listing photos (`01.jpg` ...), and `links.json`: their online links when photo links are set up |
| `data\listings\shopee\template\` | Shopee's bulk-upload template (any file name) |
| `data\listings\shopee\uploads\` | the upload files made for you (`shopee_upload_<date-time>.xlsx`) |
| `data\listings\shopee\import\` | Shopee's order reports / product lists to read; read ones move to `import\done\` |
| `data\listings\description_footer.txt` | the lines at the end of every set's description (edit freely) |
| `data\listings\description_footer_book.txt` | the same for a book sold on its own |
| `data\cache\` | remembered look-ups, thumbnails, saved web pages for debugging |
