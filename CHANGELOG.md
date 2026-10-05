# Changelog

All notable changes to this project are written down here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.2] (2026-10-05)

### Added

- Reactions: a built-in list of about forty common reactions ("facepalm", "eye roll", "thumbs up", "oh no") points each one to the emojis pack authors tag it with and to the words a description of such a sticker tends to use. Asking for a reaction finds a sticker even when no pack has its exact emoji and no description uses that word.
- An emoji that no pack has falls back to the nearest one in feeling (🤦 to 🙄 or 😑). The send result names the emoji that was asked for in `instead_of`.
- `/stickers describe again [n]` redoes descriptions made with an older prompt or taken from Hermes' cache, and `/stickers` shows how many are left. Descriptions you wrote with `/stickers about` are never redone.

### Changed

- Emojis match without skin tone, gender sign or variation selector: 🤦‍♀️, 🤦🏽 and 🤦 find the same stickers.
- A word made of a described word and more, such as "facepalm" for a description with "face", counts as a partial match.
- When nothing fits, the error and the `telegram_sticker_find` hint list the emojis the packs have, and the tool descriptions tell the agent to send the closest one.
- The vision prompt asks what the sticker shows, the feeling and three to six reaction words, and passes the pack's emoji as a hint. The plugin now uses its own prompt rather than Hermes' sticker prompt.
- When the best match was just sent in the chat, the next sticker that fits at least half as well goes out instead of a repeat.
- Questions and ideas go to GitHub Discussions, linked from the issue form and CONTRIBUTING. Tests also run on macOS.

### Fixed

- In a live test the agent asked for "facepalm", then 🙃, then 🤦‍♀️, got nothing back each time and sent no sticker. All three find one in the same packs now.

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

[Unreleased]: https://github.com/churnast/hermes-telegram-stickers/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/churnast/hermes-telegram-stickers/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/churnast/hermes-telegram-stickers/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/churnast/hermes-telegram-stickers/releases/tag/v0.1.0
