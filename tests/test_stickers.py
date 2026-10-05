"""Offline tests: a fake Telegram Bot API stands in for the network."""

from __future__ import annotations

import importlib.util
import io
import json
import random
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import stickers as st  # noqa: E402

TOKEN = "123456:TEST-TOKEN"


class FakeTelegram:
    """Records calls and answers like the Bot API, including getFile and file downloads."""

    def __init__(self, sets=None, fail=None, next_id=777):
        self.sets = sets if sets is not None else {
            "cats": {"title": "Cats", "stickers": [
                {"file_id": "cat1", "file_unique_id": "u-cat1", "emoji": "😏"},
                {"file_id": "cat2", "file_unique_id": "u-cat2", "emoji": "❤️"},
                {"file_id": "cat3", "file_unique_id": "u-cat3", "emoji": "🤣", "is_video": True,
                 "thumbnail": {"file_id": "cat3-thumb"}},
            ]},
            "memes": {"title": "Memes", "stickers": [
                {"file_id": "meme1", "file_unique_id": "u-meme1", "emoji": "😏", "is_animated": True},
            ]},
        }
        self.fail = fail or {}
        self.calls = []
        self.downloads = []
        self.next_id = next_id

    def __call__(self, request, timeout=None):
        if request.data is None:  # GET of a file: /file/bot<token>/<file_path>
            self.downloads.append(request.full_url)
            if "download" in self.fail:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b""))
            return _Resp(b"RIFF-fake-image-bytes")
        method = request.full_url.rsplit("/", 1)[-1]
        params = dict(urllib.parse.parse_qsl(request.data.decode()))
        self.calls.append((method, params))
        if method in self.fail:
            desc = self.fail[method](params) if callable(self.fail[method]) else self.fail[method]
            if desc:
                body = json.dumps({"ok": False, "description": desc}).encode()
                raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(body))
        if method == "getStickerSet":
            name = params["name"]
            if name not in self.sets:
                body = json.dumps({"ok": False, "description": "Bad Request: STICKERSET_INVALID"}).encode()
                raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(body))
            return _Resp({"ok": True, "result": self.sets[name]})
        if method == "getFile":
            ext = ".webp" if "thumb" not in params["file_id"] else ".jpg"
            return _Resp({"ok": True, "result": {"file_path": f"stickers/{params['file_id']}{ext}"}})
        if method == "sendSticker":
            self.next_id += 1
            return _Resp({"ok": True, "result": {"message_id": self.next_id - 1}})
        raise AssertionError(f"unexpected method {method}")

    def methods(self):
        return [m for m, _ in self.calls]


class _Resp(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def no_vision(path, prompt):
    raise st.StickerError("vision is not configured in this test")


def make_service(tmp_path, fake, config=None, session=None, clock=None, seed=1,
                 describer=no_vision, hermes=None):
    cfg = {"packs": ["cats", "memes"], "cooldown_seconds": 20, "allow_other_chats": False}
    cfg.update(config or {})
    now = {"t": 1_000_000.0}
    sess = session if session is not None else {
        "platform": "telegram", "chat_id": "-100500", "thread_id": "42", "message_id": "9"}
    service = st.StickerService(
        get_config=lambda key, default=None: cfg.get(key, default),
        data_dir=lambda: tmp_path,
        opener=fake,
        clock=clock or (lambda: now["t"]),
        rng=random.Random(seed),
        session=lambda: dict(sess),
        describer=describer,
        hermes_descriptions=lambda: dict(hermes or {}),
    )
    return service, now, cfg


def write_descriptions(tmp_path, **by_uid):
    (tmp_path / st.DESCRIPTIONS_FILE).write_text(
        json.dumps({uid.replace("_", "-"): {"text": text, "source": "test"} for uid, text in by_uid.items()}),
        encoding="utf-8")


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)


def test_pack_name_accepts_links():
    assert st.pack_name("https://t.me/addstickers/cats") == "cats"
    assert st.pack_name("t.me/addstickers/cats/") == "cats"
    assert st.pack_name(" cats ") == "cats"


def test_find_without_arguments_lists_packs_and_emojis(tmp_path):
    fake = FakeTelegram()
    service, _, _ = make_service(tmp_path, fake)
    out = service.find({})
    assert [p["pack"] for p in out["packs"]] == ["cats", "memes"]
    assert out["packs"][0]["count"] == 3
    assert "❤" in out["packs"][0]["emojis"]


def test_find_matches_emoji_ignoring_variation_selector(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram())
    out = service.find({"emoji": "❤"})
    assert [s["id"] for s in out["stickers"]] == ["cats:2"]
    out = service.find({"emoji": "😏"})
    assert {s["id"] for s in out["stickers"]} == {"cats:1", "memes:1"}


def test_catalog_is_cached_until_bot_or_packs_change(tmp_path, monkeypatch):
    fake = FakeTelegram()
    service, _, cfg = make_service(tmp_path, fake)
    service.find({})
    service.find({"emoji": "😏"})
    assert fake.methods().count("getStickerSet") == 2  # one per pack, once
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:OTHER")
    service.find({})
    assert fake.methods().count("getStickerSet") == 4  # new bot, new file_ids
    cfg["packs"] = ["cats"]
    service.find({})
    assert fake.methods().count("getStickerSet") == 5


def test_missing_pack_is_reported_not_fatal(tmp_path):
    service, _, cfg = make_service(tmp_path, FakeTelegram())
    cfg["packs"] = ["cats", "nope"]
    out = service.find({})
    assert [p["pack"] for p in out["packs"]] == ["cats"]
    assert "STICKERSET_INVALID" in out["skipped"]["nope"]


def test_no_packs_configured_explains_setting(tmp_path):
    service, _, cfg = make_service(tmp_path, FakeTelegram())
    cfg["packs"] = []
    with pytest.raises(st.StickerError, match="settings.packs"):
        service.find({})


def test_send_replies_in_current_chat_and_topic(tmp_path):
    fake = FakeTelegram()
    service, _, _ = make_service(tmp_path, fake)
    out = service.send({"sticker": "❤️"})
    method, params = fake.calls[-1]
    assert method == "sendSticker"
    assert params["chat_id"] == "-100500"
    assert params["sticker"] == "cat2"
    assert params["message_thread_id"] == "42"
    assert json.loads(params["reply_parameters"]) == {"message_id": 9, "allow_sending_without_reply": True}
    assert out["success"] and out["sticker"] == "cats:2" and out["message_id"] == 777


def test_general_topic_is_sent_without_thread(tmp_path):
    fake = FakeTelegram()
    session = {"platform": "telegram", "chat_id": "-100500", "thread_id": "1", "message_id": "9"}
    service, _, _ = make_service(tmp_path, fake, session=session)
    service.send({"sticker": "cats:1"})
    assert "message_thread_id" not in fake.calls[-1][1]


def test_reply_false_sends_unanchored(tmp_path):
    fake = FakeTelegram()
    service, _, _ = make_service(tmp_path, fake)
    service.send({"sticker": "cats:1", "reply": False})
    assert "reply_parameters" not in fake.calls[-1][1]


def test_exact_id_and_unknown_id(tmp_path):
    fake = FakeTelegram()
    service, now, _ = make_service(tmp_path, fake, config={"cooldown_seconds": 0})
    service.send({"sticker": "memes:1"})
    assert fake.calls[-1][1]["sticker"] == "meme1"
    with pytest.raises(st.StickerError, match="no sticker memes:5"):
        service.send({"sticker": "memes:5"})


def test_unknown_emoji_points_to_find(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram())
    with pytest.raises(st.StickerError, match="These emojis have stickers: 😏❤🤣\\. Send the closest"):
        service.send({"sticker": "🦄"})


def test_cooldown_blocks_second_sticker_in_same_chat(tmp_path):
    fake = FakeTelegram()
    service, now, _ = make_service(tmp_path, fake)
    service.send({"sticker": "😏"})
    now["t"] += 5
    with pytest.raises(st.StickerError, match="Wait 16 s"):
        service.send({"sticker": "😏"})
    now["t"] += 16
    service.send({"sticker": "😏"})
    assert fake.methods().count("sendSticker") == 2


def test_other_chats_need_owner_permission(tmp_path):
    fake = FakeTelegram()
    service, _, cfg = make_service(tmp_path, fake)
    with pytest.raises(st.StickerError, match="allow_other_chats"):
        service.send({"sticker": "😏", "chat_id": "-200"})
    cfg["allow_other_chats"] = True
    service.send({"sticker": "😏", "chat_id": "-200", "thread_id": 7})
    params = fake.calls[-1][1]
    assert params["chat_id"] == "-200" and params["message_thread_id"] == "7"
    assert "reply_parameters" not in params  # the current message lives in another chat


def test_outside_telegram_needs_explicit_allowed_chat(tmp_path):
    session = {"platform": "discord", "chat_id": "55", "thread_id": "", "message_id": "1"}
    service, _, cfg = make_service(tmp_path, FakeTelegram(), session=session)
    with pytest.raises(st.StickerError, match="not in a Telegram chat"):
        service.send({"sticker": "😏"})


def test_reply_error_retries_without_anchor(tmp_path):
    attempts = {"n": 0}

    def fail_first(params):
        attempts["n"] += 1
        return "Bad Request: message to be replied not found" if "reply_parameters" in params else None

    fake = FakeTelegram(fail={"sendSticker": fail_first})
    service, _, _ = make_service(tmp_path, fake)
    out = service.send({"sticker": "😏"})
    assert attempts["n"] == 2 and out["replied_to"] is None


def test_errors_never_contain_the_token(tmp_path):
    fake = FakeTelegram(fail={"sendSticker": "Forbidden: bot was blocked by the user"})
    service, _, _ = make_service(tmp_path, fake)
    with pytest.raises(st.StickerError) as info:
        service.send({"sticker": "😏"})
    assert "TEST-TOKEN" not in str(info.value)
    assert "blocked" in str(info.value)


def test_missing_token(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    service, _, _ = make_service(tmp_path, FakeTelegram())
    with pytest.raises(st.StickerError, match="TELEGRAM_BOT_TOKEN"):
        service.find({})


# --- v0.2: descriptions, picking by meaning, repeats, per-chat switch ---------------------------


def test_catalog_keeps_unique_ids_and_previews_and_resyncs_old_format(tmp_path):
    fake = FakeTelegram()
    (tmp_path / st.CATALOG_FILE).write_text(json.dumps({"bot_id": "123456", "pack_list": ["cats", "memes"],
                                                        "synced_at": 1_000_000.0, "packs": {}}), encoding="utf-8")
    service, _, _ = make_service(tmp_path, fake)
    catalog = service.catalog()
    assert fake.methods().count("getStickerSet") == 2  # the old file had no version: synced again
    cat3 = catalog["packs"]["cats"]["stickers"][2]
    assert cat3["uid"] == "u-cat3" and cat3["thumb"] == "cat3-thumb" and cat3["kind"] == "video"


def test_describe_reuses_hermes_cache_then_asks_vision_with_a_local_file(tmp_path):
    fake = FakeTelegram()
    seen, prompts = [], []

    def describer(path, prompt):
        seen.append(path)
        prompts.append(prompt)
        assert path.exists() and path.parent == tmp_path / "tmp"
        return f"picture from {path.name}"

    service, _, _ = make_service(tmp_path, fake, describer=describer,
                                 hermes={"u-cat1": "A smug cat smirking at the viewer"})
    result = service.describe()
    assert result["from_hermes"] == 1 and result["described"] == 2
    assert [p.name for p in seen] == ["u-cat2.webp", "u-cat3.jpg"]  # video sticker: its still preview
    assert not any(p.exists() for p in seen)  # temporary copies are removed
    assert [params["file_id"] for m, params in fake.calls if m == "getFile"] == ["cat2", "cat3-thumb"]
    assert any("no still preview" in problem for problem in result["problems"])  # animated without preview
    saved = json.loads((tmp_path / st.DESCRIPTIONS_FILE).read_text(encoding="utf-8"))
    assert saved["u-cat1"]["source"] == "hermes" and saved["u-cat2"]["source"] == "vision"
    assert result["with_description"] == 3 and result["left"] == 1
    assert all("Reactions:" in q for q in prompts) and "tags it with ❤" in prompts[0]
    assert "one frame of an animated sticker" in prompts[1] and "one frame" not in prompts[0]
    assert saved["u-cat2"]["prompt"] == st.PROMPT_VERSION


def test_describe_works_in_batches(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram(), describer=lambda p, q: "a described picture")
    first = service.describe(limit=1)
    assert first["described"] == 1 and first["left"] == 3
    second = service.describe(limit=1)
    assert second["described"] == 1 and second["with_description"] == 2


def test_describe_stops_after_repeated_vision_failures(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram())
    result = service.describe()
    assert result["described"] == 0 and "stopped" in result["problems"][-1]
    assert "vision is not configured" in service.command("describe")


def test_download_errors_never_contain_the_token(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram(fail={"download": True}), describer=lambda p, q: "x")
    result = service.describe(limit=1)
    assert "HTTP 404" in result["problems"][0] and "TEST-TOKEN" not in " ".join(result["problems"])


def test_find_by_words_ranks_described_stickers(tmp_path):
    write_descriptions(tmp_path, u_cat1="A smug cat smirking", u_cat2="A cat hugging a big red heart",
                       u_meme1="A dog laughing hard")
    service, _, _ = make_service(tmp_path, FakeTelegram())
    out = service.find({"query": "laughing dog"})
    assert out["stickers"][0]["id"] == "memes:1" and out["stickers"][0]["about"] == "A dog laughing hard"
    assert service.find({"query": "hearts"})["stickers"][0]["id"] == "cats:2"
    empty = service.find({"query": "spaceship"})
    assert empty["total"] == 0 and "No sticker fits" in empty["hint"] and "😏❤🤣" in empty["hint"]
    summary = service.find({})
    assert summary["packs"][0]["described"] == 2


def test_send_by_words_skips_what_was_just_sent(tmp_path):
    write_descriptions(tmp_path, u_cat1="A smug cat smirking", u_cat2="A smug cat in sunglasses")
    fake = FakeTelegram()
    service, _, _ = make_service(tmp_path, fake, config={"cooldown_seconds": 0})
    first = service.send({"sticker": "smug cat"})
    second = service.send({"sticker": "smug cat"})
    assert {first["sticker"], second["sticker"]} == {"cats:1", "cats:2"}
    assert service.send({"sticker": "smug cat"})["success"]  # all recent: repeats rather than fails
    assert first["about"].startswith("A smug cat")


def test_emoji_pick_skips_what_was_just_sent(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram(), config={"cooldown_seconds": 0})
    sent = {service.send({"sticker": "😏"})["sticker"] for _ in range(2)}
    assert sent == {"cats:1", "memes:1"}


def test_words_without_descriptions_point_to_describe(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram())
    with pytest.raises(st.StickerError, match="/stickers describe"):
        service.send({"sticker": "happy cat"})


def test_stickers_can_be_switched_off_in_one_chat(tmp_path):
    fake = FakeTelegram()
    service, _, _ = make_service(tmp_path, fake, config={"cooldown_seconds": 0})
    assert "switched off" in service.command("off")
    with pytest.raises(st.StickerError, match="switched stickers off"):
        service.send({"sticker": "😏"})
    assert "off in this chat" in service.command("")
    assert "switched on" in service.command("on")
    assert service.send({"sticker": "😏"})["success"]
    outside, _, _ = make_service(tmp_path / "cli", FakeTelegram(),
                                 session={"platform": "", "chat_id": "", "thread_id": "", "message_id": ""})
    assert "inside a Telegram chat" in outside.command("off")


def test_status_shows_descriptions_and_the_next_step(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram(), hermes={"u-cat2": "A cat hugging a red heart"})
    status = service.command("")
    assert status.startswith("Stickers: 4 in 2 pack(s), 1 with a description.")
    assert "Cats (cats): 3, 1 described" in status and "'/stickers describe'" in status


def test_stickers_are_paced_by_messages(tmp_path):
    fake = FakeTelegram(next_id=200)
    session = {"platform": "telegram", "chat_id": "-100500", "thread_id": "", "message_id": "199"}
    service, _, _ = make_service(tmp_path, fake, session=session,
                                 config={"cooldown_seconds": 0, "min_messages_between": 8})
    assert service.send({"sticker": "😏"})["message_id"] == 200
    session["message_id"] = "204"
    with pytest.raises(st.StickerError, match="only 4 messages ago"):
        service.send({"sticker": "😏"})
    session["message_id"] = "209"
    assert service.send({"sticker": "😏"})["success"]


def test_cooldown_pacing_and_recent_picks_survive_a_restart(tmp_path):
    first, _, _ = make_service(tmp_path, FakeTelegram(), config={"cooldown_seconds": 0, "min_messages_between": 0})
    sent = first.send({"sticker": "😏"})["sticker"]
    again, _, _ = make_service(tmp_path, FakeTelegram(), config={"cooldown_seconds": 0, "min_messages_between": 0})
    assert again.send({"sticker": "😏"})["sticker"] != sent  # the restarted service remembers what it sent
    strict, _, _ = make_service(tmp_path, FakeTelegram(), config={"cooldown_seconds": 20})
    with pytest.raises(st.StickerError, match="already went to this chat"):
        strict.send({"sticker": "😏"})


def test_anyone_can_ask_the_agent_to_stop(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram())
    out = service.mute_current_chat()
    assert out["success"] and "only the owner" in out["note"]
    with pytest.raises(st.StickerError, match="switched stickers off"):
        service.send({"sticker": "😏"})
    outside, _, _ = make_service(tmp_path / "cli", FakeTelegram(),
                                 session={"platform": "", "chat_id": "", "thread_id": "", "message_id": ""})
    with pytest.raises(st.StickerError, match="not in a Telegram chat"):
        outside.mute_current_chat()


def test_owner_can_ban_and_describe_stickers_in_a_direct_chat(tmp_path):
    service, _, _ = make_service(tmp_path, FakeTelegram(), config={"cooldown_seconds": 0, "min_messages_between": 0})
    assert "will not be used" in service.command("ban cats:1")
    assert [s["id"] for s in service.find({"emoji": "😏"})["stickers"]] == ["memes:1"]
    assert "1 sticker(s) banned" in service.command("")
    assert "back in use" in service.command("unban cats:1")
    assert len(service.find({"emoji": "😏"})["stickers"]) == 2
    assert "described as: cat holding a red heart" in service.command("about cats:2 cat holding a red heart")
    hit = service.find({"query": "red heart"})["stickers"][0]
    assert hit["id"] == "cats:2" and hit["about"] == "cat holding a red heart"
    assert "no sticker 'cats:9'" in service.command("ban cats:9")
    assert service.command("ban").startswith("Usage:")
    group, _, _ = make_service(tmp_path / "g", FakeTelegram(), session={
        "platform": "telegram", "chat_id": "-1", "thread_id": "", "message_id": "1", "chat_type": "group"})
    assert "direct chat" in group.command("ban cats:1") and "direct chat" in group.command("describe")


def test_owner_descriptions_win_and_failures_are_not_retried_forever(tmp_path):
    calls = []

    def failing(path, prompt):
        calls.append(path.name)
        raise st.StickerError("vision is down")

    service, _, _ = make_service(tmp_path, FakeTelegram(), describer=failing)
    service.command("about cats:1 my favourite smug cat")
    for _ in range(3):
        service.describe()
    assert calls.count("u-cat2.webp") == 2 and "u-cat1.webp" not in calls  # two tries, owner text kept
    saved = json.loads((tmp_path / st.DESCRIPTIONS_FILE).read_text(encoding="utf-8"))
    assert saved["u-cat1"]["source"] == "owner" and saved["u-cat2"]["tries"] == 2


def test_register_wires_tools_command_and_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "telegram_stickers_plugin", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["telegram_stickers_plugin"] = module
    spec.loader.exec_module(module)

    class Ctx:
        def __init__(self):
            self.tools, self.commands, self.skills = {}, {}, {}

        def get_config(self, key, default=None):
            return default

        def register_tool(self, name, toolset, schema, handler, **kw):
            self.tools[name] = (toolset, schema, handler, kw)

        def register_command(self, name, handler, **kw):
            self.commands[name] = handler

        def register_skill(self, name, path, **kw):
            self.skills[name] = Path(path)

    ctx = Ctx()
    module.register(ctx)
    assert set(ctx.tools) == {"telegram_sticker_find", "telegram_sticker_send", "telegram_sticker_mute"}
    assert ctx.tools["telegram_sticker_send"][1]["parameters"]["required"] == ["sticker"]
    assert set(ctx.commands) == {"stickers"}
    assert ctx.commands["stickers"]("dance").startswith("Usage:")
    assert ctx.skills["sticker-etiquette"].exists()
    # No packs configured: the tool answers with a readable error, not an exception.
    result = json.loads(ctx.tools["telegram_sticker_find"][2]({}))
    assert "settings.packs" in result["error"]
    assert "No sticker packs" in ctx.commands["stickers"]("")


# --- v0.1.1: switching a chat by id from a direct chat, the muted list, the note after a sticker -------

DM = {"platform": "telegram", "chat_id": "555", "thread_id": "", "message_id": "3", "chat_type": "dm"}
GROUP = {"platform": "telegram", "chat_id": "-1001234567890", "thread_id": "", "message_id": "40", "chat_type": "group"}


def test_owner_switches_a_chat_by_id_in_a_direct_chat(tmp_path):
    owner, _, _ = make_service(tmp_path, FakeTelegram(), session=DM)
    group, _, _ = make_service(tmp_path, FakeTelegram(), session=GROUP)
    assert "switched off in chat -1001234567890" in owner.command("off -1001234567890")
    assert owner.muted() == {"-1001234567890"}  # the direct chat itself stays on
    with pytest.raises(st.StickerError, match="'/stickers on -1001234567890' in a direct chat"):
        group.send({"sticker": "😏"})
    assert "switched on in chat -1001234567890" in owner.command("on -1001234567890")
    assert group.send({"sticker": "😏"})["success"]
    assert "were not off in chat -1001234567890" in owner.command("on -1001234567890")


def test_switching_a_chat_by_id_is_refused_in_a_group(tmp_path):
    group, _, _ = make_service(tmp_path, FakeTelegram(), session=GROUP)
    note = group.mute_current_chat()["note"]
    assert "'/stickers on -1001234567890' in a direct chat" in note
    for command in ("on -1001234567890", "off -100777", "on nonsense"):
        assert "direct chat" in group.command(command)
    assert group.muted() == {"-1001234567890"}
    assert "switched on in this chat" in group.command("on")  # the plain form still works where it arrives


def test_malformed_chat_ids_are_refused(tmp_path):
    owner, _, _ = make_service(tmp_path, FakeTelegram(), session=DM)
    for bad in ("@mygroup", "abc", "-100abc", "--100", "0", "12.5", "1" * 25):
        assert "is not a Telegram chat id" in owner.command(f"off {bad}")
    assert owner.muted() == set()


def test_status_lists_muted_chats_for_the_owner_only(tmp_path):
    owner, _, _ = make_service(tmp_path, FakeTelegram(), session=DM)
    owner.command("off -1001234567890")
    owner.command("off -100777")
    status = owner.command("")
    assert "Stickers are off in 2 chat(s): -1001234567890, -100777." in status
    assert "'/stickers on <chat id>' here switches one back on" in status
    group, _, _ = make_service(tmp_path, FakeTelegram(), session=GROUP)
    in_group = group.command("")
    assert "-100777" not in in_group  # other chats' ids are not shown in a group
    assert "'/stickers on -1001234567890' in a direct chat" in in_group


def test_note_after_a_sticker_says_it_is_the_whole_reply(tmp_path):
    note = make_service(tmp_path, FakeTelegram())[0].send({"sticker": "😏"})["note"]
    assert "whole reply" in note and "'done'" in note and "'sent'" in note and "beyond the sticker" in note
    assert len(note) < 220  # one or two short sentences


REACTION_SETS = {
    "duck": {"title": "Duck", "stickers": [
        {"file_id": "d1", "file_unique_id": "u-d1", "emoji": "😂"},
        {"file_id": "d2", "file_unique_id": "u-d2", "emoji": "🙄"},
        {"file_id": "d3", "file_unique_id": "u-d3", "emoji": "🤷‍♂️", "is_animated": True,
         "thumbnail": {"file_id": "d3-thumb"}},
        {"file_id": "d4", "file_unique_id": "u-d4", "emoji": "😱"},
    ]},
}


def reaction_service(tmp_path, **kwargs):
    fake = FakeTelegram(sets=REACTION_SETS)
    config = {"packs": ["duck"], "cooldown_seconds": 0, "min_messages_between": 0}
    service, now, _ = make_service(tmp_path, fake, config=config, **kwargs)
    return service, fake


def test_emoji_key_ignores_gender_skin_tone_and_selectors():
    assert st.emoji_key("🤦🏽‍♀️") == st.emoji_key("🤦‍♂") == st.emoji_key("🤦") == "🤦"
    assert st.emoji_key("❤️") == "❤" and st.emoji_key("👍🏻") == "👍"
    assert st.emoji_key("👨‍💻") == "👨‍💻"  # other joined emojis stay whole


def test_emoji_with_or_without_gender_finds_the_same_sticker(tmp_path):
    service, _ = reaction_service(tmp_path)
    for emoji in ("🤷", "🤷‍♀️", "🤷🏻‍♂️"):
        assert [s["id"] for s in service.find({"emoji": emoji})["stickers"]] == ["duck:3"]


def test_reaction_word_finds_a_related_emoji_without_descriptions(tmp_path):
    service, fake = reaction_service(tmp_path)
    sent = service.send({"sticker": "facepalm"})
    assert sent["sticker"] == "duck:2" and fake.calls[-1][1]["sticker"] == "d2"  # 🙄: no pack has 🤦
    assert service.find({"query": "lol"})["stickers"][0]["id"] == "duck:1"


def test_two_word_reactions_and_hyphens():
    approve = st.reactions_in("thumbs up")
    assert approve and approve[0][0][0] == "👍"
    assert st.reactions_in("eye-roll")[0][0][0] == "🙄"
    oh_no = st.reactions_in("oh no")
    assert len(oh_no) == 1 and oh_no[0][0][0] == "😱"  # not the 👎 of a lone "no"


def test_parts_of_a_compound_word_match_a_description():
    sticker = {"emoji": "😱"}
    assert st.relevance("facepalm", {"emoji": ""}, "A duck holds its face in its hands") > 0
    assert st.relevance("eyeroll", {"emoji": ""}, "A duck about to roll") > 0
    assert st.relevance("flight", {"emoji": ""}, "A light-gray cat") == 0  # a part, not a word inside
    assert st.relevance("facepalm", sticker, "A duck holds its face in its hands") > \
        st.relevance("facepalm", sticker, "A duck standing in the rain")


def test_missing_emoji_falls_back_to_the_nearest_feeling(tmp_path):
    service, fake = reaction_service(tmp_path)
    sent = service.send({"sticker": "🤦‍♀️"})
    assert sent["sticker"] == "duck:2" and sent["instead_of"] == "🤦‍♀️"
    found = service.find({"emoji": "🤦"})
    assert [s["id"] for s in found["stickers"]] == ["duck:2"] and "nearest emoji" in found["hint"]
    assert "instead_of" not in service.send({"sticker": "😂"})


def test_nothing_fits_lists_the_emojis_that_exist(tmp_path):
    service, _ = reaction_service(tmp_path)
    write_descriptions(tmp_path, u_d1="A duck laughing")
    with pytest.raises(st.StickerError, match="These emojis have stickers: 😂🙄🤷‍♂😱"):
        service.send({"sticker": "spaceship"})
    with pytest.raises(st.StickerError, match="These emojis have stickers"):
        service.send({"sticker": "🦄"})
    assert "😂🙄🤷‍♂😱" in service.find({"emoji": "🦄"})["hint"]


def test_best_sticker_just_sent_gives_way_to_the_next_good_one(tmp_path):
    service, _ = reaction_service(tmp_path)
    write_descriptions(tmp_path, u_d2="A duck rolling its eyes", u_d4="A duck holding its face in its hands")
    first = service.send({"sticker": "facepalm"})["sticker"]
    second = service.send({"sticker": "facepalm"})["sticker"]
    assert {first, second} == {"duck:2", "duck:4"}


DM_SESSION = {"platform": "telegram", "chat_id": "555", "thread_id": "", "message_id": "3", "chat_type": "dm"}


def test_describe_again_redoes_only_old_machine_descriptions(tmp_path):
    prompts = []

    def describer(path, prompt):
        prompts.append((path.name, prompt))
        return f"new words for {path.name}. Reactions: facepalm"

    (tmp_path / st.DESCRIPTIONS_FILE).write_text(json.dumps({
        "u-d1": {"text": "old vision text", "source": "vision"},
        "u-d2": {"text": "my own words", "source": "owner"},
        "u-d3": {"text": "from the hermes cache", "source": "hermes"},
        "u-d4": {"text": "already new", "source": "vision", "prompt": st.PROMPT_VERSION},
    }), encoding="utf-8")
    service, _ = reaction_service(tmp_path, describer=describer, session=DM_SESSION)
    assert "2 description(s) have no reaction words" in service.command("")
    assert "Stickers: described 0 with" in service.command("describe")  # nothing missing: no redo
    reply = service.command("describe again")
    assert "described 2 again" in reply and "0 older description(s) left" in reply
    assert [name for name, _ in prompts] == ["u-d1.webp", "u-d3.jpg"]  # animated: its still preview
    saved = json.loads((tmp_path / st.DESCRIPTIONS_FILE).read_text(encoding="utf-8"))
    assert saved["u-d2"]["text"] == "my own words" and saved["u-d4"]["text"] == "already new"
    assert saved["u-d1"]["prompt"] == saved["u-d3"]["prompt"] == st.PROMPT_VERSION
    assert "no reaction words" not in service.command("")


def test_failed_redo_keeps_the_old_description(tmp_path):
    def failing(path, prompt):
        raise st.StickerError("vision is down")

    (tmp_path / st.DESCRIPTIONS_FILE).write_text(json.dumps({
        "u-d1": {"text": "old vision text", "source": "vision"}}), encoding="utf-8")
    service, _ = reaction_service(tmp_path, describer=failing, session=DM_SESSION)
    for _ in range(3):
        service.command("describe again")
    saved = json.loads((tmp_path / st.DESCRIPTIONS_FILE).read_text(encoding="utf-8"))
    assert saved["u-d1"]["text"] == "old vision text" and saved["u-d1"]["redo_tries"] == 2
