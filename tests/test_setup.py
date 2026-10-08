"""The setup in the owner's direct chat and settings changed in words, one test per scenario.

Each test plays a short dialog the way Hermes runs it: the plugin's Telegram handler notes a sticker
(note_sticker), the pre_llm_call hook learns its pack (admit_turn) and returns the hint for the turn
(turn_hint), and the agent calls telegram_sticker_settings (service.settings).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from test_stickers import DM, GROUP, TOKEN, FakeTelegram, learned_sets, make_service  # noqa: E402

import stickers as st  # noqa: E402

OWNER = "555"


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)


def sets():
    data = learned_sets()
    data["HotCherry_by_bot"] = {"title": "Hot Cherry", "stickers": [
        {"file_id": "ch1", "file_unique_id": "u-ch1", "emoji": "🍒"},
        {"file_id": "ch2", "file_unique_id": "u-ch2", "emoji": "😏"}]}
    for name in st.DEFAULT_PACKS:
        data[name] = {"title": name, "stickers": [{"file_id": f"{name}1", "file_unique_id": f"u-{name}1",
                                                   "emoji": "😏"}]}
    return data


def fresh(tmp_path, packs=(), **config):
    """A fresh install: no packs in settings, the setup on, no pacing in the way of the test."""
    session = {**DM, "message_id": "10"}
    cfg = {"packs": list(packs), "setup": True, "cooldown_seconds": 0, "min_messages_between": 0}
    cfg.update(config)
    service, now, cfg = make_service(tmp_path, FakeTelegram(sets=sets(), next_id=500), config=cfg, session=session)
    service.observer_wired = True
    return service, now, cfg, session


def turn(service, session, sticker="", sender=OWNER, forwarded=False):
    """One message from `sender` in their direct chat (or in the chat the session names): what the hook returns."""
    session["message_id"] = str(int(session["message_id"]) + 2)  # their message and the bot's answer
    if sticker:
        service.note_sticker(sender, sticker, "static")
    if forwarded:
        service.note_forwarded(sender)
    service.admit_turn("telegram", sender, "")
    return service.turn_hint("telegram", sender)


def state(tmp_path):
    return json.loads((tmp_path / st.STATE_FILE).read_text(encoding="utf-8"))


def through_questions(service, session, direct="sometimes", groups="rarely"):
    turn(service, session)
    service.settings({"action": "pace", "where": "direct", "every": direct})
    turn(service, session)
    return service.settings({"action": "pace", "where": "groups", "every": groups})


# --- A. the usual path -----------------------------------------------------------------------------------------

def test_01_first_direct_message_starts_the_setup_with_the_question_how_often(tmp_path):
    service, _, _, session = fresh(tmp_path)
    hint = turn(service, session)
    assert hint.startswith("[telegram-stickers setup]") and st.SETUP_ASK["direct"] in hint
    assert st.TURN_HINT not in hint  # no "a sticker is allowed" during the setup
    assert state(tmp_path)["setup"] == {**state(tmp_path)["setup"], "status": "active", "by": OWNER, "tries": 1}


@pytest.mark.parametrize("every, gap", [("sometimes", 6), ("rarely", 12), ("often", 4), ("2", 4), ("4", 8), (4, 8)])
def test_02_direct_pace_in_words_or_a_number_then_the_groups_question(tmp_path, every, gap):
    service, _, _, session = fresh(tmp_path)
    turn(service, session)
    out = service.settings({"action": "pace", "where": "direct", "every": every})
    assert state(tmp_path)["pace"]["direct"] == gap
    assert out["direct"] == f"about one sticker per {gap // 2} of their messages"
    assert st.SETUP_ASK["groups"] in out["next"]
    assert st.SETUP_ASK["groups"] in turn(service, session)  # a later turn asks the same until it is answered


@pytest.mark.parametrize("every, gap", [("rarely", 12), ("same", 2), ("often", 3), ("20", 20), ("off", -1)])
def test_03_groups_pace_and_then_the_sticker_question(tmp_path, every, gap):
    service, _, _, session = fresh(tmp_path)
    out = through_questions(service, session, direct="2", groups=every)
    assert state(tmp_path)["pace"]["groups"] == gap
    assert "Ask now for a sticker from a pack they like" in out["next"]
    assert "four default Telegram packs" in out["next"]  # no packs of their own yet
    if gap == -1:
        assert out["groups"] == "off"
        service._session = lambda: dict(GROUP)
        assert service.turn_hint("telegram", "") == ""
        with pytest.raises(st.StickerError, match="off in groups"):
            service.send({"sticker": "😏"})


def test_04_05_each_sticker_saves_its_pack_and_asks_for_more(tmp_path):
    service, _, _, session = fresh(tmp_path)
    through_questions(service, session)
    hint = turn(service, session, sticker="HotCherry_by_bot")
    assert 'its pack "Hot Cherry" (HotCherry_by_bot, 2 stickers) is saved' in hint
    assert "ask for a couple more from other packs" in hint
    assert "four default Telegram packs" not in hint  # they have a pack now
    hint = turn(service, session, sticker="dogs")
    assert 'its pack "Dogs" (dogs, 1 stickers) is saved' in hint
    assert service.learned_packs() == ["dogs", "HotCherry_by_bot"]
    assert state(tmp_path)["setup"]["packs"] == ["HotCherry_by_bot", "dogs"]


def test_06_done_sums_up_and_the_setup_ends(tmp_path):
    service, _, _, session = fresh(tmp_path)
    through_questions(service, session, direct="4")
    turn(service, session, sticker="HotCherry_by_bot")
    out = service.settings({"action": "setup_finish"})
    assert out["packs"] == ["Hot Cherry"] and out["default_packs"] is False
    assert out["direct"] == "about one sticker per 4 of their messages"
    assert out["groups"] == "about one sticker per 12 messages"
    assert "remove the pack" in out["next"] and "show my packs" in out["next"]
    assert "send one sticker from HotCherry_by_bot to show" in out["next"]
    assert state(tmp_path)["setup"]["status"] == "done"
    assert turn(service, session) == st.TURN_HINT  # from now on the usual hint


# --- B. answers out of order or unclear ---------------------------------------------------------------------------

def test_07_a_sticker_before_the_first_answer_is_saved_and_the_question_repeats(tmp_path):
    service, _, _, session = fresh(tmp_path)
    hint = turn(service, session, sticker="dogs")  # the very first message is a sticker
    assert 'its pack "Dogs"' in hint and st.SETUP_ASK["direct"] in hint
    assert service.learned_packs() == ["dogs"]
    assert state(tmp_path)["setup"]["status"] == "active"  # a sticker first does not count as "used before"


def test_08_an_unclear_answer_is_left_to_the_agent_with_a_default(tmp_path):
    assert "unclear answer: take sometimes" in st.SETUP_ASK["direct"].lower()
    service, _, _, session = fresh(tmp_path)
    turn(service, session)
    with pytest.raises(st.StickerError, match="rarely, sometimes, often"):
        service.settings({"action": "pace", "where": "direct", "every": "whatever"})
    assert "pace" not in state(tmp_path)


def test_09_every_message_is_allowed_with_a_warning(tmp_path):
    service, _, _, session = fresh(tmp_path)
    turn(service, session)
    out = service.settings({"action": "pace", "where": "direct", "every": "1"})
    assert state(tmp_path)["pace"]["direct"] == 2 and "it is a lot" in out["next"]
    with pytest.raises(st.StickerError, match="from 1 to 100"):
        service.settings({"action": "pace", "where": "direct", "every": "101"})


def test_10_never_switches_the_direct_chat_off_and_ends_the_setup(tmp_path):
    service, _, _, session = fresh(tmp_path)
    turn(service, session)
    out = service.settings({"action": "pace", "where": "direct", "every": "never"})
    assert out["direct"] == "off" and "next" in out and "Ask now" not in out["next"]
    assert OWNER in state(tmp_path)["muted"] and state(tmp_path)["setup"]["status"] == "done"
    assert turn(service, session) == ""  # no setup hint, and no sticker hint in a muted chat


def test_11_skip_keeps_the_defaults_and_says_how_to_come_back(tmp_path):
    service, _, _, session = fresh(tmp_path)
    turn(service, session)
    out = service.settings({"action": "setup_skip"})
    assert out["default_packs"] is True and "set up stickers" in out["next"]
    assert out["direct"] == "no message limit, only the cooldown"  # min_messages_between: 0 in this test
    assert state(tmp_path)["setup"]["status"] == "done" and state(tmp_path)["setup"]["skipped"] is True
    assert turn(service, session) == st.TURN_HINT


def test_12_three_unanswered_turns_pause_the_setup_and_words_restart_it(tmp_path):
    service, _, _, session = fresh(tmp_path)
    for _ in range(3):
        assert turn(service, session).startswith("[telegram-stickers setup]")
    assert turn(service, session) == st.SETUP_PAUSED
    assert state(tmp_path)["setup"]["status"] == "paused"
    assert turn(service, session) == st.TURN_HINT  # said once, then the usual hint
    out = service.settings({"action": "setup_start"})  # "set up stickers"
    assert out["next"] == st.SETUP_ASK["direct"]
    assert st.SETUP_ASK["direct"] in turn(service, session)


def test_12_an_answer_resets_the_count(tmp_path):
    service, _, _, session = fresh(tmp_path)
    turn(service, session)
    turn(service, session)
    service.settings({"action": "pace", "where": "direct", "every": "often"})
    for _ in range(3):
        assert st.SETUP_ASK["groups"] in turn(service, session)
    assert turn(service, session) == st.SETUP_PAUSED


# --- C. the setup out of place ------------------------------------------------------------------------------------

def test_13_a_serious_first_message_comes_first(tmp_path):
    assert "only answer it: the setup waits" in st.SETUP_HEAD
    assert "after you answer what they wrote" in st.SETUP_HEAD


def test_14_never_in_a_group_and_a_group_first_does_not_cancel_it(tmp_path):
    service, _, _, session = fresh(tmp_path)
    service._session = lambda: {**GROUP}
    assert service.turn_hint("telegram", "") == st.TURN_HINT  # a group gets the usual hint only
    service.send({"sticker": "😏"})  # the bot already sent a sticker in a group
    service._session = lambda: dict(session)
    assert turn(service, session).startswith("[telegram-stickers setup]")  # still a fresh install


def test_15_someone_else_in_a_direct_chat_gets_no_setup_and_adds_no_pack(tmp_path):
    service, _, _, session = fresh(tmp_path)
    turn(service, session)  # the owner's setup runs
    other = {**DM, "chat_id": "777", "message_id": "3"}
    service._session = lambda: dict(other)
    assert turn(service, other, sticker="dogs", sender="777") == st.TURN_HINT
    assert service.learned_packs() == []
    service._session = lambda: dict(session)
    assert turn(service, session).startswith("[telegram-stickers setup]")


def test_16_a_restart_continues_where_it_stopped(tmp_path):
    service, _, _, session = fresh(tmp_path)
    turn(service, session)
    service.settings({"action": "pace", "where": "direct", "every": "sometimes"})
    restarted, _, _, session2 = fresh(tmp_path)
    assert st.SETUP_ASK["groups"] in turn(restarted, session2)


def test_17_an_update_of_a_plugin_in_use_starts_no_setup_until_asked(tmp_path):
    (tmp_path / st.STATE_FILE).write_text(json.dumps({"learned": [{"pack": "dogs", "at": 1, "seen": 1}]}))
    service, _, _, session = fresh(tmp_path)
    assert turn(service, session) == st.TURN_HINT
    assert state(tmp_path)["setup"]["status"] == "done"
    assert "setup starts again" in service.command("setup")  # '/stickers setup' in the direct chat
    assert turn(service, session).startswith("[telegram-stickers setup]")
    service._session = lambda: {**GROUP}
    assert "direct chat" in service.command("setup")  # refused in a group


# --- D. stickers during the setup ---------------------------------------------------------------------------------

def test_18_a_pack_already_saved(tmp_path):
    service, _, _, session = fresh(tmp_path)
    through_questions(service, session)
    turn(service, session, sticker="dogs")
    hint = turn(service, session, sticker="dogs")
    assert "dogs, which is already saved: ask for one from another pack" in hint
    (tmp_path / "settings").mkdir()
    service2, _, _, session2 = fresh(tmp_path / "settings", packs=("cats",))
    through_questions(service2, session2)
    assert "cats, which is already saved" in turn(service2, session2, sticker="cats")  # a pack from settings


def test_19_a_forwarded_sticker_adds_no_pack(tmp_path):
    service, _, _, session = fresh(tmp_path)
    through_questions(service, session)
    hint = turn(service, session, forwarded=True)
    assert "forwarded a sticker: forwarded stickers add no pack" in hint
    assert service.learned_packs() == []


def test_19_a_sticker_in_a_group_adds_no_pack(tmp_path):
    service, _, _, _ = fresh(tmp_path)
    service._session = lambda: {**GROUP}
    service.note_sticker(OWNER, "dogs")
    assert service.admit_turn("telegram", OWNER, "") == [] and service.learned_packs() == []


def test_20_a_pack_telegram_no_longer_has(tmp_path):
    service, _, _, session = fresh(tmp_path)
    through_questions(service, session)
    hint = turn(service, session, sticker="ghosts")
    assert "Telegram says that pack does not exist any more" in hint
    assert service.learned_packs() == [] and state(tmp_path)["setup"]["packs"] == []


def test_21_done_without_a_sticker_keeps_the_default_packs(tmp_path):
    service, _, _, session = fresh(tmp_path)
    through_questions(service, session)
    out = service.settings({"action": "setup_finish"})
    assert out["packs"] == [] and out["default_packs"] is True and "show how it looks" not in out["next"]
    assert service.packs() == list(st.DEFAULT_PACKS)


# --- E. changes in words later ------------------------------------------------------------------------------------

def done(tmp_path, **config):
    service, now, cfg, session = fresh(tmp_path, **config)
    turn(service, session)
    service.settings({"action": "setup_skip"})
    return service, now, cfg, session


def test_22_rarer_and_more_often_in_the_direct_chat_and_pacing_follows(tmp_path):
    service, _, _, session = done(tmp_path)
    service.settings({"action": "pace", "every": "rarely"})  # "here" in a direct chat = the direct chat
    assert state(tmp_path)["pace"]["direct"] == 12
    assert service.send({"sticker": "😏"})["message_id"] == 500
    session["message_id"] = "506"
    assert service.turn_hint("telegram", OWNER) == ""  # 6 messages: 3 of theirs, 6 needed
    service.settings({"action": "pace", "every": "often"})
    assert service.turn_hint("telegram", OWNER) == st.TURN_HINT  # 4 needed now
    service.settings({"action": "pace", "every": "10"})
    assert state(tmp_path)["pace"]["direct"] == 20


def test_23_groups_more_often_from_the_direct_chat(tmp_path):
    service, _, _, _ = done(tmp_path)
    out = service.settings({"action": "pace", "where": "groups", "every": "often"})
    assert out["groups"] == "about one sticker per 3 messages"
    service.settings({"action": "pace", "where": "groups", "every": "off"})
    service.settings({"action": "pace", "where": "groups", "every": "on"})
    assert "groups" not in state(tmp_path)["pace"]


def test_24_in_a_group_anyone_may_only_make_it_rarer_or_switch_it_off(tmp_path):
    service, _, _, _ = done(tmp_path, min_messages_between=6)
    service._session = lambda: {**GROUP}
    chat = GROUP["chat_id"]
    out = service.settings({"action": "pace", "every": "rarely"})
    assert out["here"] == "about one sticker per 12 messages" and state(tmp_path)["chats"][chat]["gap"] == 12
    for every in ("often", "sometimes", "3", "on"):
        with pytest.raises(st.StickerError, match="Only the owner"):
            service.settings({"action": "pace", "every": every})
    for where in ("direct", "groups"):
        with pytest.raises(st.StickerError, match="Only the owner"):
            service.settings({"action": "pace", "where": where, "every": "rarely"})
    service.settings({"action": "pace", "every": "off"})
    assert chat in state(tmp_path)["muted"]
    with pytest.raises(st.StickerError, match="off here"):
        service.settings({"action": "pace", "every": "rarely"})


def test_25_only_on_request(tmp_path):
    service, _, _, session = done(tmp_path)
    service.settings({"action": "pace", "every": "on_request"})
    assert turn(service, session) == ""  # no unasked stickers
    assert service.send({"sticker": "😏"})["success"]  # asked for: still works
    assert service.settings({"action": "list"})["direct"] == "only when asked"
    service.settings({"action": "pace", "every": "sometimes"})
    assert "on_request" not in state(tmp_path)["chats"].get(OWNER, {})


def test_26_27_28_dont_send_this_one_the_wrong_one_and_back(tmp_path):
    service, _, _, session = done(tmp_path, packs=("cats",))
    first = service.send({"sticker": "cats:1"})["sticker"]
    second = service.send({"sticker": "cats:2"})["sticker"]
    out = service.settings({"action": "ban"})
    assert out["removed"] == second and "u-cat2" in state(tmp_path)["banned"]
    service.settings({"action": "unban"})  # "not this one, the one before"
    out = service.settings({"action": "ban", "sticker": "previous"})
    assert out["removed"] == first and state(tmp_path)["banned"] == ["u-cat1"]
    assert service.settings({"action": "unban"})["back"] == first  # "bring it back"
    assert state(tmp_path)["banned"] == []
    (tmp_path / "empty").mkdir()
    service2, _, _, _ = done(tmp_path / "empty")
    with pytest.raises(st.StickerError, match="No such sticker went to this chat"):
        service2.settings({"action": "ban"})


def test_29_remove_a_pack_by_its_title(tmp_path):
    service, _, _, session = done(tmp_path)
    for pack in ("HotCherry_by_bot", "dogs"):
        turn(service, session, sticker=pack)
    service.catalog()  # titles come from the catalog file
    out = service.settings({"action": "forget_pack", "pack": "cherry"})
    assert out["removed"] == "HotCherry_by_bot" and out["title"] == "Hot Cherry"
    assert service.learned_packs() == ["dogs"]
    out = service.settings({"action": "forget_pack", "pack": "unicorns"})
    assert out["success"] is False and [p["pack"] for p in out["packs"]] == ["dogs"]


def test_30_remove_the_pack_of_the_sticker_just_sent(tmp_path):
    service, _, _, session = done(tmp_path)
    for pack in ("owls", "dogs"):
        turn(service, session, sticker=pack)
    sent = service.send({"sticker": "owls:1"})["sticker"]
    assert service.settings({"action": "forget_pack", "pack": "this"})["removed"] == sent.split(":")[0]
    assert service.learned_packs() == ["dogs"]


def test_31_remove_all_packs_asks_first(tmp_path):
    service, _, _, session = done(tmp_path)
    turn(service, session, sticker="dogs")
    out = service.settings({"action": "forget_all"})
    assert out["confirm_needed"] is True and [p["pack"] for p in out["packs"]] == ["dogs"]
    assert service.learned_packs() == ["dogs"]
    out = service.settings({"action": "forget_all", "confirm": True})
    assert out["success"] and service.packs() == [] and service.learned_packs() == []
    with pytest.raises(st.StickerError, match="No sticker packs yet"):
        service.send({"sticker": "😏"})
    hint = turn(service, session, sticker="owls")  # a sticker brings a pack back
    assert service.packs() == ["owls"] and hint == st.TURN_HINT


def test_32_default_packs_off_and_on(tmp_path):
    service, _, _, _ = done(tmp_path)
    assert service.settings({"action": "defaults", "value": "off"})["default_packs"] is False
    assert service.packs() == []
    out = service.settings({"action": "defaults", "value": "on"})
    assert out["in_use"] is True and service.packs() == list(st.DEFAULT_PACKS)
    service.settings({"action": "forget_pack", "pack": "Animals"})  # one default pack
    assert service.packs() == [p for p in st.DEFAULT_PACKS if p != "Animals"]


def test_33_the_last_pack_removed_does_not_bring_the_defaults_back(tmp_path):
    service, _, _, session = done(tmp_path)
    turn(service, session, sticker="dogs")
    out = service.settings({"action": "forget_pack", "pack": "dogs"})
    assert "No packs are left" in out["next"] and service.packs() == []


def test_34_a_pack_from_the_settings_stays(tmp_path):
    service, _, _, _ = done(tmp_path, packs=("cats",))
    with pytest.raises(st.StickerError, match="plugin settings on the server"):
        service.settings({"action": "forget_pack", "pack": "cats"})
    assert service.packs() == ["cats"]


def test_35_show_my_packs_and_settings(tmp_path):
    service, _, _, session = done(tmp_path, packs=("cats",))
    turn(service, session, sticker="dogs")
    service.catalog()
    service.settings({"action": "pace", "every": "4"})
    out = service.settings({"action": "list"})
    assert [(p["title"], p["from"]) for p in out["packs"]] == [("Cats", "settings"), ("Dogs", "learned")]
    assert out["direct"] == "about one sticker per 4 of their messages"
    assert out["groups"] == "no message limit, only the cooldown" and out["setup"] == "done"
    assert "show my packs" in out["next"]


def test_36_a_group_cannot_remove_stickers_or_packs_or_see_pack_names(tmp_path):
    service, _, _, session = done(tmp_path)
    turn(service, session, sticker="dogs")
    service._session = lambda: {**GROUP}
    for args in ({"action": "ban"}, {"action": "forget_pack", "pack": "dogs"}, {"action": "forget_all"},
                 {"action": "defaults", "value": "off"}, {"action": "setup_start"}, {"action": "setup_finish"}):
        with pytest.raises(st.StickerError, match="Only the owner"):
            service.settings(args)
    out = service.settings({"action": "list"})
    assert set(out) == {"success", "here", "packs", "note"} and out["packs"] == 1


def test_settings_needs_a_telegram_chat_and_a_known_action(tmp_path):
    service, _, _, _ = done(tmp_path)
    with pytest.raises(st.StickerError, match="Unknown action"):
        service.settings({"action": "explode"})
    service._session = lambda: {"platform": "discord", "chat_id": "1"}
    with pytest.raises(st.StickerError, match="not in a Telegram chat"):
        service.settings({"action": "list"})


def test_setup_off_in_the_settings(tmp_path):
    service, _, _, session = fresh(tmp_path, setup=False)
    assert turn(service, session) == st.TURN_HINT
    with pytest.raises(st.StickerError, match="setup is off"):
        service.settings({"action": "setup_start"})


def test_setup_texts_have_no_long_dashes():
    texts = [st.SETUP_HEAD, st.SETUP_TAIL, st.SETUP_PAUSED, st.OWNER_ONLY, st.CHANGE_IN_WORDS,
             *st.SETUP_ASK.values()]
    for text in texts:
        assert "\u2014" not in text and "\u2013" not in text and " - " not in text


def test_31_remove_all_with_only_settings_packs_has_nothing_to_confirm(tmp_path):
    service, _, _, _ = done(tmp_path, packs=("cats",))  # live test 07.10.2026: it asked about default packs
    out = service.settings({"action": "forget_all"})
    assert out["nothing_to_remove"] is True and "plugin settings" in out["next"] and "confirm" not in out
    assert service.packs() == ["cats"]


def test_pacing_errors_tell_the_agent_not_to_explain_them(tmp_path):
    service, _, cfg, session = done(tmp_path, packs=("cats",), cooldown_seconds=20)
    service.send({"sticker": "😏"})
    with pytest.raises(st.StickerError, match="Do not tell them about pacing"):  # the cooldown
        service.send({"sticker": "😏"})
    cfg.update(cooldown_seconds=0, min_messages_between=6)
    session["message_id"] = "502"
    with pytest.raises(st.StickerError, match="Do not tell them about pacing"):  # the message count
        service.send({"sticker": "😏"})


@pytest.mark.parametrize("text", ["шли тут стикеры почаще", "убери набор Hot Cherry", "no more stickers here",
                                  "remove that pack", "kirim stiker lebih jarang"])
def test_a_message_about_stickers_gets_the_settings_hint(tmp_path, text):
    service, _, _, session = done(tmp_path)
    hint = service.turn_hint("telegram", OWNER, text)
    assert hint == f"{st.SETTINGS_HINT} {st.TURN_HINT}"
    service.set_muted(OWNER, True)  # also where stickers are off: "switch them back on"
    assert service.turn_hint("telegram", OWNER, text) == st.SETTINGS_HINT
    service._session = lambda: {**GROUP}
    assert service.turn_hint("telegram", "", [{"type": "text", "text": text}]).startswith(st.SETTINGS_HINT)


@pytest.mark.parametrize("text", ["привет, как дела?", "package arrived",
                                  '[The user sent a sticker 😀 from "owls"~ It shows: "x"]',
                                  "[Replying to: 'a sticker']\n\nок"])
def test_other_messages_and_hermes_notes_get_no_settings_hint(tmp_path, text):
    service, _, _, _ = done(tmp_path)
    assert service.turn_hint("telegram", OWNER, text) == st.TURN_HINT


def test_no_settings_hint_outside_telegram_or_during_the_setup(tmp_path):
    service, _, _, session = fresh(tmp_path)
    assert service.turn_hint("telegram", OWNER, "стикеры").startswith("[telegram-stickers setup]")
    assert st.SETTINGS_HINT not in service.turn_hint("telegram", OWNER, "стикеры")
    assert service.turn_hint("discord", OWNER, "stickers") == ""


def test_31_confirm_on_the_first_call_still_shows_the_list_first(tmp_path):
    service, now, _, session = done(tmp_path)
    turn(service, session, sticker="dogs")
    out = service.settings({"action": "forget_all", "confirm": True})  # live test 07.10.2026
    assert out["confirm_needed"] is True and service.learned_packs() == ["dogs"]
    now["t"] += st.FORGET_ALL_WINDOW + 1  # a "yes" much later does not count
    assert service.settings({"action": "forget_all", "confirm": True})["confirm_needed"] is True
    assert service.settings({"action": "forget_all", "confirm": True})["success"] is True
    assert service.learned_packs() == []
