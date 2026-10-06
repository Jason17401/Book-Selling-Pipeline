# Telegram bot setup

You send your photos to **your own private Telegram bot**, which runs on your computer and saves them for the
pipeline. This page creates that bot. It takes about 5 minutes and is done once.

**Before you start:** you have finished README section 1 (the pipeline is installed and `.env` exists), and you have
Telegram on your phone. The steps below use the phone and the computer side by side.

---

## Step 1 - Create the bot (phone)

1. In Telegram, search for **@BotFather** (the official one has a blue tick) and open the chat.
2. Send `/newbot`.
3. It asks for a **name** - anything, e.g. `My Book Pipeline`.
4. It asks for a **username** - must be unique and end in `bot`, e.g. `my_books_bot`.
5. BotFather replies with a **token** that looks like `7412345678:AAH3k...`. Keep this message open for step 2.

The token is the password to your bot: anyone who has it can control the bot. Don't share it or post it anywhere.
If it ever leaks, send `/revoke` to @BotFather to get a new one.

## Step 2 - Put the token in `.env` (computer)

1. Open `.env` in the `Book Selling Pipeline` folder with Notepad (right-click → Open with → Notepad).
2. Find the line `TELEGRAM_BOT_TOKEN=` and paste the token after the `=` (no spaces, no quotes):
   ```
   TELEGRAM_BOT_TOKEN=7412345678:AAH3k...
   ```
3. Save the file.

## Step 3 - Start the bot and say hello

1. Double-click **`bot.bat`**. A window opens and, after a few seconds, shows
   `Book Selling Pipeline bot @your_bot is running.` Leave this window open - the bot only works while it is.
2. On the phone, open your bot: tap the `t.me/...` link in BotFather's message (or search for its username), then
   press **Start**.
3. The bot replies with instructions, and at the end: **`Your Telegram user id: 123456789`**. Note this number.

Nothing happens? Check the `bot.bat` window: a line about the token means it was pasted wrongly (step 2).

## Step 4 - Make the bot answer only you

Right now anyone who finds your bot could send it photos. Lock it to your account:

1. In `.env`, set your id from step 3:
   ```
   ALLOWED_USER_IDS=123456789
   ```
   (Several people: `ALLOWED_USER_IDS=123456789,987654321` - each finds their id by sending the bot `/start`.)
2. Save, close the `bot.bat` window, and double-click `bot.bat` again. **Restart the bot after every `.env` change.**
3. Send `/start` again - the reply no longer ends with a warning.

## Step 5 - Test it

1. Send any photo to the bot. It replies `Set 1: FRONT photo saved ...`.
2. Send `/undo`. It replies `Removed the last photo.`

Your bot is ready. **Continue with [README section 3 - Before your first set](README.md#3-before-your-first-set).**

---

## Good to know

- **The computer must be on and `bot.bat` running** when you send photos. Photos sent while it is off are not lost:
  Telegram keeps them for about a day and the bot collects them when it starts.
- **Photos or Files?** Normal photos are fine and fastest. "Send as File" keeps full resolution but files over 20 MB
  can't be received by any bot - the bot tells you if one is too big.
- **Albums:** select at most 10 photos at a time (one album). The bot downloads them in parallel and keeps them in the
  order you selected them.
- **Using another computer:** copy the whole folder including `.env`; only one computer may run the same bot at once.
- **Changing the bot's name or picture:** `/mybots` in @BotFather.
