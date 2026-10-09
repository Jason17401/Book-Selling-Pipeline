"""Private Telegram bot: send it photos, it saves them to the inbox and tells you where you are in the set.
/process runs the pipeline, /undo removes the last photo, /status shows progress."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import re

from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from ..core import progress as live
from ..core.config import Config
from ..photos.ingest import IMG_EXT, _sidecar, format_summary, list_photos, process_inbox

log = logging.getLogger("pipeline.bot")
ALBUM_QUIET_SECONDS = 3  # reply to an album once no more of its photos have arrived for this long
PARALLEL_DOWNLOADS = 8   # photos downloaded at the same time (order is kept: each file is named by its message number)
BOT_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # Telegram's Bot API refuses to hand bots files bigger than 20 MB
DOWNLOAD_ATTEMPTS = 4                  # a slow/unstable connection gets 3 more tries (after 2, 4, 8 s) before giving up


async def send_with_retries(make_request, attempts: int = 3, sleep=asyncio.sleep) -> bool:
    """Send a reply, retrying on time-outs. make_request() must create a NEW request each time.
    Returns False (instead of crashing) if Telegram never answered."""
    for attempt in range(1, attempts + 1):
        try:
            await make_request()
            return True
        except (TimedOut, NetworkError) as exc:
            log.warning("sending a reply failed (attempt %d/%d: %s)", attempt, attempts, exc)
            if attempt < attempts:
                await sleep(2 ** attempt)
    return False


async def fetch_with_retries(getter, dest, timeout: float, attempts: int = DOWNLOAD_ATTEMPTS, sleep=asyncio.sleep) -> int:
    """Ask Telegram for the file and download it to dest, retrying on time-outs / network hiccups.
    Returns how many attempts it took. A half-written file is removed before each retry and after a failure."""
    for attempt in range(1, attempts + 1):
        try:
            tg_file = await getter(read_timeout=timeout, connect_timeout=timeout)
            await tg_file.download_to_drive(custom_path=dest, read_timeout=timeout * 2, connect_timeout=timeout)
            return attempt
        except (TimedOut, NetworkError) as exc:
            dest.unlink(missing_ok=True)
            if attempt == attempts:
                raise
            log.warning("download attempt %d/%d failed (%s) - retrying", attempt, attempts, exc)
            await sleep(2 ** attempt)
    return attempts

HELP = (
    "For each set of {n}: send ONE photo of all the front covers, then the BARCODE photo (back cover) of each book, "
    "one photo per book: top row left to right, then the next row left to right.\n"
    "I reply with where you are (e.g. 'Set 1: barcode 4/{n}'). If a number is skipped, a photo did not arrive.\n\n"
    "ORDER: send the front photo ON ITS OWN first and wait for my reply, then the {n} barcode photos as ONE selection, "
    "ticked in book order (Telegram keeps the order inside one album of up to 10). Never select more than 10 at once.\n\n"
    "Normal Photos are fine for barcode photos. Each book's cover picture is cut out of the FRONT photo, so for sharp "
    "covers send the front photo as a File (Telegram shrinks normal photos). Files over 20 MB cannot be received by "
    "bots.\n"
    "Captions (all optional): 'retake' = replaces the previous barcode photo; 'front' = starts a new set early (short "
    "set); '良好 150' (or 'good 150') on the front photo = condition/price for the whole set (grades: 全新 近全新 "
    "良好 普通 差強人意).\n\n"
    "/undo - remove the last photo\n/status - where am I\n/process - ingest everything sent so far\n"
    "/todo - which books still need fixing"
)


class Downloads:
    """Counts photos still downloading, so /process, /undo and the replies wait until every photo sent so far is
    saved (downloads run in parallel, so a later photo can finish before an earlier one)."""

    def __init__(self):
        self.count = 0
        self.idle = asyncio.Event()
        self.idle.set()

    def start(self):
        self.count += 1
        self.idle.clear()

    def done(self):
        self.count = max(0, self.count - 1)
        if not self.count:
            self.idle.set()

    async def wait(self, timeout: float) -> bool:
        """True once nothing is downloading (False if that took longer than timeout seconds)."""
        if not self.count:
            return True
        try:
            await asyncio.wait_for(self.idle.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False


def progress_text(cfg: Config) -> str:
    from ..photos.setlayout import describe_progress
    return describe_progress(list_photos(cfg.inbox, 0), cfg.set_size)


def build_app(cfg: Config) -> Application:
    cfg.ensure_dirs()

    downloads = Downloads()
    busy = {"processing": False}
    wait_limit = cfg.bot_timeout * 3 * DOWNLOAD_ATTEMPTS     # longest a download can take, with all its retries

    def allowed(update: Update) -> bool:
        u = update.effective_user
        return bool(u) and (not cfg.allowed_user_ids or u.id in cfg.allowed_user_ids)

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not allowed(update):
            await update.effective_message.reply_text(f"Not authorised. Your user id is {update.effective_user.id}")
            return
        uid = update.effective_user.id
        lock = ("" if cfg.allowed_user_ids else
                f"\n\nWARNING: anyone who finds this bot can use it. Put your id in .env as ALLOWED_USER_IDS={uid} "
                "and restart the bot.")
        await update.effective_message.reply_text(HELP.format(n=cfg.set_size) + f"\n\nYour Telegram user id: {uid}" + lock)

    async def on_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not allowed(update):
            return
        msg = update.effective_message
        downloads.start()
        try:
            if msg.photo:  # Telegram-compressed photo; use the largest size
                size, ext = msg.photo[-1].file_size or 0, ".jpg"
                getter = msg.photo[-1].get_file
            else:  # "Send as file" keeps the original resolution
                doc = msg.document
                ext = "." + (doc.file_name or "x.jpg").rsplit(".", 1)[-1].lower()
                if ext not in IMG_EXT:
                    await msg.reply_text(f"Ignored {doc.file_name}: not a photo type I read ({', '.join(sorted(IMG_EXT))}).")
                    return
                size, getter = doc.file_size or 0, doc.get_file
            if size > BOT_DOWNLOAD_LIMIT:
                await msg.reply_text(f"NOT SAVED: this file is {size / 1e6:.0f} MB and Telegram does not let bots download "
                                     "files over 20 MB. Send it as a normal Photo instead (fine for a single book's back), "
                                     "or lower the camera resolution.")
                return
            name = f"{msg.message_id:010d}"  # message_id = the order Telegram delivered them in
            if msg.document and msg.document.file_name:  # keep the phone's own name (IMG_1234) for reference
                orig = re.sub(r"[^A-Za-z0-9._-]", "_", msg.document.file_name.rsplit(".", 1)[0])[:60]
                name = f"{name}_{orig}"
            dest = cfg.inbox / f"{name}{ext}"
            part = dest.with_name(dest.name + ".part")   # not counted as a photo until it is complete
            tries = await fetch_with_retries(getter, part, cfg.bot_timeout)
            part.replace(dest)
            if tries > 1:
                log.info("saved %s after %d attempts", name, tries)
            if msg.caption:
                (cfg.inbox / f"{name}.txt").write_text(msg.caption, encoding="utf-8")
        except Exception as exc:  # never fail silently: a lost photo shifts every book after it
            log.exception("download failed")
            why = ("Telegram did not answer in time - the connection is slow or Telegram is busy"
                   if isinstance(exc, TimedOut) else str(exc) or type(exc).__name__)
            await msg.reply_text(f"NOT SAVED after {DOWNLOAD_ATTEMPTS} tries ({why}). Send this photo again - "
                                 "it goes in the right place as long as you resend it before the next photo.")
            return
        finally:
            downloads.done()
        note = ""
        if msg.media_group_id:
            # an album: answer ONCE when it has finished arriving, not once per photo
            key = (msg.chat_id, msg.media_group_id)
            old = pending.pop(key, None)
            if old:
                old.cancel()
            pending[key] = asyncio.create_task(_reply_later(msg, key, note))
            return
        await downloads.wait(wait_limit)       # count only once the photos sent before this one are saved too
        await msg.reply_text(progress_text(cfg) + note, disable_notification=True)

    pending: dict = {}

    async def _reply_later(msg, key, note):
        try:
            await asyncio.sleep(ALBUM_QUIET_SECONDS)
        except asyncio.CancelledError:
            return
        pending.pop(key, None)
        await downloads.wait(wait_limit)
        await msg.reply_text("Album received. " + progress_text(cfg) + note, disable_notification=True)

    async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if allowed(update):
            await downloads.wait(wait_limit)
            await update.effective_message.reply_text(progress_text(cfg))

    async def undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not allowed(update):
            return
        await downloads.wait(wait_limit)
        photos = list_photos(cfg.inbox, 0)
        if not photos:
            await update.effective_message.reply_text("Nothing to undo - the inbox is empty.")
            return
        last = photos[-1]
        last.unlink()
        side = _sidecar(last)
        if side.exists():
            side.unlink()
        await update.effective_message.reply_text(f"Removed the last photo. Now: {progress_text(cfg)}")

    async def todo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if allowed(update):
            from .editing import todo_text
            await update.effective_message.reply_text(todo_text(cfg, limit=40)[:3900])

    async def process(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not allowed(update):
            return
        expect = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else None
        msg = update.effective_message
        if busy["processing"]:
            await msg.reply_text("Already processing - wait for it to finish.")
            return
        busy["processing"] = True
        try:
            await _process(msg, expect)
        finally:
            busy["processing"] = False

    async def _process(msg, expect):
        if downloads.count:
            await msg.reply_text(f"Waiting for {downloads.count} photo(s) still downloading...",
                                 disable_notification=True)
            if not await downloads.wait(wait_limit):
                await msg.reply_text("Some photos are still not saved - try /process again in a moment.")
                return
        chat = live.ChatProgress(lambda text: msg.reply_text(text, disable_notification=True), asyncio.get_running_loop())
        shower = asyncio.create_task(chat.run())
        # every photo sent so far has been saved (waited for above), so don't wait for files to 'settle'
        run_cfg = dataclasses.replace(cfg, settle_seconds=0)

        def work():   # runs in a worker thread: the terminal display + the chat message follow every step
            with live.terminal(), live.listening(chat):
                return process_inbox(run_cfg, expect=expect)
        try:
            summary = await asyncio.to_thread(work)
        finally:
            chat.close()
            try:
                await shower
            except Exception:   # the progress message is a nicety: never lose the summary over it
                log.warning("progress message failed", exc_info=True)
        from .editing import todo_text
        text = format_summary(summary) + "\n\n" + todo_text(cfg, limit=15)
        for i in range(0, len(text), 3900):  # Telegram's message limit is 4096 characters
            await send_with_retries(lambda chunk=text[i:i + 3900]: msg.reply_text(chunk))
        caption = "Check: on each row, the book from the front photo and its barcode photo must match"
        for sheet in summary.review_sheets:
            def send(path=sheet):
                with open(path, "rb") as f:   # re-open on every try: a failed upload has read the file
                    data = f.read()
                return msg.reply_photo(data, caption=caption, write_timeout=cfg.bot_timeout * 4,
                                       read_timeout=cfg.bot_timeout * 2)
            if not await send_with_retries(send):
                await send_with_retries(lambda p=sheet: msg.reply_text(
                    f"(Could not upload the review sheet - Telegram timed out. It is saved on the computer: {p})"))

    async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
        """Anything that still goes wrong: one clear line in the console (no wall of text) for network
        hiccups, the full details for real bugs - and the bot keeps running either way."""
        err = ctx.error
        if isinstance(err, (TimedOut, NetworkError)):
            log.warning("Telegram connection problem (%s: %s) - the bot carries on", type(err).__name__, err)
            return
        log.error("Unexpected error", exc_info=err)
        msg = getattr(update, "effective_message", None)
        if msg is not None:
            try:
                await msg.reply_text(f"Something went wrong: {err}. The bot is still running.")
            except Exception:
                pass

    async def announce(application):
        """One clear line in the window once the bot is online."""
        try:
            me = await application.bot.get_me()
            name = f"@{me.username}"
        except Exception:
            name = "your bot"
        print(f"\nBook Selling Pipeline bot {name} is running. Keep this window open; close it (or Ctrl+C) to stop.")
        if not cfg.allowed_user_ids:
            print("WARNING: ALLOWED_USER_IDS is empty - anyone who finds the bot can use it. Send it /start to see "
                  "your user id, put it in .env and restart. (TELEGRAM_SETUP.md, step 4)")
        print()

    t = cfg.bot_timeout
    app = (Application.builder().token(cfg.telegram_token)
           .connect_timeout(t).read_timeout(t).write_timeout(t).pool_timeout(t)   # default 5 s is too short for photos
           .get_updates_connect_timeout(t).get_updates_read_timeout(t)
           .concurrent_updates(PARALLEL_DOWNLOADS)          # photos of an album download in parallel, not one by one
           .connection_pool_size(PARALLEL_DOWNLOADS + 4)
           .post_init(announce)
           .build())
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("process", process))
    app.add_handler(CommandHandler("todo", todo))
    app.add_error_handler(on_error)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_image))
    return app


def run(cfg: Config) -> None:
    if not cfg.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is empty in .env - see TELEGRAM_SETUP.md, steps 1-2.")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs every URL, and the bot token is part of the URL
    build_app(cfg).run_polling()
