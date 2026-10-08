# Changelog

All notable changes to this project are written down here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] (2026-10-08)

### Added

- Setup in the owner's direct chat: on a fresh install, the first direct-chat turn starts it. The `pre_llm_call` hook adds a setup paragraph to that person's turns: answer their message first (a serious one gets only an answer), then ask one question per message: how often in this chat (rarely, sometimes, often or a number of their messages), how often in groups (rarely, the same, off or a number), and a sticker from a pack they like, then a couple more. A sticker sent then names its pack with title and size (fetched in a background thread), says when the pack was already saved, forwarded or gone from Telegram. Done sums up packs, both paces and how to change them in words; skip keeps the defaults. Three turns with no answer pause it; "set up stickers" or `/stickers setup` starts it again. Only the person who started it gets it, and while it runs, stickers from other people add no packs. An update of a plugin already in use (learned packs, chats, mutes or bans in `state.json`) does not start it. `setup: false` turns it off.
- `telegram_sticker_settings`: settings in words. `pace` sets how often in this chat, in all direct chats or in all groups (`rarely`, `sometimes`, `often`, a number, `off`, `on`, `on_request`, `same`); in a group anyone may only make it rarer, `off` or `on_request` there. `ban` and `unban` take the last or previous sticker sent in this chat ("don't send this one", "not that one, the one before", "bring it back"). `forget_pack` removes a pack by short name, title words or `this` (the pack of the last sticker); `forget_all` asks first; `defaults` turns the default packs off or on; `list` shows packs and paces. Everything except `pace` and `list` is for the owner in a direct chat; in a group, `list` shows no pack names.
- `/stickers setup`, and a line with the paces set in words in `/stickers`.
- No tool progress line before a sticker. With tool progress on (the config Hermes' installer writes up to 0.21.5 sets `display.tool_progress: all`, which overrides Telegram's default `off`), Hermes posted a line such as "🐾 telegram_sticker_send..." in the chat before every sticker. The plugin now registers a `tool_execution` middleware (declared as `provides_middleware: [tool_execution]` in `plugin.yaml`). When the agent calls one of the plugin's tools in its tool loop on a Telegram turn, the middleware runs the tool itself and does not hand the call on, so Hermes never reaches the step that posts the line. Before the tool runs, it checks the bot token (as the tools' `check_fn` did) and calls Hermes' own `pre_tool_call` step, so plugin and shell hooks still block, ask for approval or change the arguments, and Hermes' argument coercion, so a `"false"` from the model is still `false`; a block returns `{"error": ...}` as Hermes does, and a failing handler returns the tools' usual `{"error": ...}`. That covers every tool `register()` registers, also when the agent calls them through `tool_call`, which Hermes unwraps before the middleware. Every other call goes on to Hermes unchanged: other tools, the sticker tools on other turns and outside the agent's tool loop, and any call where deciding fails or this Hermes lacks those two functions. For the sticker calls it runs, the tool-loop guardrail's check before a call, `transform_tool_result` hooks, `tool_execution` middleware of plugins that run after this one and Hermes' tool start step (the progress line, and the `tool_calls.log` line under `tool_progress: log`; on Hermes main also its check for compression markers in arguments) do not run; `post_tool_call` hooks still do. This uses only Hermes' public middleware surface, so `hermes plugins validate` passes on Hermes main, `no core override` included. Not with `plugins.isolation: host`, where Hermes hands the middleware its next step as a placeholder it cannot call (the plugin does not register it there), or with the Codex app-server runtime, which posts the line before any middleware. New setting `hide_tool_progress` (default `true`; `false` runs the sticker tools through Hermes' usual path, with the line). The README explains the lines, what the middleware skips, the lines it leaves to Hermes (a `tool_describe` or `tool_search` lookup, and a `tool_call` Hermes refuses, which on Hermes 0.21.5 and older includes two sticker tools in one call) and how to hide every tool's lines in Telegram (`display.platforms.telegram.tool_progress: "off"`).
- CI checks on each Hermes version it runs (0.20.6, 0.21.5 and main) that Hermes' loader registers the middleware; that Hermes' own tool loop step then runs the sticker tools on a Telegram turn without the progress event, after Hermes' `pre_tool_call` hooks (a probe plugin's block and modify answers included) and argument coercion, while another tool, a sticker tool on a CLI turn and `model_tools.handle_function_call` go through Hermes; and that `agent/tool_executor.py` still posts the progress event only from the step at the end of the chain (`.github/scripts/check_tool_progress.py`).

### Changed

- Pacing: a pace set in words comes before `min_messages_between`, per chat, then for direct chats or groups. "Only on request" turns the sticker hint off in that chat.
- Removing your last pack in words keeps the default packs off instead of bringing them back; "bring back the default packs" turns them on.
- The sticker hint gives the whole call, `telegram_sticker_send` with `{"sticker": ...}`, and says to pass it to `tool_call` directly when the tool is not listed, without `tool_search` or `tool_describe` first: each of those lookups posts its own progress line in the chat. The `sticker-etiquette` skill says the same. The hint is about 540 characters, was about 450.

## [1.0.0] (2026-10-07)

### Added

- Learned packs: a sticker the owner sends the bot in a direct chat adds its pack, so "send the bot a few stickers you like" is the whole setup. Static, animated and video stickers count; groups never do, and forwarded stickers do not either, except on Hermes 0.20.6, which has no Telegram handlers for plugins (there, only static stickers count). A pack is kept only once Hermes runs a turn for that sender in their own direct chat. There is no limit on learned packs: each is kept, with its sticker kind and when it was learned and last seen, until `/stickers forget` or until Telegram no longer has it (it is then forgotten on the next sync). Set names match without case, so `cats` and `Cats` are one pack. `learn_packs: false` turns this off.
- Default packs: with no packs of your own, four packs made by Telegram (TheFoods, MelieTheCavy, OfficeTurkey, Animals) work out of the box. `default_packs: false` turns them off.
- `/stickers forget <pack>` (owner only: a direct chat, or the Hermes CLI) removes a learned pack.
- A sticker hint on Telegram turns: where a sticker is allowed now (stickers on in the chat, pacing passed; in a direct chat, a turn with a sender; in a group none is needed, since Hermes drops the sender where it observes every message), the `pre_llm_call` hook returns one fixed paragraph that Hermes appends to that turn's user message. It says a sticker may answer a joke, banter, good or funny news or a strong emotion, how to call `telegram_sticker_send` (through `tool_call` when tool search hides it), when not to, and to still write one short line after it. It never calls the network; `turn_hint: false` turns it off.
- An emoji no sticker is tagged with, and with no nearest emoji in feeling, is looked up by the words of its Unicode name in pack titles, short names and descriptions: 🍒 (cherries) finds a pack called Hot Cherry, 🍕 (slice of pizza) one called Pizza Party. Words such as "face", "sign" or "slice" do not count, and only whole words match, so ROCKET does not find "rocking". `telegram_sticker_find` says so in its hint, and the send result names the emoji asked for in `instead_of`.
- CI checks that Hermes' note for a static sticker still names its pack the way the plugin reads it.
- A card for the Hermes plugin catalog, `docs/card.png` (1200x600): one message and a kitten sticker as the reply. `docs/banner.png` stays the README banner.
- A 15-second demo in the README (animated WebP, plus the same clip as `docs/demo.mp4`), re-drawn from live tests: three scenes, each in a fresh chat. Happy news gets a sticker nobody asked for, a smug cat gets a smug kitten, and the third message gets words.
- Screenshots re-drawn from live test chats in `docs/screenshots` (1280x800, with the agent's name and bot username replaced): 01, 02 and 04 in the README, 03 and 05 for the catalog gallery.
- CI also validates the plugin against hermes-agent `main`, next to the pinned v2026.9.24 (Hermes Agent 0.21.5). A failure on `main` shows up in the run but does not fail the build.

### Changed

- Pacing default: `min_messages_between` is 6, was 8. It counts messages in the chat by Telegram message numbers, the bot's own included, so in a direct chat 6 is about every third message of the person. The README, `plugin.yaml` and `config.example.yaml` now say what is counted.
- With no cap on learned packs, replies that name packs stay short: `/stickers` lists 20 packs and counts the rest, as do its list of muted chats and `/stickers forget`; `telegram_sticker_find` with no arguments lists 20 packs with `total_packs`; the error when no pack loads names five. Every `/stickers` reply fits one Telegram message.
- Supported Hermes: 0.20.6 and newer, was 0.21.5 (`requires_hermes: ">=0.20.6"`). `plugin.yaml` uses `manifest_version: 1`, since the installers of Hermes 0.21.3 and older refuse version 2, and the first line of `config.example.yaml` no longer names the path of Hermes' config file, which the install scanner of Hermes 0.20.6 blocks. CI also runs `hermes plugins doctor` and the sticker note check on Hermes 0.20.6. On Hermes 0.20.6, which has no Telegram handlers for plugins, only static stickers teach packs.
- Before Hermes 0.21.5, Hermes runs plugin slash commands without the chat they came from, so `/stickers` no longer takes a missing chat type for a direct chat. In a chat on such a Hermes, `/stickers` shows the status (learned packs only as a count, no muted chats) and `/stickers sync` reloads the packs; the commands for the owner (`describe`, `ban`, `unban`, `about`, `forget`, `off` or `on` with a chat id) and `/stickers off` or `on` for the current chat answer that this Hermes does not say which chat a command came from and point to the Hermes CLI on the server, where they work, since the person at the console is the owner. Without this, anyone who may run slash commands in a group could have run the owner's commands there. A command with no chat counts as the owner's only when Hermes' own CLI or TUI command dispatcher called it; anything else is treated like a chat on such a gateway. On that gateway, a command also runs in a worker thread, not on the gateway's event loop. With `plugins.isolation: host` (Hermes main), where the plugin never gets the chat, `/stickers` shows the status and reloads the packs, and the other commands are refused, in Telegram and in the Hermes CLI, with a pointer to `plugins.isolation: in_process`. On Hermes 0.21.5 and newer with `plugins.isolation: in_process`, nothing changes.
- `/stickers describe` takes learned packs first, newest first, then the rest in pack order, so a pack just learned is described on the next run, not after every pack in `settings.packs`. `/stickers` names the learned pack it starts with (in a group, only that it starts with learned packs). Vision calls still run only on the owner's command.
- Packs in use: `settings.packs` first, then learned packs (newest first); the default packs only while both are empty. `/stickers` marks learned and default packs and states this order. In a direct chat it says when learning is off or only static stickers can be learned; in a group it shows learned packs only as a count.
- A changed pack list (a pack learned or forgotten, `settings.packs` edited) loads only the packs that are new or loaded more than a week ago; the rest come from the cached list. `/stickers sync` still loads them all.
- With no packs at all (`default_packs: false`), the error says "No sticker packs yet" and says how to add packs: by name in `settings.packs`, or, while `learn_packs` is on, by sending the bot a few stickers.
- `plugin.yaml` declares the `pre_llm_call` hook and the `learn_packs`, `default_packs` and `turn_hint` settings.
- The README is rewritten around a Quick start (install and restart, send the bot a few stickers or set packs by name, `/stickers` to check), keeps the /stickers commands in one table, and explains stickers nobody asked for (the plugin's own hint on Telegram turns, with no channel prompt needed), who can run what, and Known limitations.
- The README banner drops its pills and uses larger text so it reads in small link previews.
- The `telegram_sticker_mute` description, its result and the error in a muted chat no longer say that only the owner can switch stickers back on: `/stickers on` in that chat does too (before Hermes 0.21.5, `/stickers on <chat id>` in the Hermes CLI, which they also name).
- CI runs `hermes plugins validate . --install-deps`, the same check the plugin catalog runs, and clones Hermes with its tags so `main` can tell its own version.
- `sticker-etiquette` skill: the agent may send a sticker nobody asked for where it would otherwise answer with a single emoji, ends with one short line after a sticker, and answers in words, not silence, when stickers are paced.
- The `plugin.yaml` description no longer says the packs must be the owner's own: any public pack works. `0` for `min_messages_between` turns off only that limit, not the cooldown.
- The code of conduct points questions to Discussions, and conduct reports go through GitHub's Report content.
- `config.example.yaml` shows `plugins.enabled` next to `entries` and says not to paste a second `plugins:` key: pasted below the one `hermes plugins install --enable` writes, it dropped the enabled list.

### Fixed

- On Windows, saving `state.json` while another process (the CLI next to the gateway) reads or saves it no longer fails with "Access is denied": the read or replace is retried for up to about two seconds.
- Words also match a pack's title and short name, plurals included, with less weight than a sticker's own description: "cherry" or "cherries" now finds the stickers of an undescribed pack called Hot Cherry (HotCherry) instead of nothing. A `_by_<bot>` ending and words like "pack" or "stickers" do not count. `telegram_sticker_find` limited to one pack lists that pack's stickers with a hint when nothing in it fits, and says when no loaded pack has that name.
- After a sticker the agent no longer answers with [SILENT]: Hermes 0.21.5 turns that into an error notice in the chat, so the tool result now asks for one short line that carries the conversation on.
- When no pack could be loaded (for example, Telegram rate limits a `/stickers sync`), the `/stickers` reply in a group, or in a chat on Hermes before 0.21.5, no longer names learned packs: it counts them, as the rest of that reply does.
- In a chat where stickers are off, the error no longer says that the owner switched them off: often someone in the chat asked the agent to.
- Joined emojis, such as a woman's facepalm, are written as escapes in the code and tests: Hermes' install-time scanner blocks a community plugin with a zero-width joiner in its files.
- Two processes saving the same file at once (for example the CLI and the gateway) no longer make one save fail: each save writes a temporary file of its own.
- A hand-edited `state.json` with a value of the wrong type no longer breaks every tool and `/stickers`: that value counts as empty.

### Security

- Learning packs uses a native Telegram handler for stickers in direct chats (pack name, kind and sender id, in memory for up to an hour) and a `pre_llm_call` hook, which reads the turn's platform and sender id for learning and, where a sticker is allowed, appends one fixed paragraph to that turn's user message (kept with it in the session history; `turn_hint: false` turns it off). Senders Hermes does not answer, groups, forwarded stickers (where the handler runs) and turns with an unknown chat type teach nothing. Without the handler, only a note that starts the message or a paragraph counts, so a note inside a sticker description is not taken, and a message sent as a reply teaches nothing, since the quote can hold a note of its own. The README section "Privacy and safety" describes both. Every person Hermes answers in a direct chat can add packs: the README says so next to the setting and in Known limitations.
- With an `api_base` that does not start with `https://` or `http://`, a failed call could put the bot token into a tool error or the `/stickers` reply. Such an `api_base` is now refused before any request, a malformed address or an answer that is not JSON gives an error without the URL, and the token is removed from tool errors and `/stickers` replies before they leave the plugin.
- Pack titles are written by whoever made the pack, and most sticker descriptions come from a vision model looking at those pictures. `telegram_sticker_find` and `telegram_sticker_send` now add a `data_note` next to this text asking the agent to treat it as data, not as instructions, and the `telegram_sticker_find` description says the same. The text itself is passed on unchanged.

## [0.1.2] (2026-10-05)

### Added

- Reactions: a built-in list of about forty common reactions ("facepalm", "eye roll", "thumbs up", "oh no") points each one to the emojis pack authors tag it with and to the words a description of such a sticker tends to use. Asking for a reaction finds a sticker even when no pack has its exact emoji and no description uses that word.
- An emoji that no pack has falls back to the nearest one in feeling (🤦 to 🙄 or 😑). The send result names the emoji that was asked for in `instead_of`.
- `/stickers describe again [n]` redoes descriptions made with an older prompt or taken from Hermes' cache, and `/stickers` shows how many are left. Descriptions you wrote with `/stickers about` are never redone.

### Changed

- Emojis match without skin tone, gender sign or variation selector: 🤦🏽 and 🤦, or a woman's and a man's facepalm, find the same stickers.
- A word made of a described word and more, such as "facepalm" for a description with "face", counts as a partial match.
- When nothing fits, the error and the `telegram_sticker_find` hint list the emojis the packs have, and the tool descriptions tell the agent to send the closest one.
- The vision prompt asks what the sticker shows, the feeling and three to six reaction words, and passes the pack's emoji as a hint. The plugin now uses its own prompt rather than Hermes' sticker prompt.
- When the best match was just sent in the chat, the next sticker that fits at least half as well goes out instead of a repeat.
- Questions and ideas go to GitHub Discussions, linked from the issue form and CONTRIBUTING. Tests also run on macOS and Windows.

### Fixed

- In a live test the agent asked for "facepalm", then 🙃, then a woman's facepalm emoji, got nothing back each time and sent no sticker. All three find one in the same packs now.

## [0.1.1] (2026-10-05)

### Added

- `/stickers on <chat id>` and `/stickers off <chat id>` switch stickers in any chat by its id. They work only in a direct chat with the bot, because anyone in a group may be able to run slash commands.
- `/stickers` in a direct chat lists the chats where stickers are off, with their ids.

### Changed

- The mute confirmation, the error in a muted chat and the README point to `/stickers on <chat id>` in a direct chat.
- After a sticker is sent, the tool result says plainly that the sticker is the whole reply: no "done" or "sent" line after it.
- `sticker-etiquette` skill: where Hermes keeps plugin tools behind `tool_search` / `tool_describe` / `tool_call`, call the sticker tools through `tool_call` directly.

### Fixed

- A group muted by the agent could only be switched back on by editing `state.json` when commands typed in that group did not reach the bot (for example in a group that needs a mention).

## [0.1.0] (2026-10-05)

First public release.

### Added

- `telegram_sticker_send`: sends one sticker from the owner's packs by a few words, an emoji or an exact id, as a reply to the message being answered and in the same forum topic.
- `telegram_sticker_find`: lists packs and emojis, finds stickers by words or emoji, with a short description of each.
- `telegram_sticker_mute`: lets the agent switch stickers off in a chat when someone asks.
- `/stickers` command: status, `sync`, `describe [n]`, `off` and `on`, `ban` and `unban`, `about`.
- Picking by meaning: reuses Hermes' own sticker descriptions and describes the rest with the configured vision model on request, animated and video stickers through their still preview.
- Pacing: about one sticker per 8 messages and a 20 second cooldown per chat, kept across restarts; the last five stickers sent in a chat are not repeated.
- Bundled `sticker-etiquette` skill.

[Unreleased]: https://github.com/churnast/hermes-telegram-stickers/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/churnast/hermes-telegram-stickers/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/churnast/hermes-telegram-stickers/releases/tag/v1.0.0
[0.1.2]: https://github.com/churnast/hermes-telegram-stickers/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/churnast/hermes-telegram-stickers/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/churnast/hermes-telegram-stickers/releases/tag/v0.1.0
