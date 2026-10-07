"""telegram-stickers: let a Hermes agent answer with stickers from its owner's Telegram packs.

Registers three tools (telegram_sticker_find, telegram_sticker_send, telegram_sticker_mute), one
slash command (/stickers) to check the setup, describe stickers for picking by meaning, mute a
chat and curate stickers, and one bundled skill with etiquette notes. Stickers are sent through the Telegram Bot API
as the bot the gateway already runs.

A pre_llm_call hook adds a short sticker hint to the user message of a Telegram turn where a sticker is allowed
now (turn_hint, on by default), so the agent answers a joke or good news with a sticker nobody asked for. To learn
packs from stickers the owner sends the bot, it also registers a native Telegram handler that notes stickers in
direct chats (memory only); the same hook keeps a noted pack once Hermes runs a turn for that sender in their
direct chat.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

from . import schemas
from .stickers import StickerError, StickerService, command_origin

_service = None
# A handler group of the plugin's own. PTB runs one handler per group; in group 0 this one would take direct-chat
# stickers away from Hermes' media handler, and group 99 is Hermes' update observer.
OBSERVER_GROUP = -42


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def _has_token() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def _scrub(text: str) -> str:
    """Last line of defence: no text that leaves the plugin carries the bot token."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return text.replace(token, "<token>") if token else text


def _handle_find(args, **kwargs) -> str:
    try:
        return _json(_service.find(args or {}))
    except StickerError as exc:
        return _json({"error": _scrub(str(exc))})
    except Exception as exc:  # never let a tool crash the turn
        return _json({"error": _scrub(f"telegram_sticker_find failed: {type(exc).__name__}: {exc}")})


def _handle_send(args, **kwargs) -> str:
    try:
        return _json(_service.send(args or {}))
    except StickerError as exc:
        return _json({"error": _scrub(str(exc))})
    except Exception as exc:
        return _json({"error": _scrub(f"telegram_sticker_send failed: {type(exc).__name__}: {exc}")})


def _handle_mute(args, **kwargs) -> str:
    try:
        return _json(_service.mute_current_chat())
    except StickerError as exc:
        return _json({"error": _scrub(str(exc))})
    except Exception as exc:
        return _json({"error": _scrub(f"telegram_sticker_mute failed: {type(exc).__name__}: {exc}")})


def _run_stickers_command(raw_args: str) -> str:
    try:
        return _scrub(_service.command(raw_args))
    except Exception as exc:  # Hermes would log the text: keep the token out of it
        return _scrub(f"Stickers: /stickers failed: {type(exc).__name__}: {exc}")


def _on_gateway_loop() -> bool:
    """An event loop runs on this thread and neither Hermes' console nor a plugin host called: a gateway before
    Hermes 0.21.5."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return command_origin() == "gateway"


def _stickers_command(raw_args: str = ""):
    """/stickers: setup check, reload, descriptions, per-chat switch and owner curation. Hermes before 0.21.5
    calls a plugin command on the gateway's event loop and awaits a coroutine it returns: there the command runs
    in a worker thread, so reloading packs does not hold up every chat. Hermes 0.21.5 and newer already run it in
    a worker thread, and the CLI has no gateway loop."""
    if _on_gateway_loop():
        return asyncio.to_thread(_run_stickers_command, raw_args)
    return _run_stickers_command(raw_args)


def _live_service():
    """The service of the plugin module loaded now: a handler wired before a plugin reload keeps working."""
    module = sys.modules.get(__name__)
    return getattr(module, "_service", None) if module is not None else None


def _sticker_kind(sticker) -> str:
    if getattr(sticker, "is_video", False):
        return "video"
    return "animated" if getattr(sticker, "is_animated", False) else "static"


def _forwarded(message) -> bool:
    """A forward carries someone else's choice of sticker, not the sender's (PTB 22 forward_origin; older
    versions had forward_date)."""
    return bool(getattr(message, "forward_origin", None) or getattr(message, "forward_date", None))


async def _on_dm_sticker(update, context) -> None:
    """A sticker in a direct chat with the bot: note its pack, unless it was forwarded. Never replies and never
    raises, so Hermes' own handlers (group 0, after this one) still get the sticker."""
    try:
        message = getattr(update, "message", None)
        sticker = getattr(message, "sticker", None)
        user = getattr(message, "from_user", None)
        chat = getattr(message, "chat", None)
        service = _live_service()
        if service is None:
            return
        service.observer_wired = True
        if (sticker is None or user is None or getattr(user, "is_bot", False)
                or getattr(chat, "type", "") != "private" or str(getattr(chat, "id", "")) != str(user.id)
                or getattr(sticker, "type", "") != "regular" or _forwarded(message)):
            return
        service.note_sticker(str(user.id), getattr(sticker, "set_name", "") or "", _sticker_kind(sticker))
    except Exception:
        pass


def _wire_sticker_observer(application, adapter) -> None:
    """Factory for ctx.register_telegram_handler: Hermes calls it with its PTB Application when the Telegram
    gateway connects. Stickers in direct chats only; animated and video ones included, which Hermes' own
    sticker note leaves without a pack name."""
    from telegram.ext import MessageHandler, filters  # python-telegram-bot comes with Hermes' Telegram gateway

    application.add_handler(
        MessageHandler(filters.Sticker.ALL & filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE, _on_dm_sticker),
        group=OBSERVER_GROUP)
    service = _live_service()
    if service is not None:
        service.observer_wired = True


def _on_pre_llm_call(platform="", sender_id="", user_message="", **kwargs):
    """Hermes runs a turn: learn the packs of stickers this sender sent in their direct chat, then, where a sticker
    is allowed now, return a short hint that Hermes appends to this turn's user message. Never raises, and a
    failure of one part never stops the other."""
    service = _live_service()
    if service is None:
        return None
    with contextlib.suppress(Exception):
        service.admit_turn(platform, sender_id, user_message)
    hint = ""
    with contextlib.suppress(Exception):
        hint = service.turn_hint(platform, sender_id)
    return {"context": hint} if isinstance(hint, str) and hint else None


def register(ctx) -> None:
    global _service
    _service = StickerService(get_config=ctx.get_config)
    tools = ((schemas.FIND, _handle_find, "🗂"), (schemas.SEND, _handle_send, "🐾"), (schemas.MUTE, _handle_mute, "🔕"))
    for schema, handler, emoji in tools:
        ctx.register_tool(
            name=schema["name"], toolset="telegram_stickers", schema=schema, handler=handler,
            check_fn=_has_token, requires_env=["TELEGRAM_BOT_TOKEN"], emoji=emoji,
        )
    ctx.register_command("stickers", _stickers_command,
                         description="Sticker packs: status, sync, describe, off/on (here or by chat id), "
                                     "ban/unban, about, forget a learned pack",
                         args_hint="[sync | describe [again] [n] | off [chat id] | on [chat id] | ban <id> "
                                   "| unban <id> | about <id> <text> | forget <pack>]")
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    # Refused under plugins.isolation: host, where nothing is learned (the session is not visible there either).
    # In-process, if the handler is not wired (refused, or its factory fails), learning falls back to Hermes'
    # note (static stickers only).
    with contextlib.suppress(Exception):
        ctx.register_telegram_handler(_wire_sticker_observer)
    skill = Path(__file__).parent / "skills" / "sticker-etiquette" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("sticker-etiquette", skill,
                           description="When a sticker fits a Telegram conversation and when it does not.")
