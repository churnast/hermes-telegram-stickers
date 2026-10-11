"""Long and hostile text from the model, through the tools' entry points, on 2,000 stickers; and the bot token read
through Hermes' secret scope.

A search reads every sticker in every pack in use, and learned packs have no cap, so the model's text is cut before
anything reads it (MAX_QUERY_CHARS, MAX_QUERY_WORDS, MAX_EMOJI_CHARS). Each timing is the best of three runs and
must stay under 100 ms; 1.1.0 took up to 70 s here on a 16,000-character query.
"""

from __future__ import annotations

import contextlib
import json
import random
import sys
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from test_stickers import TOKEN, FakeTelegram, make_service  # noqa: E402

import stickers as st  # noqa: E402

LIMIT_MS = 100
WORDS = ["cat", "dog", "happy", "sad", "laugh", "cry", "sleep", "party", "coffee", "love", "angry", "shock", "cool",
         "think", "work", "travel", "dance", "money", "face", "hands"]
EMOJIS = "😂🤣😭😢❤😍😘🤗😡😱😎🤔😴🥳👍👎👋🙏👏🔥"
PACKS = [f"Pack{i}" for i in range(40)]


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """40 packs of 50 stickers, every sticker described with 25 words."""
    rng = random.Random(1)
    sets = {name: {"title": f"Pack {i}", "stickers": [
        {"file_id": f"{i}-{j}", "file_unique_id": f"u-{i}-{j}", "emoji": rng.choice(EMOJIS)} for j in range(50)]}
        for i, name in enumerate(PACKS)}
    path = tmp_path_factory.mktemp("big")
    (path / st.DESCRIPTIONS_FILE).write_text(json.dumps({
        f"u-{i}-{j}": {"text": " ".join(rng.choice(WORDS) for _ in range(25)), "source": "test"}
        for i in range(40) for j in range(50)}), encoding="utf-8")
    return path, sets


def service_for(big, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    path, sets = big
    service, _, _ = make_service(path, FakeTelegram(sets=sets), config={
        "packs": PACKS, "cooldown_seconds": 0, "min_messages_between": 0},
        session={"platform": "telegram", "chat_id": "555", "chat_type": "dm", "message_id": "100"})
    service.catalog()
    return service


def best_ms(call) -> float:
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        with contextlib.suppress(st.StickerError):
            call()
        best = min(best, (time.perf_counter() - start) * 1000)
    return best


def distinct(chars: int, length: int = 5) -> str:
    return " ".join(f"w{i:0{length - 2}d}x" for i in range(chars // (length + 1)))


HOSTILE = {
    "find, 16,000 chars of distinct words": lambda s: s.find({"query": distinct(16_000)}),
    "find, 16,000 chars of long distinct words": lambda s: s.find({"query": distinct(16_000, 12)}),
    "find, 30,000 chars of 'facepalm '": lambda s: s.find({"query": "facepalm " * 3_400}),
    "find, one 100,000-char word": lambda s: s.find({"query": "a" * 100_000}),
    "find, 10,000 emoji no pack has": lambda s: s.find({"emoji": "🍒🚀🦄🧲🪐🫧" * 1_700}),
    "find, 100,000-char pack name": lambda s: s.find({"pack": "p" * 100_000, "emoji": "😂"}),
    "send, 16,000 chars of distinct words": lambda s: s.send({"sticker": distinct(16_000)}),
    "send, 10,000 emoji no pack has": lambda s: s.send({"sticker": "🍒🚀🦄🧲🪐🫧" * 1_700}),
    "send, an id with 5,000 digits": lambda s: s.send({"sticker": "Pack1:" + "1" * 5_000}),
    "settings, forget a 100,000-char pack": lambda s: s.settings({"action": "forget_pack", "pack": "cat " * 25_000}),
    "settings, unban an id with 5,000 digits":
        lambda s: s.settings({"action": "unban", "sticker": "Pack1:" + "1" * 5_000}),
}


@pytest.mark.parametrize("label", list(HOSTILE))
def test_hostile_text_is_fast(big, monkeypatch, label):
    service = service_for(big, monkeypatch)
    took = best_ms(lambda: HOSTILE[label](service))
    assert took < LIMIT_MS, f"{label}: {took:.1f} ms"


def test_long_query_counts_only_its_start(big, monkeypatch):
    """The first MAX_QUERY_WORDS words of the first MAX_QUERY_CHARS characters decide; the rest is not read."""
    service = service_for(big, monkeypatch)
    short = service.find({"query": "sleepy cat", "limit": 50})
    padded = service.find({"query": "sleepy cat " + "zzz " * 10_000, "limit": 50})
    assert padded == short
    words = " ".join(["qqqq"] * st.MAX_QUERY_WORDS)
    assert service.find({"query": f"{words} cat"})["stickers"] == []


def test_overlong_ids_are_refused_cleanly(big, monkeypatch):
    service = service_for(big, monkeypatch)
    assert st._as_int("1" * 5_000) is None
    assert st._as_int("-" + "1" * 21) is None
    assert st._as_int("-1001234567890") == -1001234567890
    assert st._as_int("²") is None
    out = service.send({"sticker": "Pack1:" + "1" * 5_000})  # not an id: read as words ('pack1' names the pack)
    assert out["sticker"].startswith("Pack1:")
    with pytest.raises(st.StickerError, match="There is no sticker"):
        service._resolve("Pack1:" + "1" * 5_000)
    out = service.send({"sticker": "Pack1:3"})
    assert out["sticker"] == "Pack1:3"


# --- the bot token through Hermes' secret scope ---------------------------------------------------------------


def fake_secret_scope(monkeypatch, get_secret):
    agent = types.ModuleType("agent")
    scope = types.ModuleType("agent.secret_scope")
    scope.get_secret = get_secret
    agent.secret_scope = scope
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)


def test_token_comes_from_hermes_secret_scope(monkeypatch, tmp_path):
    """With several profiles in one gateway, os.environ may hold another profile's token: Hermes' scope decides."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "111:OTHER-PROFILE")
    asked = []

    def get_secret(name, default=None):
        asked.append(name)
        return "222:THIS-PROFILE" if name == "TELEGRAM_BOT_TOKEN" else default

    fake_secret_scope(monkeypatch, get_secret)
    assert st.bot_token() == "222:THIS-PROFILE"
    fake = FakeTelegram()
    service, _, _ = make_service(tmp_path, fake)
    service.find({})
    assert asked and all(name == "TELEGRAM_BOT_TOKEN" for name in asked)
    assert json.loads((tmp_path / st.CATALOG_FILE).read_text(encoding="utf-8"))["bot_id"] == "222"


def test_a_refused_secret_read_means_no_token(monkeypatch, tmp_path):
    """Hermes refuses the read (several profiles, no scope on this thread): no fallback to os.environ."""
    def get_secret(name, default=None):
        raise RuntimeError("UnscopedSecretError")

    fake_secret_scope(monkeypatch, get_secret)
    assert st.bot_token() == ""
    service, _, _ = make_service(tmp_path, FakeTelegram())
    with pytest.raises(st.StickerError, match="TELEGRAM_BOT_TOKEN"):
        service.find({})
    assert service.turn_hint("telegram", "555", "haha") == ""


def test_without_hermes_the_environment_is_read(monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)  # import fails, as outside Hermes
    assert st.bot_token() == TOKEN
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert st.bot_token() == ""
