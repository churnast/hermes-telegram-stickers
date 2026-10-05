"""Core logic for the telegram-stickers plugin.

Everything here is plain Python with no Hermes imports at module level, so the
test suite runs without a Hermes checkout. Hermes-specific lookups (session
routing, the plugin data directory, the vision model, Hermes' own sticker
description cache) are resolved lazily and degrade gracefully.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

PLUGIN_NAME = "telegram-stickers"
DEFAULT_API_BASE = "https://api.telegram.org"
CATALOG_FILE = "catalog.json"
CATALOG_VERSION = 2
DESCRIPTIONS_FILE = "descriptions.json"
STATE_FILE = "state.json"
CATALOG_MAX_AGE_SECONDS = 7 * 24 * 3600
DEFAULT_COOLDOWN_SECONDS = 20
DEFAULT_MIN_MESSAGES = 8
DEFAULT_DESCRIBE_BATCH = 30
MAX_DESCRIBE_BATCH = 200
MAX_VISION_FAILURES = 3
MAX_DESCRIBE_TRIES = 2
RECENT_PER_CHAT = 5
MAX_FILE_BYTES = 2_000_000
ABOUT_CHARS = 140
VARIATION_SELECTOR = "️"
STICKER_PROMPT = ("Describe this sticker in one or two sentences. Focus on what it depicts: "
                  "character, action, emotion. Be concise and objective.")
DIRECT_CHAT_TYPES = {"", "dm", "direct", "private", "im"}
ADDSTICKERS_PREFIXES = (
    "https://t.me/addstickers/",
    "http://t.me/addstickers/",
    "t.me/addstickers/",
    "tg://addstickers?set=",
)


class StickerError(Exception):
    """A failure that is reported back to the model as a readable sentence."""


def normalize_emoji(value: str) -> str:
    """Drop the emoji variation selector so that '❤️' and '❤' match."""
    return (value or "").replace(VARIATION_SELECTOR, "").strip()


def pack_name(value: str) -> str:
    """Accept either a sticker set short name or its t.me/addstickers link."""
    text = (value or "").strip()
    for prefix in ADDSTICKERS_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.split("?", 1)[0].strip("/ ")


def bot_id_from_token(token: str) -> str:
    """The numeric bot id is the part of the token before the colon (not secret)."""
    return token.split(":", 1)[0] if token and ":" in token else ""


def sticker_id(name: str, sticker: dict[str, Any]) -> str:
    return f"{name}:{sticker['i']}"


# --- Telegram Bot API -------------------------------------------------------------------------


class TelegramClient:
    """Minimal Bot API client. Never puts the token into an error message."""

    def __init__(self, token: str, api_base: str = DEFAULT_API_BASE,
                 opener: Callable[..., Any] | None = None, timeout: float = 30.0) -> None:
        if not token:
            raise StickerError("TELEGRAM_BOT_TOKEN is not set, so there is no bot to send stickers from.")
        self._token = token
        self._base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self._open = opener or urllib.request.urlopen
        self._timeout = timeout

    def call(self, method: str, **params: Any) -> Any:
        fields = {}
        for key, value in params.items():
            if value is None:
                continue
            fields[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
        request = urllib.request.Request(
            f"{self._base}/bot{self._token}/{method}",
            data=urllib.parse.urlencode(fields).encode(),
        )
        try:
            with self._open(request, timeout=self._timeout) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            # Telegram puts the human-readable reason into the error body.
            try:
                body = json.loads(exc.read() or b"{}")
            except Exception:
                raise StickerError(f"Telegram answered HTTP {exc.code}.") from None
        except urllib.error.URLError as exc:
            raise StickerError(f"Telegram is unreachable: {exc.reason}.") from None
        if not isinstance(body, dict) or not body.get("ok"):
            description = body.get("description") if isinstance(body, dict) else None
            raise StickerError(f"Telegram refused {method}: {description or 'unknown error'}.")
        return body.get("result")

    def download(self, file_id: str, max_bytes: int = MAX_FILE_BYTES) -> tuple[bytes, str]:
        """Fetch a file the bot can see; returns (bytes, extension). The URL holds the token,
        so it never leaves this process: the caller hands a local copy to the vision model."""
        info = self.call("getFile", file_id=file_id) or {}
        file_path = str(info.get("file_path") or "")
        if not file_path:
            raise StickerError("Telegram did not return a file path.")
        request = urllib.request.Request(f"{self._base}/file/bot{self._token}/{file_path}")
        try:
            with self._open(request, timeout=self._timeout) as response:
                data = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise StickerError(f"Telegram answered HTTP {exc.code} for a sticker file.") from None
        except urllib.error.URLError as exc:
            raise StickerError(f"Telegram is unreachable: {exc.reason}.") from None
        if len(data) > max_bytes:
            raise StickerError("the sticker file is larger than a sticker should be")
        return data, (Path(file_path).suffix or ".webp")


# --- Files ------------------------------------------------------------------------------------


def default_data_dir() -> Path:
    """<HERMES_HOME>/plugin-data/telegram-stickers/, via Hermes when available."""
    try:
        from plugins.plugin_storage import plugin_data_dir  # type: ignore

        return plugin_data_dir(PLUGIN_NAME)
    except Exception:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        path = home / "plugin-data" / PLUGIN_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# --- Catalog ----------------------------------------------------------------------------------


def catalog_is_fresh(catalog: dict[str, Any] | None, packs: list[str], bot_id: str, now: float) -> bool:
    """file_ids belong to one bot, so a token change or a new pack list means a re-sync."""
    if not catalog:
        return False
    return (
        catalog.get("version") == CATALOG_VERSION
        and catalog.get("bot_id") == bot_id
        and catalog.get("pack_list") == list(packs)
        and now - float(catalog.get("synced_at") or 0) < CATALOG_MAX_AGE_SECONDS
    )


def sticker_kind(raw: dict[str, Any]) -> str:
    if raw.get("is_video"):
        return "video"
    if raw.get("is_animated"):
        return "animated"
    return "static"


def sync_catalog(client: TelegramClient, packs: list[str], bot_id: str, now: float) -> dict[str, Any]:
    catalog: dict[str, Any] = {"version": CATALOG_VERSION, "bot_id": bot_id, "synced_at": now,
                               "pack_list": list(packs), "packs": {}, "skipped": {}}
    for name in packs:
        try:
            result = client.call("getStickerSet", name=name)
        except StickerError as exc:
            catalog["skipped"][name] = str(exc)
            continue
        catalog["packs"][name] = {
            "title": result.get("title") or name,
            "stickers": [
                {"i": index, "emoji": normalize_emoji(raw.get("emoji", "")),
                 "file_id": raw["file_id"], "uid": raw.get("file_unique_id") or "",
                 "thumb": (raw.get("thumbnail") or raw.get("thumb") or {}).get("file_id") or "",
                 "kind": sticker_kind(raw)}
                for index, raw in enumerate(result.get("stickers") or [], 1)
                if raw.get("file_id")
            ],
        }
    return catalog


def all_stickers(catalog: dict[str, Any], packs: list[str], pack: str = "") -> Iterable[tuple[str, dict[str, Any]]]:
    for name in packs:
        if pack and name != pack:
            continue
        for sticker in catalog.get("packs", {}).get(name, {}).get("stickers", []):
            yield name, sticker


def matches(catalog: dict[str, Any], packs: list[str], emoji: str = "",
            pack: str = "") -> list[tuple[str, dict[str, Any]]]:
    wanted = normalize_emoji(emoji)
    return [(name, s) for name, s in all_stickers(catalog, packs, pack) if not wanted or s["emoji"] == wanted]


def without(catalog: dict[str, Any], banned: set[str]) -> dict[str, Any]:
    """The catalog minus stickers the owner banned (by Telegram's stable file_unique_id)."""
    if not banned:
        return catalog
    view = dict(catalog)
    view["packs"] = {name: {**pack, "stickers": [s for s in pack["stickers"] if s.get("uid") not in banned]}
                     for name, pack in catalog.get("packs", {}).items()}
    return view


# --- Descriptions -----------------------------------------------------------------------------

_WORD = re.compile(r"[^\W_]+")
_STOP = frozenset({"the", "and", "with", "this", "that", "its", "his", "her", "their", "for", "from", "into",
                   "has", "have", "are", "was", "who", "which", "while", "very", "some", "sticker", "image",
                   "picture", "cartoon", "character", "depicts", "shows", "showing"})


def description_of(descriptions: dict[str, Any], sticker: dict[str, Any]) -> str:
    entry = descriptions.get(sticker.get("uid") or "")
    return str(entry.get("text") or "") if isinstance(entry, dict) else ""


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if len(w) > 2 and w not in _STOP]


def _stem(word: str) -> str:
    return word[:max(4, len(word) - 3)] if len(word) > 5 else word


def relevance(query: str, sticker: dict[str, Any], description: str) -> int:
    """How well a sticker fits a few words: whole words count double, word stems once,
    and an emoji in the query that equals the sticker's emoji adds two. 0 means no match."""
    text = (description or "").lower()
    score = 0
    for word in _words(query):
        if re.search(rf"\b{re.escape(word)}\b", text):
            score += 2
        elif _stem(word) in text:
            score += 1
    if sticker.get("emoji") and sticker["emoji"] in normalize_emoji(query):
        score += 2
    return score


def search(catalog: dict[str, Any], packs: list[str], descriptions: dict[str, Any], query: str,
           pack: str = "") -> list[tuple[int, str, dict[str, Any]]]:
    """Best matches first; ties keep the owner's pack order."""
    found = []
    for order, (name, sticker) in enumerate(all_stickers(catalog, packs, pack)):
        score = relevance(query, sticker, description_of(descriptions, sticker))
        if score:
            found.append((score, order, name, sticker))
    found.sort(key=lambda item: (-item[0], item[1]))
    return [(score, name, sticker) for score, _, name, sticker in found]


def hermes_sticker_descriptions() -> dict[str, str]:
    """Descriptions Hermes already made for stickers people sent to the bot, by file_unique_id."""
    try:
        from hermes_cli.config import get_hermes_home  # type: ignore

        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    data = load_json(home / "sticker_cache.json") or {}
    out = {}
    for uid, entry in data.items():
        text = str(entry.get("description") or "").strip() if isinstance(entry, dict) else ""
        if len(text) >= 12:  # skip placeholders such as "a sticker"
            out[uid] = text
    return out


def _run_coroutine(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # handed back to the caller below
            box["error"] = exc

    worker = threading.Thread(target=runner, daemon=True)
    worker.start()
    worker.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def vision_describe(image: Path) -> str:
    """One or two sentences about a sticker from Hermes' auxiliary vision model: the same call
    the Telegram adapter makes for stickers that people send to the bot."""
    try:
        from tools.vision_tools import vision_analyze_tool  # type: ignore
    except Exception:
        raise StickerError("this Hermes has no vision tool") from None
    try:
        from gateway.sticker_cache import STICKER_VISION_PROMPT as prompt  # type: ignore
    except Exception:
        prompt = STICKER_PROMPT
    raw = _run_coroutine(vision_analyze_tool(image_url=str(image), user_prompt=prompt))
    result = json.loads(raw) if isinstance(raw, str) else (raw or {})
    text = str(result.get("analysis") or "").strip()
    if not result.get("success") or not text:
        raise StickerError(f"the vision model gave no description ({result.get('error') or 'empty answer'})")
    return text


# --- Picking ----------------------------------------------------------------------------------


def pick(catalog: dict[str, Any], packs: list[str], wanted: str, rng: random.Random,
         descriptions: dict[str, Any] | None = None,
         avoid: Iterable[str] = ()) -> tuple[str, dict[str, Any]]:
    """'pack:12' picks one exact sticker; words pick the best-described match; an emoji picks at
    random, favouring earlier packs. Stickers sent recently in the chat are skipped when possible."""
    wanted = (wanted or "").strip()
    avoid = set(avoid)
    if not wanted:
        raise StickerError("Say which sticker: an emoji such as 😏, a few words like 'sleepy cat', "
                           "or an id like 'pack_name:12'.")
    name, sep, index = wanted.rpartition(":")
    if sep and name and index.isdigit():
        for sticker in catalog.get("packs", {}).get(name, {}).get("stickers", []):
            if sticker["i"] == int(index):
                return name, sticker
        raise StickerError(f"There is no sticker {wanted}. Call telegram_sticker_find to see valid ids.")

    if any(ch.isalnum() for ch in wanted):
        ranked = search(catalog, packs, descriptions or {}, wanted)
        if not ranked:
            extra = ("" if descriptions else
                     " No sticker has a description yet: the owner adds them with /stickers describe.")
            raise StickerError(f"No sticker description fits '{wanted}'. Try other words or an emoji.{extra}")
        best = [(n, s) for score, n, s in ranked if score == ranked[0][0]]
        fresh = [item for item in best if sticker_id(*item) not in avoid] or best
        return rng.choice(fresh)

    found = matches(catalog, packs, emoji=wanted)
    if not found:
        raise StickerError(f"No sticker for {wanted} in the configured packs. "
                           "Call telegram_sticker_find without arguments to see which emojis exist.")
    fresh = [item for item in found if sticker_id(*item) not in avoid] or found
    weights = [len(packs) - packs.index(n) for n, _ in fresh]
    return rng.choices(fresh, weights=weights, k=1)[0]


# --- Routing ----------------------------------------------------------------------------------


def session_value(name: str) -> str:
    """Per-turn routing ids. Hermes keeps them in context variables; os.environ is the fallback."""
    try:
        from gateway.session_context import get_session_env  # type: ignore

        return get_session_env(name, "") or ""
    except Exception:
        return os.environ.get(name, "")


def current_session() -> dict[str, str]:
    return {
        "platform": session_value("HERMES_SESSION_PLATFORM"),
        "chat_id": session_value("HERMES_SESSION_CHAT_ID"),
        "thread_id": session_value("HERMES_SESSION_THREAD_ID"),
        "message_id": session_value("HERMES_SESSION_MESSAGE_ID"),
        "chat_type": session_value("HERMES_SESSION_CHAT_TYPE"),
    }


def _as_int(value: Any) -> int | None:
    text = str(value).strip() if value is not None else ""
    if text.lstrip("-").isdigit():
        return int(text)
    return None


_CHAT_ID = re.compile(r"-?[1-9][0-9]{0,19}")


def chat_id_from_text(value: str) -> str:
    """A chat id the owner typed, such as -1001234567890, in the form the session uses for the current chat."""
    text = (value or "").strip()
    if not _CHAT_ID.fullmatch(text):
        raise StickerError(f"'{text[:40]}' is not a Telegram chat id. Chat ids are whole numbers such as "
                           "-1001234567890; '/stickers' in a direct chat with the bot lists the chats where "
                           "stickers are off.")
    return text


def resolve_target(args: dict[str, Any], session: dict[str, str],
                   allow_other_chats: bool) -> tuple[str, int | None, int | None]:
    """Return (chat_id, thread_id, reply_to_message_id).

    By default a sticker can only go to the Telegram chat of the current turn; any other chat
    needs the owner to switch on allow_other_chats.
    """
    in_telegram = session.get("platform") == "telegram" and bool(session.get("chat_id"))
    session_chat = session.get("chat_id", "") if in_telegram else ""
    explicit = str(args.get("chat_id") or "").strip()
    if explicit and explicit != session_chat:
        if not allow_other_chats:
            raise StickerError("Stickers go only to the current Telegram chat. The owner can allow other chats "
                               "with plugins.entries.telegram-stickers.settings.allow_other_chats: true.")
        chat = explicit
    elif session_chat:
        chat = session_chat
    else:
        raise StickerError("This turn is not in a Telegram chat, so there is nowhere to send a sticker.")

    same_chat = chat == session_chat
    thread = _as_int(args.get("thread_id"))
    if thread is None and same_chat:
        thread = _as_int(session.get("thread_id"))
    if thread == 1:
        thread = None  # the forum's General topic is addressed without a thread id
    reply_to = None
    if same_chat and args.get("reply", True) is not False:
        reply_to = _as_int(session.get("message_id"))
    return chat, thread, reply_to


# --- Plugin service ---------------------------------------------------------------------------


class StickerService:
    """Holds configuration lookups and process-local state (cooldowns, recent picks, locks)."""

    def __init__(self, get_config: Callable[[str, Any], Any],
                 data_dir: Callable[[], Path] | None = None,
                 opener: Callable[..., Any] | None = None,
                 clock: Callable[[], float] = time.time,
                 rng: random.Random | None = None,
                 session: Callable[[], dict[str, str]] = current_session,
                 describer: Callable[[Path], str] = vision_describe,
                 hermes_descriptions: Callable[[], dict[str, str]] = hermes_sticker_descriptions) -> None:
        self._get_config = get_config
        self._data_dir = data_dir or default_data_dir
        self._opener = opener
        self._clock = clock
        self._rng = rng or random.Random()
        self._session = session
        self._describer = describer
        self._hermes_descriptions = hermes_descriptions
        self._lock = threading.Lock()
        self._files_lock = threading.Lock()
        self._describing = threading.Lock()
        self._last_sent: dict[str, float] = {}

    # configuration
    def packs(self) -> list[str]:
        raw = self._get_config("packs", []) or []
        if isinstance(raw, str):
            raw = [part for part in raw.replace(",", " ").split()]
        names = []
        for item in raw:
            name = pack_name(str(item))
            if name and name not in names:
                names.append(name)
        return names

    def _int_setting(self, key: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(self._get_config(key, default))))
        except (TypeError, ValueError):
            return default

    def cooldown(self) -> int:
        return self._int_setting("cooldown_seconds", DEFAULT_COOLDOWN_SECONDS, 0, 24 * 3600)

    def min_messages(self) -> int:
        return self._int_setting("min_messages_between", DEFAULT_MIN_MESSAGES, 0, 1000)

    def describe_batch(self) -> int:
        return self._int_setting("describe_batch", DEFAULT_DESCRIBE_BATCH, 1, MAX_DESCRIBE_BATCH)

    def allow_other_chats(self) -> bool:
        return self._get_config("allow_other_chats", False) is True

    def client(self) -> TelegramClient:
        return TelegramClient(os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                              api_base=str(self._get_config("api_base", DEFAULT_API_BASE) or DEFAULT_API_BASE),
                              opener=self._opener)

    # catalog
    def catalog(self, refresh: bool = False) -> dict[str, Any]:
        packs = self.packs()
        if not packs:
            raise StickerError("No sticker packs are configured. Add their short names (the part after "
                               "t.me/addstickers/) to plugins.entries.telegram-stickers.settings.packs.")
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        bot_id = bot_id_from_token(token)
        path = self._data_dir() / CATALOG_FILE
        with self._lock:
            cached = load_json(path)
            now = self._clock()
            if not refresh and catalog_is_fresh(cached, packs, bot_id, now):
                return cached  # type: ignore[return-value]
            fresh = sync_catalog(self.client(), packs, bot_id, now)
            if not fresh["packs"]:
                reasons = "; ".join(f"{name}: {why}" for name, why in fresh["skipped"].items())
                raise StickerError(f"None of the configured packs could be loaded ({reasons}).")
            save_json(path, fresh)
        self._merge_hermes_descriptions(fresh)
        return fresh

    # descriptions
    def descriptions(self) -> dict[str, Any]:
        return load_json(self._data_dir() / DESCRIPTIONS_FILE) or {}

    def _merge_hermes_descriptions(self, catalog: dict[str, Any]) -> int:
        """Reuse what Hermes already described for stickers people sent to the bot: free, no model call."""
        try:
            known = self._hermes_descriptions()
        except Exception:
            return 0
        if not known:
            return 0
        with self._files_lock:
            descriptions = self.descriptions()
            added = 0
            for _, sticker in all_stickers(catalog, self.packs()):
                uid = sticker.get("uid")
                if uid and uid not in descriptions and uid in known:
                    descriptions[uid] = {"text": known[uid], "source": "hermes", "at": round(self._clock())}
                    added += 1
            if added:
                save_json(self._data_dir() / DESCRIPTIONS_FILE, descriptions)
        return added

    def describe(self, limit: int | None = None) -> dict[str, Any]:
        """Describe stickers that have no description yet with the vision model, up to `limit`."""
        if not self._describing.acquire(blocking=False):
            raise StickerError("a description run is already going; try again when it finishes")
        try:
            before = self.descriptions()
            catalog = self.catalog()  # a fresh sync already merges Hermes' cache
            self._merge_hermes_descriptions(catalog)
            limit = self.describe_batch() if limit is None else max(1, min(MAX_DESCRIBE_BATCH, limit))
            descriptions = self.descriptions()
            from_hermes = sum(1 for uid, entry in descriptions.items()
                              if uid not in before and isinstance(entry, dict) and entry.get("source") == "hermes")

            def wanted(sticker: dict[str, Any]) -> bool:
                entry = descriptions.get(sticker.get("uid") or "")
                if not sticker.get("uid"):
                    return False
                if not isinstance(entry, dict):
                    return True
                return not entry.get("text") and int(entry.get("tries") or 0) < MAX_DESCRIBE_TRIES

            todo = [(n, s) for n, s in all_stickers(without(catalog, self.banned()), self.packs()) if wanted(s)]
            client = self.client() if todo else None
            tmp_dir = self._data_dir() / "tmp"
            described, problems, failures_in_a_row, tried = 0, [], 0, 0
            for name, sticker in todo[:limit]:
                tried += 1
                file_id = sticker["file_id"] if sticker["kind"] == "static" else sticker.get("thumb")
                if not file_id:
                    problems.append(f"{sticker_id(name, sticker)}: no still preview to look at")
                    continue
                image = None
                try:
                    data, ext = client.download(file_id)  # type: ignore[union-attr]
                    image = tmp_dir / f"{sticker['uid']}{ext}"
                    image.parent.mkdir(parents=True, exist_ok=True)
                    image.write_bytes(data)
                    text = self._describer(image)
                except Exception as exc:
                    problems.append(f"{sticker_id(name, sticker)}: {exc}")
                    failures_in_a_row += 1
                    with self._files_lock:  # a sticker that keeps failing is not retried forever
                        current = self.descriptions()
                        entry = current.get(sticker["uid"]) if isinstance(current.get(sticker["uid"]), dict) else {}
                        current[sticker["uid"]] = {"text": "", "source": "failed",
                                                   "tries": int(entry.get("tries") or 0) + 1,
                                                   "at": round(self._clock())}
                        save_json(self._data_dir() / DESCRIPTIONS_FILE, current)
                    if failures_in_a_row >= MAX_VISION_FAILURES:
                        problems.append("stopped after several failures in a row")
                        break
                    continue
                finally:
                    if image is not None:
                        image.unlink(missing_ok=True)
                failures_in_a_row = 0
                with self._files_lock:
                    current = self.descriptions()
                    current[sticker["uid"]] = {"text": text, "source": "vision", "at": round(self._clock())}
                    save_json(self._data_dir() / DESCRIPTIONS_FILE, current)
                described += 1
            counts = self.counts(catalog)
            return {"described": described, "from_hermes": from_hermes, "problems": problems,
                    "with_description": counts["described"], "total": counts["total"],
                    "left": counts["total"] - counts["described"], "tried": tried}
        finally:
            self._describing.release()

    def counts(self, catalog: dict[str, Any]) -> dict[str, int]:
        descriptions = self.descriptions()
        stickers = list(all_stickers(catalog, self.packs()))
        return {"total": len(stickers),
                "described": sum(1 for _, s in stickers if description_of(descriptions, s))}

    # owner choices and per-chat pacing, kept across restarts
    def state(self) -> dict[str, Any]:
        data = load_json(self._data_dir() / STATE_FILE) or {}
        data.setdefault("muted", [])
        data.setdefault("banned", [])
        data.setdefault("chats", {})
        return data

    def _update_state(self, change: Callable[[dict[str, Any]], None]) -> None:
        with self._files_lock:
            data = self.state()
            change(data)
            save_json(self._data_dir() / STATE_FILE, data)

    def muted(self) -> set[str]:
        return {str(chat) for chat in self.state()["muted"]}

    def set_muted(self, chat: str, muted: bool) -> None:
        def change(data: dict[str, Any]) -> None:
            chats = {str(c) for c in data["muted"]}
            chats.add(chat) if muted else chats.discard(chat)
            data["muted"] = sorted(chats)
        self._update_state(change)

    def banned(self) -> set[str]:
        return {str(uid) for uid in self.state()["banned"]}

    def _resolve(self, wanted: str) -> tuple[str, dict[str, Any]]:
        catalog = self.catalog()
        name, sep, index = (wanted or "").strip().rpartition(":")
        if sep and index.isdigit():
            for sticker in catalog.get("packs", {}).get(name, {}).get("stickers", []):
                if sticker["i"] == int(index) and sticker.get("uid"):
                    return name, sticker
        raise StickerError(f"There is no sticker '{wanted}'. Ids look like 'pack_name:12' (see telegram_sticker_find).")

    def _current_telegram_chat(self) -> str:
        session = self._session()
        return session.get("chat_id", "") if session.get("platform") == "telegram" else ""

    def _in_direct_chat(self) -> bool:
        return (self._session().get("chat_type") or "").strip().lower() in DIRECT_CHAT_TYPES

    def _require_direct_chat(self) -> None:
        if not self._in_direct_chat():
            raise StickerError("this command works in a direct chat with the bot")

    # tools
    def find(self, args: dict[str, Any]) -> dict[str, Any]:
        catalog = without(self.catalog(refresh=bool(args.get("refresh"))), self.banned())
        packs = self.packs()
        descriptions = self.descriptions()
        emoji = str(args.get("emoji") or "")
        query = str(args.get("query") or "").strip()
        pack = pack_name(str(args.get("pack") or ""))
        try:
            limit = min(50, max(1, int(args.get("limit") or 10)))
        except (TypeError, ValueError):
            limit = 10
        if not emoji and not pack and not query:
            summary = []
            for name in packs:
                if name not in catalog.get("packs", {}):
                    continue
                stickers = catalog["packs"][name]["stickers"]
                summary.append({
                    "pack": name, "title": catalog["packs"][name]["title"], "count": len(stickers),
                    "described": sum(1 for s in stickers if description_of(descriptions, s)),
                    "emojis": "".join(sorted({s["emoji"] for s in stickers if s["emoji"]})),
                })
            return {
                "packs": summary,
                "skipped": catalog.get("skipped", {}),
                "hint": ("Call again with an emoji, or with query='a few English words about the picture', "
                         "to get sticker ids; or send directly with telegram_sticker_send."),
            }
        if query:
            found = [(n, s) for _, n, s in search(catalog, packs, descriptions, query + " " + emoji, pack=pack)]
        else:
            found = matches(catalog, packs, emoji=emoji, pack=pack)
        out: dict[str, Any] = {"stickers": [], "total": len(found)}
        for name, sticker in found[:limit]:
            item = {"id": sticker_id(name, sticker), "emoji": sticker["emoji"], "kind": sticker["kind"],
                    "pack_title": catalog["packs"][name]["title"]}
            about = description_of(descriptions, sticker)
            if about:
                item["about"] = about[:ABOUT_CHARS]
            out["stickers"].append(item)
        if query and not found:
            out["hint"] = ("No description matches these words. Try other words or an emoji."
                           + ("" if descriptions else " No sticker has a description yet: the owner adds "
                              "them with /stickers describe."))
        return out

    def send(self, args: dict[str, Any]) -> dict[str, Any]:
        chat, thread, reply_to = resolve_target(args, self._session(), self.allow_other_chats())
        if chat in self.muted():
            raise StickerError(f"The owner switched stickers off in this chat ('/stickers on {chat}' in a direct "
                               "chat with the bot switches them back). Answer in words.")
        memory = self.state()["chats"].get(chat, {})
        cooldown = self.cooldown()
        now = self._clock()
        last = self._last_sent.get(chat, memory.get("last_ts"))
        if cooldown and last is not None and 0 <= now - float(last) < cooldown:
            wait = int(cooldown - (now - float(last))) + 1
            raise StickerError(f"A sticker already went to this chat {int(now - float(last))} s ago. "
                               f"Wait {wait} s or answer in words; this keeps stickers from piling up.")
        current_id = _as_int(self._session().get("message_id")) if chat == self._current_telegram_chat() else None
        last_id = _as_int(memory.get("last_message_id"))
        gap_needed = self.min_messages()
        if gap_needed and current_id is not None and last_id is not None and 0 <= current_id - last_id < gap_needed:
            raise StickerError(f"The last sticker in this chat was only {current_id - last_id} messages ago; "
                               f"stickers are paced to about one per {gap_needed} messages. Answer in words.")
        catalog = without(self.catalog(), self.banned())
        descriptions = self.descriptions()
        recent = list(memory.get("recent") or [])
        name, sticker = pick(catalog, self.packs(), str(args.get("sticker") or ""), self._rng,
                             descriptions=descriptions, avoid=recent)
        client = self.client()
        params: dict[str, Any] = {"chat_id": chat, "sticker": sticker["file_id"], "message_thread_id": thread}
        if reply_to is not None:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        try:
            result = client.call("sendSticker", **params)
        except StickerError as exc:
            if reply_to is None or "replied" not in str(exc).lower():
                raise
            params.pop("reply_parameters", None)
            result = client.call("sendSticker", **params)
            reply_to = None
        self._last_sent[chat] = now
        sent_id = _as_int((result or {}).get("message_id"))

        def remember(data: dict[str, Any]) -> None:
            entry = data["chats"].setdefault(chat, {})
            entry["last_ts"] = now
            if sent_id is not None:
                entry["last_message_id"] = sent_id
            entry["recent"] = ([r for r in entry.get("recent", []) if r != sticker_id(name, sticker)]
                               + [sticker_id(name, sticker)])[-RECENT_PER_CHAT:]
        self._update_state(remember)
        out = {
            "success": True,
            "chat_id": chat,
            "message_id": (result or {}).get("message_id"),
            "sticker": sticker_id(name, sticker),
            "emoji": sticker["emoji"],
            "replied_to": reply_to,
            "note": ("The sticker is the whole reply: do not follow it with a line about it (no 'done', 'sent' "
                     "or a description, in any language). Write text only for something worth saying beyond "
                     "the sticker."),
        }
        about = description_of(descriptions, sticker)
        if about:
            out["about"] = about[:ABOUT_CHARS]
        return out

    def mute_current_chat(self) -> dict[str, Any]:
        """Someone asked for no stickers: the agent can switch them off here, never back on."""
        chat = self._current_telegram_chat()
        if not chat:
            raise StickerError("This turn is not in a Telegram chat.")
        self.set_muted(chat, True)
        return {"success": True, "chat_id": chat,
                "note": "Stickers are off in this chat. Tell them it is done; only the owner can switch them "
                        f"back on, with '/stickers on {chat}' in a direct chat with the bot."}

    # slash command
    def command(self, raw_args: str = "") -> str:
        words = (raw_args or "").split()
        sub = words[0].lower() if words else ""
        try:
            if sub in ("", "status", "sync", "refresh", "reload"):
                return self.status_text(refresh=sub in ("sync", "refresh", "reload"))
            if sub in ("off", "on"):
                return self.switch_text(muted=sub == "off", chat=words[1] if len(words) > 1 else "")
            if sub in ("describe", "ban", "unban", "about"):
                self._require_direct_chat()
            if sub == "describe":
                limit = int(words[1]) if len(words) > 1 and words[1].isdigit() else None
                return self.describe_text(limit)
            if sub in ("ban", "unban") and len(words) > 1:
                return self.ban_text(words[1], banned=sub == "ban")
            if sub == "about" and len(words) > 2:
                return self.about_text(words[1], " ".join(words[2:]))
        except StickerError as exc:
            return f"Stickers: {exc}"
        return ("Usage: /stickers [sync | describe [n] | off [chat id] | on [chat id] | ban <id> | unban <id> "
                "| about <id> <text>]")

    def ban_text(self, wanted: str, banned: bool) -> str:
        name, sticker = self._resolve(wanted)

        def change(data: dict[str, Any]) -> None:
            uids = {str(u) for u in data["banned"]}
            uids.add(sticker["uid"]) if banned else uids.discard(sticker["uid"])
            data["banned"] = sorted(uids)
        self._update_state(change)
        return (f"Stickers: {sticker_id(name, sticker)} will not be used any more." if banned
                else f"Stickers: {sticker_id(name, sticker)} is back in use.")

    def about_text(self, wanted: str, text: str) -> str:
        name, sticker = self._resolve(wanted)
        with self._files_lock:
            descriptions = self.descriptions()
            descriptions[sticker["uid"]] = {"text": text.strip()[:300], "source": "owner", "at": round(self._clock())}
            save_json(self._data_dir() / DESCRIPTIONS_FILE, descriptions)
        return f"Stickers: {sticker_id(name, sticker)} is now described as: {text.strip()[:300]}"

    def status_text(self, refresh: bool = False) -> str:
        catalog = self.catalog(refresh=refresh)
        self._merge_hermes_descriptions(catalog)
        descriptions = self.descriptions()
        counts = self.counts(catalog)
        lines = [f"Stickers: {counts['total']} in {len(catalog['packs'])} pack(s), "
                 f"{counts['described']} with a description{' (just reloaded)' if refresh else ''}."]
        for name, pack in catalog["packs"].items():
            described = sum(1 for s in pack["stickers"] if description_of(descriptions, s))
            lines.append(f"• {pack['title']} ({name}): {len(pack['stickers'])}, {described} described")
        for name, why in (catalog.get("skipped") or {}).items():
            lines.append(f"• {name}: not loaded ({why})")
        chat = self._current_telegram_chat()
        muted = self.muted()
        if chat:
            lines.append(f"Stickers are off in this chat ('/stickers on' here, or '/stickers on {chat}' in a direct "
                         "chat with the bot)." if chat in muted
                         else "Stickers are on in this chat ('/stickers off' mutes them here).")
        if muted and self._in_direct_chat():  # other chats' ids only for the owner, never in a group
            lines.append(f"Stickers are off in {len(muted)} chat(s): {', '.join(sorted(muted))}. "
                         "'/stickers on <chat id>' here switches one back on.")
        banned = self.banned()
        if banned:
            lines.append(f"{len(banned)} sticker(s) banned ('/stickers unban <id>' brings one back).")
        if counts["described"] < counts["total"]:
            lines.append(f"'/stickers describe' lets the vision model describe up to {self.describe_batch()} more, "
                         "so the agent can pick stickers by meaning, not only by emoji.")
        return "\n".join(lines)

    def describe_text(self, limit: int | None = None) -> str:
        result = self.describe(limit)
        parts = [f"described {result['described']} with the vision model"]
        if result["from_hermes"]:
            parts.append(f"took {result['from_hermes']} from Hermes' own sticker cache")
        lines = [f"Stickers: {', '.join(parts)}. {result['with_description']} of {result['total']} "
                 f"now have a description."]
        if result["left"]:
            lines.append(f"{result['left']} left: run '/stickers describe' again for the next batch.")
        for problem in result["problems"][:5]:
            lines.append(f"• {problem}")
        return "\n".join(lines)

    def switch_text(self, muted: bool, chat: str = "") -> str:
        if chat:  # any chat by id: owner only, since anyone in a group may be able to run slash commands
            self._require_direct_chat()
            chat = chat_id_from_text(chat)
            if not muted and chat not in self.muted():
                return f"Stickers: they were not off in chat {chat}. '/stickers' lists the chats where they are off."
            self.set_muted(chat, muted)
            return (f"Stickers: switched off in chat {chat}. The agent answers in words there."
                    if muted else f"Stickers: switched on in chat {chat}.")
        chat = self._current_telegram_chat()
        if not chat:
            return ("Stickers: '/stickers on' and '/stickers off' work inside a Telegram chat, or name one: "
                    "'/stickers on <chat id>'.")
        self.set_muted(chat, muted)
        return ("Stickers: switched off in this chat. The agent answers in words here."
                if muted else "Stickers: switched on in this chat.")
