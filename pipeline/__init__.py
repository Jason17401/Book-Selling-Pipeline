"""Book Selling Pipeline: photos of book sets -> ISBNs -> book data and market prices -> review -> listings.

  core/     settings, books.csv, web requests, checks, progress
  photos/   barcodes, front-photo grid, inbox, sets
  sources/  book data, market prices, web search
  apps/     Telegram bot, review window, listings

Run: python -m pipeline --help
"""
__version__ = "1.0.0"
