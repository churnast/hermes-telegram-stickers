"""telegram-stickers: let a Hermes agent answer with stickers from its owner's Telegram packs.

Registers three tools (telegram_sticker_find, telegram_sticker_send, telegram_sticker_mute), one
slash command (/stickers) to check the setup, describe stickers for picking by meaning, mute a
chat and curate stickers, and one bundled skill with etiquette notes. Stickers are sent through the Telegram Bot API
as the bot the gateway already runs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import schemas
from .stickers import StickerError, StickerService

_service = None


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def _has_token() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def _handle_find(args, **kwargs) -> str:
    try:
        return _json(_service.find(args or {}))
    except StickerError as exc:
        return _json({"error": str(exc)})
    except Exception as exc:  # never let a tool crash the turn
        return _json({"error": f"telegram_sticker_find failed: {type(exc).__name__}: {exc}"})


def _handle_send(args, **kwargs) -> str:
    try:
        return _json(_service.send(args or {}))
    except StickerError as exc:
        return _json({"error": str(exc)})
    except Exception as exc:
        return _json({"error": f"telegram_sticker_send failed: {type(exc).__name__}: {exc}"})


def _handle_mute(args, **kwargs) -> str:
    try:
        return _json(_service.mute_current_chat())
    except StickerError as exc:
        return _json({"error": str(exc)})
    except Exception as exc:
        return _json({"error": f"telegram_sticker_mute failed: {type(exc).__name__}: {exc}"})


def _stickers_command(raw_args: str = "") -> str:
    """/stickers: setup check, reload, descriptions, per-chat switch and owner curation."""
    return _service.command(raw_args)


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
                                     "ban/unban, about",
                         args_hint="[sync | describe [again] [n] | off [chat id] | on [chat id] | ban <id> "
                                   "| unban <id> | about <id> <text>]")
    skill = Path(__file__).parent / "skills" / "sticker-etiquette" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("sticker-etiquette", skill,
                           description="When a sticker fits a Telegram conversation and when it does not.")
