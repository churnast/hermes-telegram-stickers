"""telegram-stickers: let a Hermes agent answer with stickers from its owner's Telegram packs.

Registers four tools (telegram_sticker_find, telegram_sticker_send, telegram_sticker_mute,
telegram_sticker_settings for settings changed in words and the setup in the owner's direct chat), one
slash command (/stickers) to check the setup, describe stickers for picking by meaning, mute a
chat and curate stickers, and one bundled skill with etiquette notes. Stickers are sent through the Telegram Bot API
as the bot the gateway already runs.

A pre_llm_call hook adds a short sticker hint to the user message of a Telegram turn where a sticker is allowed
now (turn_hint, on by default), so the agent answers a joke or good news with a sticker nobody asked for. After a
fresh install, the same hook walks the owner through a short setup in their direct chat (setup, on by default). To learn
packs from stickers the owner sends the bot, it also registers a native Telegram handler that notes stickers in
direct chats (memory only); the same hook keeps a noted pack once Hermes runs a turn for that sender in their
direct chat.

On Telegram turns, a tool_execution middleware runs the plugin's own tools itself in the agent's tool loop
(hide_tool_progress, on by default), after Hermes' own pre_tool_call step and argument coercion, so Hermes posts no
tool progress line such as "telegram_sticker_send..." in the chat before a sticker. Every other call passes through
it unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

from . import schemas
from .stickers import NO_TOKEN, StickerError, StickerService, command_origin, in_plugin_host

_service = None
# Every tool register() registers, by name: the tools the tool_execution middleware runs itself.
_tool_handlers: dict = {}
TOOL_EXECUTION = "tool_execution"
# Hermes' tool_execution chain (module, function) and the module whose call of it is the agent's own tool loop. Only
# there does the step at the end of the chain run Hermes' pre-call checks and then post the tool progress line. Other
# callers (model_tools.handle_function_call, for execute_code or a plugin's dispatch_tool) have run pre_tool_call
# before the chain and post no line, so the middleware leaves their calls alone.
CHAIN = ("hermes_cli.middleware", "run_tool_execution_middleware")
AGENT_LOOP = "agent.tool_executor"
# The ids Hermes passes every tool hook; the middleware gets the same ones as keywords.
HOOK_IDS = ("task_id", "session_id", "tool_call_id", "turn_id", "api_request_id")
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


def _handle_settings(args, **kwargs) -> str:
    try:
        return _json(_service.settings(args or {}))
    except StickerError as exc:
        return _json({"error": _scrub(str(exc))})
    except Exception as exc:
        return _json({"error": _scrub(f"telegram_sticker_settings failed: {type(exc).__name__}: {exc}")})

def _chain_caller() -> str:
    """The module that started the tool_execution chain this call runs in, read from this thread's call stack."""
    frame = sys._getframe(1)
    while frame is not None:
        if (frame.f_globals.get("__name__"), frame.f_code.co_name) == CHAIN:
            caller = frame.f_back
            return str(caller.f_globals.get("__name__") or "") if caller is not None else ""
        frame = frame.f_back
    return ""


def _hermes_pre_call_steps():
    """Hermes' own steps before a tool runs that the middleware takes over for the sticker tools, or None when this
    Hermes lacks one: the pre_tool_call dispatch its tool loop calls (plugin and shell hooks with block, approve and
    modify, the human approval gate, the thread's tool whitelist) and the schema-guided coercion of arguments (a
    "false" string to false). Both have these names and signatures from Hermes 0.20.6 to main."""
    try:
        from hermes_cli.plugins import _dispatch_pre_tool_call_hooks
        from model_tools import coerce_tool_args
    except Exception:
        return None
    return _dispatch_pre_tool_call_hooks, coerce_tool_args


def _own_tool(tool_name):
    """The handler of one of the plugin's own tools when the middleware should run it itself, else None: the
    setting is on, the turn is a Telegram one and the chain is the agent's tool loop, the one place where a
    progress line follows it."""
    handler = _tool_handlers.get(tool_name) if isinstance(tool_name, str) else None
    if handler is None or _service is None or not _service.hide_tool_progress_enabled():
        return None
    if not _service.in_telegram_turn() or _chain_caller() != AGENT_LOOP:
        return None
    return handler


class _PreCallUnavailable(Exception):
    """Hermes' pre_tool_call step could not run here: the call goes Hermes' own way instead."""


def _run_own_tool(tool_name: str, handler, args, steps, context) -> str:
    """Run a sticker tool as Hermes' tool loop would: the check its check_fn made (the bot token), Hermes'
    pre_tool_call step (a block or a denied approval returns {"error": message}, as Hermes does), Hermes' argument
    coercion, then the handler, with the tool handlers' error shape. If the pre_tool_call step itself fails (a
    Hermes that changed it, for example), raises _PreCallUnavailable, so hooks and approvals are never skipped."""
    if not _has_token():
        return _json({"error": NO_TOKEN})
    pre_tool_call, coerce = steps
    args = dict(args) if isinstance(args, dict) else {}
    try:
        block, modified = pre_tool_call(tool_name, args, middleware_trace=[],
                                        **{key: str(context.get(key) or "") for key in HOOK_IDS})
    except Exception as exc:
        raise _PreCallUnavailable() from exc
    if block is not None:
        return _json({"error": _scrub(str(block))})
    if isinstance(modified, dict):
        args = modified
    with contextlib.suppress(Exception):
        args = coerce(tool_name, dict(args))
    try:
        return handler(args if isinstance(args, dict) else {})
    except Exception as exc:
        return _json({"error": _scrub(f"{tool_name} failed: {type(exc).__name__}: {exc}")})


def _tool_execution_middleware(*, tool_name="", args=None, next_call=None, **context):
    """Hermes' tool_execution middleware. In the agent's tool loop, the step at the end of this chain runs Hermes'
    pre-call checks and then emits "tool.started", which the gateway posts as a tool progress line. So on a Telegram
    turn, for the plugin's own tools (also when called through tool_call, which Hermes unwraps before the chain),
    the middleware runs Hermes' pre_tool_call step and argument coercion itself, then the handler, and never calls
    next_call: no line is posted. The README (Tool progress lines) lists what it does not run. Every other call,
    and any failure while deciding, goes on to next_call unchanged. Hermes runs plugin tools one at a time, never
    in a parallel batch, so no other tool waits on Hermes' start order for a sticker call."""
    try:
        handler = _own_tool(tool_name)
        steps = _hermes_pre_call_steps() if handler is not None else None
    except Exception:
        handler = steps = None
    if handler is None or steps is None:
        return next_call(args)
    try:
        return _run_own_tool(tool_name, handler, args, steps, context)
    except _PreCallUnavailable:  # Hermes' own step runs its pre-call checks; the progress line shows this once
        return next_call(args)


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
                or getattr(sticker, "type", "") != "regular"):
            return
        if _forwarded(message):
            service.note_forwarded(str(user.id))
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
        hint = service.turn_hint(platform, sender_id, user_message)
    return {"context": hint} if isinstance(hint, str) and hint else None


def register(ctx) -> None:
    global _service
    _service = StickerService(get_config=ctx.get_config)
    tools = ((schemas.FIND, _handle_find, "🗂"), (schemas.SEND, _handle_send, "🐾"), (schemas.MUTE, _handle_mute, "🔕"),
             (schemas.SETTINGS, _handle_settings, "⚙"))
    for schema, handler, emoji in tools:
        ctx.register_tool(
            name=schema["name"], toolset="telegram_stickers", schema=schema, handler=handler,
            check_fn=_has_token, requires_env=["TELEGRAM_BOT_TOKEN"], emoji=emoji,
        )
        _tool_handlers[schema["name"]] = handler
    ctx.register_command("stickers", _stickers_command,
                         description="Sticker packs: status, sync, describe, off/on (here or by chat id), "
                                     "ban/unban, about, forget a learned pack, setup again",
                         args_hint="[sync | describe [again] [n] | off [chat id] | on [chat id] | ban <id> "
                                   "| unban <id> | about <id> <text> | forget <pack> | setup]")
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    # Refused under plugins.isolation: host, where nothing is learned (the session is not visible there either).
    # In-process, if the handler is not wired (refused, or its factory fails), learning falls back to Hermes'
    # note (static stickers only).
    with contextlib.suppress(Exception):
        ctx.register_telegram_handler(_wire_sticker_observer)
    # Keeps the sticker tools out of Hermes' tool progress lines. Not in a plugin host (plugins.isolation: host):
    # Hermes hands a middleware there its next step as a placeholder it cannot call, so other tools could not
    # pass through it.
    if not in_plugin_host():
        with contextlib.suppress(Exception):
            ctx.register_middleware(TOOL_EXECUTION, _tool_execution_middleware)
    skill = Path(__file__).parent / "skills" / "sticker-etiquette" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("sticker-etiquette", skill,
                           description="When a sticker fits a Telegram conversation and when it does not.")
