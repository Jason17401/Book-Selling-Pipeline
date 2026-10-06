"""Telegram bot download retries (no network: Telegram is faked)."""
import asyncio
import importlib
import sys
import types

import pytest


@pytest.fixture
def bot(monkeypatch):
    try:
        import telegram.error  # noqa: F401
    except ImportError:  # python-telegram-bot not installed here: minimal stand-ins
        tg, err, ext = (types.ModuleType(n) for n in ("telegram", "telegram.error", "telegram.ext"))
        tg.Update = object
        err.NetworkError = type("NetworkError", (Exception,), {})
        err.TimedOut = type("TimedOut", (err.NetworkError,), {})
        for name in ("Application", "CommandHandler", "ContextTypes", "MessageHandler", "filters"):
            setattr(ext, name, object)
        for m in (tg, err, ext):
            monkeypatch.setitem(sys.modules, m.__name__, m)
        monkeypatch.delitem(sys.modules, "pipeline.apps.bot", raising=False)
    return importlib.import_module("pipeline.apps.bot")


class FakeFile:
    def __init__(self, fail_times, err):
        self.fail_times, self.err, self.calls = fail_times, err, 0

    async def download_to_drive(self, custom_path, **kw):
        self.calls += 1
        custom_path.write_bytes(b"partial")
        if self.calls <= self.fail_times:
            raise self.err("Timed out")
        custom_path.write_bytes(b"photo")


def run(coro):
    return asyncio.run(coro)


async def no_sleep(_):
    return None


def test_timeout_is_retried_then_saved(bot, tmp_path):
    f = FakeFile(2, bot.TimedOut)

    async def getter(**kw):
        assert kw["read_timeout"] == 30
        return f
    dest = tmp_path / "0000000001.jpg"
    assert run(bot.fetch_with_retries(getter, dest, 30, sleep=no_sleep)) == 3
    assert dest.read_bytes() == b"photo"


def test_gives_up_after_all_attempts_and_leaves_no_half_file(bot, tmp_path):
    f = FakeFile(99, bot.TimedOut)

    async def getter(**kw):
        return f
    dest = tmp_path / "0000000001.jpg"
    with pytest.raises(bot.TimedOut):
        run(bot.fetch_with_retries(getter, dest, 30, sleep=no_sleep))
    assert f.calls == bot.DOWNLOAD_ATTEMPTS and not dest.exists()


def test_send_with_retries_gives_up_quietly(bot):
    calls = []

    async def flaky():
        calls.append(1)
        raise bot.TimedOut("Timed out")
    assert run(bot.send_with_retries(flaky, sleep=no_sleep)) is False and len(calls) == 3

    async def ok():
        return None
    assert run(bot.send_with_retries(ok, sleep=no_sleep)) is True


def test_downloads_tracker_waits_for_parallel_downloads(bot):
    async def main():
        d = bot.Downloads()
        assert await d.wait(0.1) is True                       # nothing downloading
        d.start()
        d.start()
        assert await d.wait(0.05) is False                     # still busy
        waiter = asyncio.create_task(d.wait(1))
        d.done()
        await asyncio.sleep(0)
        assert not waiter.done()
        d.done()
        assert await waiter is True and d.count == 0
    run(main())
